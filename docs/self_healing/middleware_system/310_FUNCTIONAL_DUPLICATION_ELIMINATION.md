# 310. Functional Duplication Elimination — Retry/Recovery/Alert 중복 통합

> **Status**: Refactor
> **Severity**: P1 (HIGH)
> **Target**:
> - `packages/selfhealing-python/src/selfhealing/services/retry_handler/` — RetryHandler
> - `packages/selfhealing-python/src/selfhealing/services/replay_service/` — ReplayService
> - `packages/selfhealing-python/src/selfhealing/services/coordination/recovery_coordinator/` — RecoveryCoordinator
> - `packages/selfhealing-python/src/selfhealing/interfaces/task_queue.py` — TaskQueueInterface retry 옵션
> - `packages/selfhealing-python/src/selfhealing/interfaces/alert_adapter.py` — AlertAdapter
> - `packages/selfhealing-python/src/selfhealing/interfaces/notification.py` — NotificationAdapter
> **References**:
> - 309 — 아키텍처 패턴 통일
> - 311 — 인터페이스 계약 정합성

---

## 1. 현황 및 문제

시스템 성장 과정에서 **동일 목적의 로직이 여러 모듈에 독립적으로 구현**되었다.
이는 버그 수정 시 모든 구현체를 동시에 수정해야 하는 N-way 동기화 문제를 유발한다.

---

## 2. 중복 분석

### 2.1 Retry 로직 4중 구현 (P0)

동일한 "실패한 작업을 재시도" 로직이 4곳에 분산되어 있다.

| 구현체 | 위치 | API | Backoff | DLQ 연계 |
|--------|------|-----|---------|----------|
| `RetryHandler` | `services/retry_handler/handler.py` | `execute(func, *args)` → `RetryResult` | Exponential (configurable) | O (실패 시 DLQ 적재) |
| `ReplayService` | `services/replay_service/service.py` | `replay_single(id)` → `ReplayResult` | retry_count 기반 | O (DLQ에서 읽어 재실행) |
| `RecoveryCoordinator` | `services/coordination/recovery_coordinator/` | `_execute_with_timeout()` → `CompensationResult` | Step-level timeout | X |
| `TaskQueueInterface` | `interfaces/task_queue.py:133-174` | `enqueue(task, options)` | `retry_backoff`, `autoretry_for` | X |

**공통 패턴** (모든 구현에 존재):

```python
# 4곳 모두 아래 핵심 루프를 변형 구현
for attempt in range(max_retries):
    try:
        result = execute(func, *args)
        return Success(result)
    except target_exceptions as e:
        wait = backoff_strategy(attempt)
        sleep(wait)
return Failure(last_exception)
```

**문제**:
- backoff 계산 로직이 4곳에 각각 구현됨 (일부는 jitter 있고 일부는 없음)
- max_retries 기본값이 구현체마다 다름 (2, 3, 5)
- retry 메트릭 emit 방식이 다름 (일부는 Prometheus, 일부는 structlog만)

---

### 2.2 Recovery 로직 4곳 분산 (P1)

"장애 복구" 로직이 여러 서비스에 독립적으로 존재한다.

| 구현체 | 책임 | 연계 |
|--------|------|------|
| `RecoveryCoordinator` | Multi-step recovery with distributed locking, compensation | Saga 패턴 |
| `CircuitBreakerService.force_close(trigger_replay=True)` | CB 복구 시 DLQ replay 트리거 | ReplayService 호출 |
| `ReplayService` | DLQ 항목 단건/벌크 재실행 | FailedOperationRepository |
| `HealthCheckService` | 암묵적 recovery 상태 관리 | CB 상태 확인 |

**문제**:
- Recovery 전략 간 조율(coordination) 메커니즘 부재
- CB가 force_close되면서 ReplayService를 트리거할 때, RecoveryCoordinator는 관여하지 않음
- 동시에 여러 Recovery 경로가 활성화되면 충돌 가능

---

### 2.3 Alert vs Notification 기능 중복 (P2)

| 구분 | AlertAdapter | NotificationAdapter |
|------|-------------|---------------------|
| **인터페이스 방식** | ABC | Protocol (runtime_checkable) |
| **핵심 메서드** | `send(alert: Alert)` → `None` | `send(notification: Notification)` → `bool` |
| **반환 타입** | None (fire-and-forget) | bool (성공 여부) |
| **배치 지원** | X | O (`send_batch`) |
| **해소(resolve)** | O (`resolve(alert_key)`) | X |
| **구현체 수** | 4 (Stdout, Null, File, +1) | 2 (Stdout, Logging) |
| **레지스트리** | 미등록 (ProviderRegistry 미연동) | ProviderRegistry |

**공통점**: 둘 다 "외부 채널(Slack, PagerDuty, Email 등)로 메시지를 보내는" 단일 책임.

**차이점**: AlertAdapter는 경보(alert) 해소(resolve) 기능이 있고, NotificationAdapter는 배치 전송이 있다.

**추가 문제**: AlertAdapter는 ProviderRegistry에 등록/조회 메커니즘이 없다.
6곳(`chaos/blast_radius.py`, `chaos/safety_guard/guard.py`, `chaos/reports.py`, `error_budget/enums.py`, `celery/tasks/monitoring.py`)에서 `get_alert_adapter()`를 호출하지만, 이 함수가 존재하지 않아 런타임 `ImportError` 위험이 있다 (현재 `try/except`로 Fail-Open 처리).

---

## 3. 개선 계획

### 3.1 Phase 1: Retry Core 추출 (P0)

**목표**: 4곳의 retry 루프를 하나의 Core retry primitive로 통합

#### 3.1.1 Core Retry Primitive 설계

> **설계 원칙**: Sync 전용. 현재 4개 구현체(RetryHandler, ReplayService, RecoveryCoordinator, TaskQueueInterface) 모두 동기 방식이므로, async를 혼합하지 않는다 (PEP 20: Explicit is better than implicit). 향후 비동기 필요 시 `core/async_retry.py`에 `async_retry_with_backoff()`를 별도로 구현한다.

```python
# core/retry.py (기존 core/backoff.py 확장)
from selfhealing.core.backoff import ExponentialBackoff, BackoffStrategy

@dataclass
class RetryContext:
    """
    on_retry/on_exhausted 콜백에 전달되는 컨텍스트.

    콜백 시그니처를 깨지 않고 메트릭에 필요한 데이터를 확장 가능.
    Prometheus 라벨, Audit 로그, OTel trace 연계에 사용.
    """
    func_name: str
    attempt: int
    max_retries: int
    wait_time: float
    elapsed_total: float
    metric_labels: dict[str, str] = field(default_factory=dict)
    trace_id: str | None = None  # OTel span context에서 자동 주입 가능

@dataclass
class RetryConfig:
    max_retries: int = 3
    backoff: BackoffStrategy = field(default_factory=ExponentialBackoff)
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,)
    context_name: str = ""  # 메트릭 라벨링용 (예: "retry_handler", "replay_service")
    on_retry: Callable[[RetryContext, Exception], None] | None = None
    on_exhausted: Callable[[RetryContext, Exception], None] | None = None

@dataclass
class RetryOutcome(Generic[T]):
    success: bool
    result: T | None = None
    exception: Exception | None = None
    attempts: int = 0
    total_wait_seconds: float = 0.0

def retry_with_backoff(
    func: Callable[..., T],
    config: RetryConfig,
    *args: Any,
    **kwargs: Any,
) -> RetryOutcome[T]:
    """
    단일 retry primitive. 모든 retry 로직의 기반. Sync 전용.

    - RetryHandler: 이 함수를 래핑하여 DLQ 적재 추가
    - ReplayService: config.on_exhausted에 DLQ 상태 업데이트 바인딩
    - RecoveryCoordinator: step 단위로 이 함수 호출
    - TaskQueue: 어댑터 레벨에서 위임 (Celery 자체 retry 우선, 필요 시 fallback)
    """
    last_exception = None
    total_wait = 0.0
    func_name = config.context_name or getattr(func, "__name__", "unknown")

    # OTel trace_id 자동 주입 (선택적, Fail-Open)
    trace_id = None
    try:
        from selfhealing.observability import get_current_trace_id_from_otel
        trace_id = get_current_trace_id_from_otel()
    except Exception:
        pass

    for attempt in range(config.max_retries):
        try:
            result = func(*args, **kwargs)
            return RetryOutcome(success=True, result=result, attempts=attempt + 1)
        except config.retryable_exceptions as e:
            last_exception = e
            wait = 0.0
            if attempt < config.max_retries - 1:
                wait = config.backoff.calculate(attempt)
                total_wait += wait

            ctx = RetryContext(
                func_name=func_name,
                attempt=attempt,
                max_retries=config.max_retries,
                wait_time=wait,
                elapsed_total=total_wait,
                metric_labels={"context": config.context_name},
                trace_id=trace_id,
            )

            logger.info(
                "retry_attempt",
                func=func_name,
                attempt=attempt + 1,
                max_retries=config.max_retries,
                wait=wait,
                trace_id=trace_id,
            )

            if config.on_retry:
                config.on_retry(ctx, e)

            if attempt < config.max_retries - 1:
                time.sleep(wait)
        except Exception as e:
            # Non-retryable exception: fail immediately
            return RetryOutcome(
                success=False,
                exception=e,
                attempts=attempt + 1,
                total_wait_seconds=total_wait,
            )

    exhausted_ctx = RetryContext(
        func_name=func_name,
        attempt=config.max_retries - 1,
        max_retries=config.max_retries,
        wait_time=0.0,
        elapsed_total=total_wait,
        metric_labels={"context": config.context_name},
        trace_id=trace_id,
    )

    if config.on_exhausted and last_exception:
        config.on_exhausted(exhausted_ctx, last_exception)

    return RetryOutcome(
        success=False,
        exception=last_exception,
        attempts=config.max_retries,
        total_wait_seconds=total_wait,
    )
```

#### 3.1.2 Standard Hook Factory

메트릭/로깅 파편화를 해소하기 위해, Core primitive 외부에 **표준 Hook 팩토리**를 제공한다.
Core(`core/retry.py`)는 structlog 기본 로깅만 담당하고, Prometheus/Audit 등 도메인별 메트릭은 Hook에 위임하여 `core/` → `metrics/` 순환 의존을 방지한다.

```python
# core/retry_hooks.py — 선택적 사용, core/retry.py에 하드 의존성 없음
from selfhealing.core.retry import RetryContext

def make_standard_on_retry(audit_domain: str) -> Callable[[RetryContext, Exception], None]:
    """AuditHook + MetricsHook을 결합한 표준 on_retry 팩토리."""
    def _on_retry(ctx: RetryContext, exc: Exception) -> None:
        # 1. Audit 로깅 (Fail-Open)
        try:
            from selfhealing.services.audit.retry_audit import log_retry_audit
            log_retry_audit(
                domain=audit_domain,
                attempt=ctx.attempt,
                max_attempts=ctx.max_retries,
                success=False,
                wait_time=ctx.wait_time,
            )
        except Exception:
            pass

        # 2. Prometheus 메트릭 (Fail-Open)
        try:
            from selfhealing.services.metrics.definitions import (
                retry_attempts_histogram,
            )
            retry_attempts_histogram.labels(
                domain=audit_domain,
                **ctx.metric_labels,
            ).observe(ctx.attempt + 1)
        except Exception:
            pass
    return _on_retry

def make_standard_on_exhausted(audit_domain: str) -> Callable[[RetryContext, Exception], None]:
    """최종 실패 시 Audit + 메트릭 기록 표준 팩토리."""
    def _on_exhausted(ctx: RetryContext, exc: Exception) -> None:
        try:
            from selfhealing.services.audit.retry_audit import log_retry_audit
            log_retry_audit(
                domain=audit_domain,
                attempt=ctx.attempt,
                max_attempts=ctx.max_retries,
                success=False,
                error_type=type(exc).__name__,
                error_message=str(exc)[:500],
            )
        except Exception:
            pass
    return _on_exhausted
```

**사용 예시**:

```python
# RetryHandler에서의 사용
from selfhealing.core.retry import retry_with_backoff, RetryConfig
from selfhealing.core.retry_hooks import make_standard_on_retry, make_standard_on_exhausted

config = RetryConfig(
    max_retries=5,
    context_name="retry_handler",
    on_retry=make_standard_on_retry("payment"),
    on_exhausted=make_standard_on_exhausted("payment"),
)
outcome = retry_with_backoff(func, config, *args)
```

#### 3.1.3 기존 서비스 마이그레이션

| 서비스 | 변경 |
|--------|------|
| `RetryHandler` | 내부 retry 루프를 `retry_with_backoff()` 호출로 교체, DLQ 적재는 `on_exhausted` 콜백으로 |
| `ReplayService` | `replay_single()` 내부에서 `retry_with_backoff()` 사용 |
| `RecoveryCoordinator` | step 실행 시 `retry_with_backoff()` 위임, timeout은 기존 로직 유지 |
| `TaskQueueInterface` | 인터페이스 변경 불요. **어댑터 구현체**(Celery/RQ)에서 enqueue 실패 시 `retry_with_backoff()`로 래핑 |

**TaskQueue 어댑터 enqueue fallback 가이드라인**:

큐 자체 장애(enqueue 실패)에 대한 application-level retry는 어댑터 구현체에서 처리한다.
`retryable_exceptions`를 세밀하게 지정하여 일시적 오류만 재시도하고 영구적 오류는 Fast-fail한다.

```python
# 어댑터 구현 예시 (adapters/celery_adapter.py)
class CeleryTaskQueueAdapter:
    def enqueue(self, task, options):
        return retry_with_backoff(
            self._celery_app.send_task,
            RetryConfig(
                max_retries=3,
                context_name="celery_enqueue",
                retryable_exceptions=(
                    ConnectionError,        # 네트워크 일시 장애
                    redis.TimeoutError,     # Redis 타임아웃
                    kombu.exceptions.OperationalError,  # 브로커 일시 장애
                ),
                # 영구 에러(인증, 페이로드 초과 등)는 retryable에 포함하지 않음 → 즉시 Fail
                on_exhausted=lambda ctx, e: self._store_to_dlq(task, e),
            ),
            task.name, task.args,
        )
```

**기존 core/backoff.py와의 관계**:
- `core/backoff.py`는 이미 `ExponentialBackoff`, `BackoffStrategy` 등을 제공
- `retry_with_backoff()`는 backoff 계산을 `BackoffStrategy`에 위임
- 중복이 아니라 **상위 레벨 조합**

---

### 3.2 Phase 2: Recovery 조율 레이어 (P1)

**목표**: 여러 Recovery 경로 간 충돌 방지

#### 3.2.1 RecoveryCoordinator를 중앙 조율자로 승격

현재 RecoveryCoordinator는 multi-step saga만 담당한다.
이를 확장하여 **모든 recovery 경로의 진입점**으로 만든다.

**분산 락**: 기존 `DistributedRecoveryLock`(Redis 기반) + `InMemoryRecoveryLock`(테스트용)을 그대로 재사용한다. CAS(Compare-And-Set) Lua script과 OCC(Optimistic Concurrency Control)로 zombie thread stale write를 방지하는 기존 메커니즘이 충분하다.

```python
# services/coordination/recovery_coordinator/__init__.py 확장

class RecoveryCoordinator:
    def request_recovery(
        self,
        source: str,           # "circuit_breaker", "health_check", "manual"
        target_service: str,
        strategy: str,         # "replay", "force_close", "full_saga"
        context: dict[str, Any] | None = None,
    ) -> RecoveryResult:
        """
        모든 recovery 요청의 단일 진입점.

        1. 동일 target_service에 대한 concurrent recovery 방지 (distributed lock)
        2. 전략에 따라 적절한 handler 디스패치
        3. 결과를 audit trail에 기록
        """
        with self._acquire_recovery_lock(target_service):
            if strategy == "replay":
                return self._delegate_to_replay(target_service, context)
            elif strategy == "force_close":
                return self._delegate_to_circuit_breaker(target_service, context)
            elif strategy == "full_saga":
                return self._execute_saga(target_service, context)
```

#### 3.2.2 동시성 정책: Fast-fail

Lock을 획득하지 못한 경우(다른 Recovery가 진행 중) **즉시 SKIPPED 반환**한다. Blocking wait는 하지 않는다.

```python
def request_recovery(self, source, target_service, strategy, context=None):
    lock_acquired = self._try_acquire_recovery_lock(target_service)
    if not lock_acquired:
        # INFO 레벨: 기대된 방어 동작이므로 WARNING/ERROR가 아님
        # Alert Fatigue 방지를 위해 의도적으로 INFO 사용
        logger.info(
            "recovery_skipped_concurrent_in_progress",
            source=source,
            target_service=target_service,
            strategy=strategy,
        )
        return RecoveryResult(
            status=RecoveryStatus.SKIPPED,
            reason="concurrent_recovery_in_progress",
        )
    # ... lock 획득 성공 시 정상 진행
```

**Fast-fail 선택 근거**:
- Google SRE 원칙: Recovery 도중 추가 Recovery는 상황을 악화시킬 수 있음
- Blocking wait는 thread 점유로 자원 고갈 위험
- Recovery는 idempotent하므로 다음 health check 주기에 안전하게 재시도 가능
- 프로젝트의 기존 Fail-Open 철학과 일관

#### 3.2.3 CircuitBreakerService 변경

```python
# services/circuit_breaker/service.py
def force_close(self, service_name: str, trigger_replay: bool = False, ...):
    # 기존: 직접 ReplayService 호출
    # 변경: RecoveryCoordinator를 통해 요청
    if trigger_replay:
        self._recovery_coordinator.request_recovery(
            source="circuit_breaker",
            target_service=service_name,
            strategy="replay",
        )
```

**주의**: 이 변경은 복잡도가 높으므로 Phase 1의 retry 통합 완료 후 진행.

---

### 3.3 Phase 3: Alert/Notification 통합 결정 (P2)

**목표**: 두 인터페이스의 관계를 명확히 정의 + AlertAdapter ProviderRegistry 연동

#### 3.3.1 분석

완전 통합보다는 **역할 분리 명확화**가 적합하다:

| 역할 | 인터페이스 | 사용 시나리오 |
|------|-----------|-------------|
| **경보 관리** (생성 + 해소) | `AlertAdapter` | Circuit Breaker Open, SLA Breach, Security Incident |
| **단방향 알림** (발송만) | `NotificationAdapter` | Chaos 실험 승인 요청, 일반 정보 전달 |

#### 3.3.2 권장: 역할 분리 유지 + 공통 타입 추출 + 채널 Enum 정적 유지

```python
# interfaces/messaging_common.py (신규)
class MessageSeverity(str, Enum):
    """AlertSeverity와 NotificationSeverity를 통합."""
    CRITICAL = "critical"
    HIGH = "high"
    WARNING = "warning"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

class MessageChannel(str, Enum):
    """
    AlertAdapter와 NotificationAdapter가 공유하는 채널 enum.

    정적 Enum으로 유지한다. 동적 레지스트리가 아닌 이유:
    - 타입 안전성: mypy/IDE 자동완성 지원
    - 설정 검증: Pydantic에서 자동 검증
    - 거버넌스: 명시적 채널 목록 = 감사 추적 가능
    - 확장 빈도: 새 채널 추가는 연 1-2회 수준 (Enum 2줄 + 어댑터 1개로 충분)

    채널 타입은 Enum으로 고정하되, 어댑터 구현체는 ProviderRegistry로 동적 등록.
    """
    SLACK = "slack"
    TEAMS = "teams"
    PAGERDUTY = "pagerduty"
    EMAIL = "email"
    WEBHOOK = "webhook"
    SMS = "sms"
    STDOUT = "stdout"
    FILE = "file"
```

#### 3.3.3 AlertAdapter ProviderRegistry 연동

AlertAdapter가 ProviderRegistry에 미등록된 상태를 해소한다.
NotificationAdapter의 기존 등록 패턴(`factory.py:167-170, 720-753`)을 그대로 따른다.

**1. ProviderRegistry 확장** (`factory.py`):

```python
class ProviderRegistry:
    # 기존 notification 패턴과 동일
    _alerts: dict[str, type | Callable] = {}
    _alert_instances: dict[str, AlertAdapter] = {}
    _default_alert: str = "stdout"

    @classmethod
    def register_alert(cls, name: str, factory: type | Callable) -> None:
        cls._alerts[name] = factory

    @classmethod
    def get_alert(cls, name: str | None = None) -> AlertAdapter:
        """Thread-safe singleton 조회. NotificationAdapter 패턴과 동일."""
        target = name or cls._default_alert
        if target in cls._alert_instances:
            return cls._alert_instances[target]
        with cls._lock:
            if target in cls._alert_instances:
                return cls._alert_instances[target]
            if target not in cls._alerts:
                cls._auto_register_alert_adapters()
            if target not in cls._alerts:
                raise ValueError(
                    f"Unknown alert adapter: {target}. "
                    f"Available: {list(cls._alerts.keys())}"
                )
            instance = cls._alerts[target]()
            cls._alert_instances[target] = instance
            return instance

    @classmethod
    def _auto_register_alert_adapters(cls) -> None:
        from selfhealing.adapters.alert import StdoutAlertAdapter, NullAlertAdapter
        cls.register_alert("stdout", StdoutAlertAdapter)
        cls.register_alert("null", NullAlertAdapter)
```

**2. 편의 함수 추가** (`adapters/alert/__init__.py`):

```python
def get_alert_adapter(name: str | None = None) -> AlertAdapter:
    """
    ProviderRegistry를 통한 AlertAdapter 조회.

    6곳의 기존 호출부와 연결:
    - chaos/blast_radius.py:877
    - chaos/safety_guard/guard.py:817, 842, 935
    - chaos/reports.py:729
    - error_budget/enums.py:162
    """
    from selfhealing.factory import ProviderRegistry
    return ProviderRegistry.get_alert(name)
```

#### 3.3.4 수정 범위

| 파일 | 변경 |
|------|------|
| `interfaces/messaging_common.py` | 신규 생성 (`MessageSeverity`, `MessageChannel`) |
| `interfaces/alert_adapter.py` | `AlertSeverity = MessageSeverity` 별칭으로 통합 (WARNING 포함 6멤버). 기존 코드 하위 호환 유지 |
| `interfaces/notification.py` | `NotificationSeverity` → `MessageSeverity`, `NotificationChannel` → `MessageChannel` import |
| `factory.py` | `register_alert()`, `get_alert()`, `_auto_register_alert_adapters()` 추가 |
| `adapters/alert/__init__.py` | `get_alert_adapter()` 편의 함수 추가 |
| 기존 `NotificationSeverity`, `NotificationChannel` | `MessageSeverity`, `MessageChannel`의 deprecated alias로 유지 (backward-compatible) |
| 기존 `AlertSeverity` | `MessageSeverity`의 backward-compatible alias (`AlertSeverity = MessageSeverity`) |

**타입 통합, 인터페이스 분리 유지**:
- `AlertSeverity`는 `MessageSeverity`로 완전 통합 (WARNING 멤버 포함 6개)
- AlertAdapter의 `resolve()` 메서드는 NotificationAdapter에 없으며, 경보 lifecycle 관리에 필수
- 타입(Enum)은 통합하되, 인터페이스(AlertAdapter vs NotificationAdapter)는 Single Responsibility 유지

---

## 4. 구현 순서

| Phase | 작업 | 파일 수 | 의존성 | 우선순위 |
|-------|------|---------|--------|----------|
| 1 | Retry Core 추출 (`core/retry.py`) | 1 신규 | 없음 | P0 |
| 1.1 | Standard Hook Factory (`core/retry_hooks.py`) | 1 신규 | Phase 1 | P0 |
| 1.2 | RetryHandler 마이그레이션 | 1 수정 | Phase 1, 1.1 | P0 |
| 1.3 | ReplayService 마이그레이션 | 1 수정 | Phase 1, 1.1 | P0 |
| 1.4 | RecoveryCoordinator 마이그레이션 | 1 수정 | Phase 1 | P1 |
| 1.5 | TaskQueue 어댑터 enqueue fallback | 1 수정 | Phase 1 | P1 |
| 2 | Recovery 조율 레이어 (Fast-fail 정책) | 2-3 수정 | Phase 1 완료 | P1 |
| 3.1 | 공통 타입 추출 (`messaging_common.py`) | 1 신규 + 2 수정 | 없음 (독립) | P2 |
| 3.2 | AlertAdapter ProviderRegistry 연동 | 2 수정 | Phase 3.1 | P2 |

---

## 5. 테스트 계획

### 5.1 Core Retry Primitive 테스트

```python
# tests/unit/test_retry_core.py

class TestRetryWithBackoff:
    def test_success_on_first_attempt(self):
        result = retry_with_backoff(lambda: 42, RetryConfig(max_retries=3))
        assert result.success is True
        assert result.result == 42
        assert result.attempts == 1

    def test_success_after_retries(self):
        counter = {"n": 0}
        def flaky():
            counter["n"] += 1
            if counter["n"] < 3:
                raise ConnectionError("fail")
            return "ok"
        result = retry_with_backoff(flaky, RetryConfig(max_retries=5))
        assert result.success is True
        assert result.attempts == 3

    def test_exhausted_calls_on_exhausted(self):
        callback_called = {"v": False}
        def on_exhausted(ctx, e):
            callback_called["v"] = True
            assert isinstance(ctx, RetryContext)
            assert ctx.func_name != ""
        config = RetryConfig(max_retries=1, on_exhausted=on_exhausted)
        result = retry_with_backoff(lambda: 1/0, config)
        assert result.success is False
        assert callback_called["v"] is True

    def test_non_retryable_exception_raises_immediately(self):
        config = RetryConfig(retryable_exceptions=(ConnectionError,))
        result = retry_with_backoff(lambda: 1/0, config)
        # ZeroDivisionError는 retryable이 아니므로 즉시 실패
        assert result.success is False
        assert result.attempts == 1

    def test_retry_context_passed_to_on_retry(self):
        contexts = []
        def capture_ctx(ctx, exc):
            contexts.append(ctx)
        config = RetryConfig(
            max_retries=3,
            context_name="test_ctx",
            on_retry=capture_ctx,
            retryable_exceptions=(ValueError,),
        )
        retry_with_backoff(lambda: (_ for _ in ()).throw(ValueError("x")), config)
        assert len(contexts) == 3
        assert all(c.func_name == "test_ctx" for c in contexts)
        assert contexts[0].attempt == 0
        assert contexts[-1].attempt == 2

    def test_metric_labels_propagated(self):
        captured = {}
        def check_labels(ctx, exc):
            captured.update(ctx.metric_labels)
        config = RetryConfig(
            max_retries=1,
            context_name="label_test",
            on_retry=check_labels,
        )
        retry_with_backoff(lambda: 1/0, config)
        assert captured.get("context") == "label_test"
```

### 5.2 Standard Hook Factory 테스트

```python
# tests/unit/test_retry_hooks.py

class TestStandardHookFactory:
    def test_make_standard_on_retry_does_not_raise(self):
        """Fail-Open: audit/metrics 실패 시에도 예외 전파하지 않음."""
        hook = make_standard_on_retry("test_domain")
        ctx = RetryContext(func_name="test", attempt=0, wait_time=1.0, elapsed_total=1.0)
        hook(ctx, ValueError("test"))  # 예외 없이 완료

    def test_make_standard_on_exhausted_does_not_raise(self):
        hook = make_standard_on_exhausted("test_domain")
        ctx = RetryContext(func_name="test", attempt=2, wait_time=0.0, elapsed_total=3.0)
        hook(ctx, ConnectionError("final"))  # 예외 없이 완료
```

### 5.3 Recovery Fast-fail 테스트

```python
# tests/unit/test_recovery_fast_fail.py

class TestRecoveryFastFail:
    def test_concurrent_recovery_returns_skipped(self):
        """Lock 획득 실패 시 SKIPPED 반환, blocking하지 않음."""
        coordinator = RecoveryCoordinator(...)
        # 첫 번째 recovery 진행 중 시뮬레이션
        with coordinator._acquire_recovery_lock("payment-service"):
            result = coordinator.request_recovery(
                source="circuit_breaker",
                target_service="payment-service",
                strategy="replay",
            )
        assert result.status == RecoveryStatus.SKIPPED
        assert "concurrent" in result.reason
```

### 5.4 AlertAdapter ProviderRegistry 테스트

```python
# tests/unit/test_alert_registry.py

class TestAlertAdapterRegistry:
    def test_get_alert_adapter_returns_default(self):
        adapter = get_alert_adapter()
        assert adapter is not None

    def test_register_and_get_custom_alert_adapter(self):
        ProviderRegistry.register_alert("test", NullAlertAdapter)
        adapter = ProviderRegistry.get_alert("test")
        assert isinstance(adapter, NullAlertAdapter)
```

### 5.5 마이그레이션 회귀 테스트

기존 RetryHandler, ReplayService 테스트가 마이그레이션 후에도 동일하게 통과하는지 확인.
테스트 변경 없이 기존 테스트 통과가 목표.

---

## 6. 위험 및 완화

| 위험 | 영향 | 완화 |
|------|------|------|
| Retry primitive 교체 시 기존 동작 미묘한 차이 | backoff 타이밍, jitter 차이로 테스트 실패 | 기존 backoff 파라미터를 RetryConfig로 1:1 매핑 |
| RecoveryCoordinator 중앙화 시 단일 장애점 | Recovery 자체가 실패 | distributed lock timeout + fallback (직접 호출) |
| Alert/Notification 공통 타입 추출 시 import 변경 | 기존 코드의 `from interfaces.alert_adapter import AlertSeverity` 영향 | deprecated alias 유지 |
| on_retry 콜백 시그니처 변경 (attempt, e) → (RetryContext, e) | 기존 Hook 코드 수정 필요 | RetryContext는 신규이므로 기존 호출부 없음 (breaking change 없음) |
| AlertAdapter ProviderRegistry 연동 시 기존 try/except 호출부 | `get_alert_adapter` 함수 신규 노출로 동작 변경 | 기존 6곳 Fail-Open 패턴 유지, 함수 추가는 하위 호환 |

---

## 7. Multi-tenancy 확장 참고

현재 310의 스코프는 **기능 중복 제거**이므로, Tenant별 RetryConfig/Recovery 전략 차별화는 별도 문서로 분리한다 (YAGNI).

다만, `RetryConfig`가 순수 dataclass로 설계되었으므로 향후 확장 시 팩토리 메서드만 추가하면 된다:

```python
@classmethod
def for_tenant(cls, tenant_id: str, overrides: dict) -> "RetryConfig":
    base = get_tenant_config(tenant_id)
    return cls(**{**base, **overrides})
```

---

## 8. 설계 결정 기록

리뷰를 통해 확정된 설계 결정사항을 기록한다.

| # | 결정 | 근거 |
|---|------|------|
| D1 | Sync 전용 설계, async 별도 모듈 분리 | 4개 구현체 전부 sync. PEP 20: Explicit is better than implicit |
| D2 | Core에 structlog만, Prometheus/Audit는 콜백 위임 | `core/` → `metrics/` 순환 의존 방지, SRP 유지 |
| D3 | RetryContext 도입으로 콜백 시그니처 확장성 확보 | 향후 metric label/trace_id 추가 시 시그니처 변경 불요 |
| D4 | Standard Hook Factory로 메트릭 파편화 해소 | 각 서비스가 `make_standard_on_retry()` 한 줄로 표준화 |
| D5 | TaskQueue enqueue fallback은 어댑터 레벨 처리 | 인프라 레벨 관심사, 인터페이스 변경 불요 |
| D6 | retryable_exceptions 세밀 지정 (영구 에러 Fast-fail) | 일시 오류만 재시도, 인증/페이로드 에러는 즉시 실패 |
| D7 | 기존 Redis 분산 락 + CAS + OCC 재사용 | 검증된 인프라, 신규 구현 불요 |
| D8 | 동시성 정책: Fast-fail + INFO 로그 | SKIPPED는 방어 동작, Alert Fatigue 방지 |
| D9 | Multi-tenancy는 310 스코프 외 (YAGNI) | RetryConfig dataclass 구조로 향후 확장 용이 |
| D10 | MessageChannel은 정적 Enum 유지 | 타입 안전성 + Pydantic 검증 + 거버넌스, 확장 빈도 낮음 |
| D11 | AlertAdapter ProviderRegistry 연동 추가 | 6곳 호출부의 잠재 ImportError 해소 |

---

## 9. 변경 이력

| 날짜 | 버전 | 변경 내용 |
|------|------|----------|
| 2026-03-06 | 1.0.0 | 초안 작성 |
| 2026-03-07 | 1.1.0 | 리뷰 반영: RetryContext 도입, Standard Hook Factory, Fast-fail 정책, AlertAdapter ProviderRegistry 연동, 설계 결정 기록 추가 |
