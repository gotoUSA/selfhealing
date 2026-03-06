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
| **레지스트리** | ProviderRegistry | 자체 모듈 레벨 dict |

**공통점**: 둘 다 "외부 채널(Slack, PagerDuty, Email 등)로 메시지를 보내는" 단일 책임.

**차이점**: AlertAdapter는 경보(alert) 해소(resolve) 기능이 있고, NotificationAdapter는 배치 전송이 있다.

---

## 3. 개선 계획

### 3.1 Phase 1: Retry Core 추출 (P0)

**목표**: 4곳의 retry 루프를 하나의 Core retry primitive로 통합

#### 3.1.1 Core Retry Primitive 설계

```python
# core/retry.py (기존 core/backoff.py 확장)
from selfhealing.core.backoff import ExponentialBackoff, BackoffStrategy

@dataclass
class RetryConfig:
    max_retries: int = 3
    backoff: BackoffStrategy = field(default_factory=ExponentialBackoff)
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,)
    on_retry: Callable[[int, Exception], None] | None = None  # retry 콜백
    on_exhausted: Callable[[Exception], None] | None = None  # 최종 실패 콜백

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
    단일 retry primitive. 모든 retry 로직의 기반.

    - RetryHandler: 이 함수를 래핑하여 DLQ 적재 추가
    - ReplayService: config.on_exhausted에 DLQ 상태 업데이트 바인딩
    - RecoveryCoordinator: step 단위로 이 함수 호출
    - TaskQueue: 어댑터 레벨에서 위임 (Celery 자체 retry 우선, 필요 시 fallback)
    """
    last_exception = None
    total_wait = 0.0

    for attempt in range(config.max_retries):
        try:
            result = func(*args, **kwargs)
            return RetryOutcome(success=True, result=result, attempts=attempt + 1)
        except config.retryable_exceptions as e:
            last_exception = e
            if config.on_retry:
                config.on_retry(attempt, e)
            if attempt < config.max_retries - 1:
                wait = config.backoff.calculate(attempt)
                total_wait += wait
                time.sleep(wait)

    if config.on_exhausted and last_exception:
        config.on_exhausted(last_exception)

    return RetryOutcome(
        success=False,
        exception=last_exception,
        attempts=config.max_retries,
        total_wait_seconds=total_wait,
    )
```

#### 3.1.2 기존 서비스 마이그레이션

| 서비스 | 변경 |
|--------|------|
| `RetryHandler` | 내부 retry 루프를 `retry_with_backoff()` 호출로 교체, DLQ 적재는 `on_exhausted` 콜백으로 |
| `ReplayService` | `replay_single()` 내부에서 `retry_with_backoff()` 사용 |
| `RecoveryCoordinator` | step 실행 시 `retry_with_backoff()` 위임, timeout은 기존 로직 유지 |
| `TaskQueueInterface` | 변경 불요 (Celery/RQ 자체 retry가 우선, 이 primitive는 application-level retry) |

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

#### 3.2.2 CircuitBreakerService 변경

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

**목표**: 두 인터페이스의 관계를 명확히 정의

#### 3.3.1 분석

완전 통합보다는 **역할 분리 명확화**가 적합하다:

| 역할 | 인터페이스 | 사용 시나리오 |
|------|-----------|-------------|
| **경보 관리** (생성 + 해소) | `AlertAdapter` | Circuit Breaker Open, SLA Breach, Security Incident |
| **단방향 알림** (발송만) | `NotificationAdapter` | Chaos 실험 승인 요청, 일반 정보 전달 |

#### 3.3.2 권장: 역할 분리 유지 + 공통 타입 추출

```python
# interfaces/messaging_common.py (신규)
class MessageSeverity(str, Enum):
    """AlertSeverity와 NotificationSeverity를 통합."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

class MessageChannel(str, Enum):
    """AlertAdapter와 NotificationAdapter가 공유하는 채널 enum."""
    SLACK = "slack"
    TEAMS = "teams"
    PAGERDUTY = "pagerduty"
    EMAIL = "email"
    WEBHOOK = "webhook"
    SMS = "sms"
    STDOUT = "stdout"
    FILE = "file"
```

#### 3.3.3 수정 범위

| 파일 | 변경 |
|------|------|
| `interfaces/alert_adapter.py` | `AlertSeverity` → `MessageSeverity` import |
| `interfaces/notification.py` | `NotificationSeverity` → `MessageSeverity`, `NotificationChannel` → `MessageChannel` import |
| `interfaces/messaging_common.py` | 신규 생성 |
| 기존 `AlertSeverity`, `NotificationSeverity` | deprecated alias로 유지 (backward-compatible) |

**통합하지 않는 이유**:
- AlertAdapter의 `resolve()` 메서드는 NotificationAdapter에 없으며, 경보 lifecycle 관리에 필수
- 무리한 통합은 Single Responsibility 위반
- 공통 타입만 추출하면 import 시 혼란 제거 + 향후 통합 가능성 확보

---

## 4. 구현 순서

| Phase | 작업 | 파일 수 | 의존성 | 우선순위 |
|-------|------|---------|--------|----------|
| 1 | Retry Core 추출 (`core/retry.py`) | 1 신규 + 3 수정 | 없음 | P0 |
| 1.1 | RetryHandler 마이그레이션 | 1 | Phase 1 | P0 |
| 1.2 | ReplayService 마이그레이션 | 1 | Phase 1 | P0 |
| 1.3 | RecoveryCoordinator 마이그레이션 | 1 | Phase 1 | P1 |
| 2 | Recovery 조율 레이어 | 2-3 수정 | Phase 1 완료 | P1 |
| 3 | Alert/Notification 공통 타입 추출 | 1 신규 + 2 수정 | 없음 (독립) | P2 |

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
        def on_exhausted(e):
            callback_called["v"] = True
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
```

### 5.2 마이그레이션 회귀 테스트

기존 RetryHandler, ReplayService 테스트가 마이그레이션 후에도 동일하게 통과하는지 확인.
테스트 변경 없이 기존 테스트 통과가 목표.

---

## 6. 위험 및 완화

| 위험 | 영향 | 완화 |
|------|------|------|
| Retry primitive 교체 시 기존 동작 미묘한 차이 | backoff 타이밍, jitter 차이로 테스트 실패 | 기존 backoff 파라미터를 RetryConfig로 1:1 매핑 |
| RecoveryCoordinator 중앙화 시 단일 장애점 | Recovery 자체가 실패 | distributed lock timeout + fallback (직접 호출) |
| Alert/Notification 공통 타입 추출 시 import 변경 | 기존 코드의 `from interfaces.alert_adapter import AlertSeverity` 영향 | deprecated alias 유지 |

---

## 7. 변경 이력

| 날짜 | 버전 | 변경 내용 |
|------|------|----------|
| 2026-03-06 | 1.0.0 | 초안 작성 |
