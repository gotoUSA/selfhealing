# 226. RetryPolicy 전환 설계

## 1. 개요

RetryPolicy는 전환 대상 중 **가장 복잡한 패턴**이다.
현재 `RetryHandler.execute()`에 12건의 하드코딩 크로스-패턴 의존성이 존재하며,
이를 Guard/Hook/Sink 인터페이스로 분리해야 한다.

## 2. 현재 구현 분석

### 2.1 파일 위치

| 파일 | 역할 | 라인 수 |
|------|------|---------|
| `services/retry_handler/handler.py` | 레거시 재시도 로직 (deprecated) | 655줄 |
| `services/retry_handler/models.py` | RetryConfig, RetryPolicyConfig, RetryResult, RetryAction | 233줄 |
| `services/retry_handler/decorators.py` | `@with_retry` 데코레이터 (RetryPolicy 사용) | 65줄 |
| `services/retry_handler/policy.py` | RetryPolicy 순수 재시도 로직 | 224줄 |
| `services/retry_handler/guards.py` | KillSwitchGuard, ErrorBudgetGuard | 101줄 |
| `services/retry_handler/hooks.py` | AuditHook, MetricsHook | ~160줄 |
| `services/retry_handler/sinks.py` | DLQSink | ~100줄 |
| `services/retry_handler/__init__.py` | 패키지 퍼사드 | re-export |

### 2.2 handler.py의 execute() 실행 흐름 (고정 순서)

```
handler.py L432: Kill Switch 체크 ← SystemControlManager lazy import (L30)
    ↓
handler.py L442: ErrorBudgetGate 체크 ← check_automation_allowed lazy import (L186)
    ↓
handler.py L468: while attempt < effective_max_attempts
    │
    ├─ L472: AdaptiveRetryBudget.record_request() ← 직접 생성 (L126)
    ├─ L475: AdaptiveRetryBudget.should_allow_retry()
    ├─ L480: RateLimit 대기 ← get_rate_limit_coordinator lazy import (L138)
    ├─ L483: func(*args, **kwargs) 실행
    │
    │  [성공 시]
    ├─ L487: RateLimitCoordinator.on_success()
    ├─ L492: _log_retry_audit() ← audit_helpers lazy import (L161)
    │
    │  [실패 시]
    ├─ L519: is_rate_limit_error() + _handle_rate_limit_error()
    ├─ L526: get_combined_delay() ← ThrottleAwareBackoffCalculator (L102)
    ├─ L530: Full Stop → break
    ├─ L538: _log_retry_audit() (실패 기록)
    └─ L554: should_retry() 판단

handler.py L567: _move_to_dlq() ← dlq_service.store_to_dlq lazy import (L604)
```

### 2.3 하드코딩 의존성 12건 분류

#### Guard로 분리 (2건)

| # | 의존 대상 | 현재 위치 | 분리 후 |
|---|-----------|-----------|---------|
| 1 | `SystemControlManager.is_enabled()` | handler.py L30, L432 | `KillSwitchGuard.check()` |
| 2 | `check_automation_allowed()` | handler.py L186, L442 | `ErrorBudgetGuard.check()` |

#### Hook으로 분리 (3건)

| # | 의존 대상 | 현재 위치 | 분리 후 |
|---|-----------|-----------|---------|
| 4 | `audit_helpers.log_retry_audit()` | handler.py L161 | `PolicyHook.on_success()` / `.on_failure()` |
| 5 | `retry_critical_tier_grace_retries_total` (Prometheus) | handler.py L346 | `PolicyHook.on_execute()` |
| 6 | `RateLimitCoordinator.on_success()` | handler.py L487 | `PolicyHook.on_success()` 내부 |

#### Sink로 분리 (1건)

| # | 의존 대상 | 현재 위치 | 분리 후 |
|---|-----------|-----------|---------|
| 7 | `dlq_service.store_to_dlq()` | handler.py L604 | `FailureSink.handle_failure()` |

#### 생성자 주입으로 변경 (4건)

| # | 의존 대상 | 현재 위치 | 분리 후 |
|---|-----------|-----------|---------|
| 3 | `AdaptiveRetryBudget.should_allow_retry()` | handler.py L126, L475 | Collaborator로 주입 (`retry_budget: AdaptiveRetryBudget \| None`) |
| 8 | `ThrottleAwareBackoffCalculator` | handler.py L102 | `BackoffStrategy(ABC)` 인터페이스 주입 (기존 `core/backoff.py` L19 재활용) |
| 9 | `AdaptiveRetryBudget` | handler.py L126 | 생성자 파라미터 |
| 10 | `RateLimitCoordinator` | handler.py L138 | 생성자 파라미터 (이미 optional) |

> **#3 재분류 사유**: `AdaptiveRetryBudget`은 `record_request()`, `should_allow_retry()`,
> `adjust_budget_for_throttle_state()` 등 **매 시도(attempt)마다 상태를 변경**하는
> stateful 객체이다 (`budget.py` L49-74). `PolicyGuard.check(context)` 서명은
> stateless 진입 전 1회 체크를 상정하므로 Guard에 부적합하다.
> RetryPolicy의 while 루프 내부에서 직접 호출하는 Collaborator로 재분류한다.

#### 유지 (2건, 경미한 유틸리티 의존)

| # | 의존 대상 | 현재 위치 | 판단 |
|---|-----------|-----------|------|
| 11 | `core.timezone.now` | handler.py L19 | 유틸리티, 커플링 경미. 유지 |
| 12 | `BackoffConfig` (데이터 객체) | handler.py L21 | 데이터 전달용, 유지 |

## 3. 전환 설계

### 3.1 RetryPolicy 클래스

```python
class RetryPolicy(ResiliencePolicy[T]):
    """
    순수 재시도 Policy.

    현재 RetryHandler.execute()에서 하드코딩된 외부 의존성을
    모두 제거하고, 순수한 재시도 로직만 담당한다.

    외부 관심사는 Guard/Hook/Sink로 주입받는다.

    Collaborator 패턴:
    - retry_budget: 루프 내 매 시도마다 상태 변경 (Guard 부적합 → Collaborator)
    - rate_limit_coordinator: wait/signal/cooldown 복합 책임 (생성자 주입)
    - backoff: 기존 core/backoff.py BackoffStrategy(ABC) 재활용
    - sleeper: 테스트 용이성을 위한 대기 함수 주입 (기본 None = Celery 위임)
    """

    def __init__(
        self,
        config: RetryPolicyConfig,
        backoff: BackoffStrategy | None = None,
        rate_limit_coordinator: RateLimitCoordinator | None = None,
        retry_budget: AdaptiveRetryBudget | None = None,
        sleeper: Callable[[float], None] | None = None,
    ):
        self._config = config
        self._backoff = backoff or ExponentialBackoff(
            base_delay=config.backoff_base,
            max_delay=config.backoff_max,
            jitter_factor=config.jitter_percent / 100.0,
        )
        self._rate_limit_coordinator = rate_limit_coordinator
        self._retry_budget = retry_budget
        self._sleeper = sleeper  # None이면 sleep하지 않음 (현재 handler.py L541-544 동작 유지)

    @property
    def name(self) -> str:
        return "retry"

    def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        context: PolicyContext | None = None,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        순수 재시도 실행.

        Kill Switch, ErrorBudgetGate, Audit, DLQ는
        이 메서드 내에 없다. PolicyComposer가 처리한다.
        """
        attempt = 0
        last_error = None
        retry_history: list[dict[str, Any]] = []

        while attempt < self._config.max_attempts:
            attempt += 1

            # Adaptive Retry Budget: 요청 기록 + 예산 확인 (Collaborator)
            if self._retry_budget:
                self._retry_budget.record_request(is_retry=(attempt > 1))
                if attempt > 1 and not self._retry_budget.should_allow_retry():
                    break

            # Rate limit 대기 (선택적)
            if self._rate_limit_coordinator:
                self._rate_limit_coordinator.wait_if_needed(
                    self._config.domain
                )

            try:
                result = func(*args, **kwargs)

                # RateLimitCoordinator 성공 알림
                if self._rate_limit_coordinator:
                    self._rate_limit_coordinator.on_success(self._config.domain)

                return PolicyResult(
                    value=result,
                    outcome=PolicyOutcome.SUCCESS,
                    total_attempts=attempt,
                    executed_policies=["retry"],
                )
            except Exception as e:
                last_error = e
                retry_history.append({
                    "attempt": attempt,
                    "error_type": type(e).__name__,
                    "error_message": str(e)[:500],
                })

                # 429 감지 → RateLimitCoordinator에 쿨다운 요청
                if self._rate_limit_coordinator:
                    self._notify_rate_limit_cooldown(e)

                if not self._should_retry(e, attempt):
                    break

                # Backoff 계산 (context 전달로 tier/부하 정보 활용)
                delay = self._backoff.calculate(attempt, context=context)

                # Sleeper: None이면 delay 값만 기록 (Celery 위임)
                if self._sleeper and delay > 0:
                    self._sleeper(delay)

        return PolicyResult(
            outcome=PolicyOutcome.FAILURE,
            error=last_error,
            total_attempts=attempt,
            executed_policies=["retry"],
            metadata={
                "max_attempts": self._config.max_attempts,
                "domain": self._config.domain,
                "should_dlq": self._config.enable_dlq,
                "retry_history": retry_history,
            },
        )
```

> **설계 변경 요약 (기존 §3.1 대비)**:
> 1. `config` 타입: `RetryConfig` → `RetryPolicyConfig` (순수 설정만 포함)
> 2. `retry_budget` 파라미터 추가 (Collaborator, Guard에서 재분류)
> 3. `sleeper` 파라미터 추가 (기본 None = Celery 위임, 테스트 시 mock 가능)
> 4. `backoff.calculate(attempt, context=context)` — PolicyContext 전달
> 5. `metadata["should_dlq"]` 플래그 — FailureSink가 판단 없이 저장만 수행
> 6. `execute()` 서명에 `context: PolicyContext | None = None` 추가 (225 Protocol 준수)

### 3.2 현재 RetryHandler와의 공존

기존 `RetryHandler`는 즉시 삭제하지 않는다. 점진적 전환:

```
Phase 1: RetryPolicy 신규 생성 (순수 Retry 로직)
Phase 2: @with_retry 데코레이터가 내부적으로 RetryPolicy 사용하도록 전환
Phase 3: RetryHandler를 "legacy wrapper"로 표시 (deprecated)
Phase 4: 소비자(shopping 등)가 PolicyComposer로 마이그레이션 완료 후 제거
```

### 3.3 현재 RetryHandler.execute() 호출을 PolicyComposer로 대체

**Before (현재)**:
```python
# handler.py 내부에 모든 것이 하드코딩
handler = RetryHandler(config=config, domain="payment")
result = handler.execute(call_payment_api, order_id=123)
# → Kill Switch, ErrorBudgetGate, RateLimit, Throttle, Audit, DLQ 전부 내장
```

**After (Policy Composition)**:
```python
from selfhealing.composition import compose, retry, PolicyComposer

policy = (
    PolicyComposer("payment")
    .add_guard(KillSwitchGuard())
    .add_guard(ErrorBudgetGuard())
    .add_hook(AuditHook())
    .add_hook(MetricsHook())
    .add_sink(DLQSink())
    .with_policy(retry(max_attempts=3, backoff=exponential()))
    .build()
)

result = policy.execute(call_payment_api, order_id=123)
```

소비자는 필요 없는 Guard/Hook/Sink를 제거할 수 있다:

```python
# 최소 구성 — Retry만
policy = PolicyComposer("simple").with_policy(retry(max_attempts=5)).build()
```

## 4. models.py 변경사항

### 4.1 RetryConfig — 하드코딩 설정 제거

현재 `RetryConfig`에 Rate Limit, Throttle, Critical Tier 설정이 포함되어 있다:

```python
# 현재 (models.py L48-72)
@dataclass
class RetryConfig:
    max_attempts: int = 3
    backoff_base: int = 4
    backoff_max: int = 180
    jitter_percent: int = 25
    retryable_exceptions: tuple[type[Exception], ...] = ...
    non_retryable_exceptions: tuple[type[Exception], ...] = ...
    enable_dlq: bool = True                      # ← DLQ 의존
    domain: str = "default"
    rate_limit_aware: bool = True                 # ← RateLimit 의존
    rate_limit_key: str | None = None             # ← RateLimit 의존
    throttle_aware: bool = True                   # ← Throttle 의존
    throttle_backoff_multiplier_cap: float = 4.0  # ← Throttle 의존
    critical_tier_full_stop_grace_retries: int = 1  # ← Throttle 의존
    critical_tier_full_stop_max_delay: int = 720    # ← Throttle 의존
```

순수 RetryPolicy용 Config:

```python
@dataclass
class RetryPolicyConfig:
    """RetryPolicy 전용 설정. 순수 재시도 관심사만 포함."""
    max_attempts: int = 3
    backoff_base: int = 4
    backoff_max: int = 180
    jitter_percent: int = 25
    retryable_exceptions: tuple[type[Exception], ...] = field(default_factory=lambda: (Exception,))
    non_retryable_exceptions: tuple[type[Exception], ...] = field(default_factory=tuple)
    domain: str = "default"
    enable_dlq: bool = True  # ← metadata["should_dlq"] 플래그용 (FailureSink가 참조)
    # rate_limit_* → RateLimitCoordinator 생성자 주입
    # throttle_* → BackoffStrategy 생성자 주입
    # critical_tier_* → PolicyComposer 레벨에서 처리
```

### 4.2 RetryResult — 하위 호환 유지

기존 `RetryResult`는 내부적으로 유지하되, `PolicyResult`로의 변환 메서드를 추가한다:

```python
@dataclass
class RetryResult:
    # ... 기존 필드 유지 ...

    def to_policy_result(self) -> PolicyResult:
        """PolicyResult로 변환."""
        if self.success:
            outcome = PolicyOutcome.SUCCESS
        else:
            outcome = PolicyOutcome.FAILURE

        return PolicyResult(
            value=self.value,
            outcome=outcome,
            error=self.error,
            total_attempts=self.attempt,
            executed_policies=["retry"],
            metadata={"dlq_id": self.dlq_id, "action": self.action.value},
        )
```

## 5. decorators.py 전환 (✅ 완료)

```python
# 전환 전 (레거시)
def with_retry(domain="default", max_attempts=None, ...):
    def decorator(func):
        def wrapper(*args, **kwargs):
            handler = RetryHandler(config=config, domain=domain)
            result = handler.execute(func, *args, **kwargs)
            ...

# 전환 후 (현재 decorators.py L42-60) — 내부적으로 RetryPolicy 사용
def with_retry(domain="default", max_attempts=None, ...):
    def decorator(func):
        def wrapper(*args, **kwargs):
            config = RetryPolicyConfig.from_settings(domain)  # L43
            policy = RetryPolicy(config=config)                # L49
            result = policy.execute(func, *args, **kwargs)     # L50
            if result.success:
                return result.value
            else:
                raise MaxRetriesExceededError(...)
```

## 6. 영향 범위

### 6.1 현재 `RetryHandler` 직접 사용처

| 위치 | 사용 방식 |
|------|-----------|
| `services/retry_handler/decorators.py` L50 | `policy.execute()` (RetryPolicy로 전환 완료) |
| `services/retry_handler/__init__.py` | re-export |
| `services/__init__.py` | public API 노출 없음 (v2.0에서 제거됨) |

### 6.2 `@with_retry` 데코레이터 사용처

| 위치 | 사용 방식 |
|------|-----------|
| `services/retry_handler/decorators.py` L34 (docstring 예시) | 예시 코드 |
| 테스트: `tests/unit/services/test_retry_handler_unit.py` L414, L427 | 단위 테스트 |

### 6.3 `RetryConfig.from_settings()` 사용처

`models.py` L76에서 정의. `handler.py` L92에서 호출, `decorators.py` L43에서 호출.

## 7. 체크리스트

- [x] `interfaces/resilience_policy.py` 생성 (225번 인터페이스) — ✅ 완료
- [x] `PolicyHook.on_retry(policy_name, attempt, delay)` 메서드 추가 (225 인터페이스 보완) — ✅ 완료
- [x] `BackoffStrategy.calculate(attempt, context=None)` 서명 확장 (기존 `core/backoff.py` ABC) — ✅ 완료
- [x] `RetryPolicyConfig` 데이터 클래스 생성 — ✅ 완료
- [x] `RetryPolicy` 클래스 구현 (순수 Retry 로직 + Collaborator 패턴) — ✅ 완료
- [x] `KillSwitchGuard` 구현 — ✅ 완료
- [x] `ErrorBudgetGuard` 구현 — ✅ 완료
- [x] `AuditHook` 구현 (log_retry_audit 래핑) — ✅ 완료
- [x] `MetricsHook` 구현 (Prometheus 메트릭 래핑) — ✅ 완료
- [x] `DLQSink` 구현 (store_to_dlq 래핑, Dumb Sink — `should_dlq` 플래그만 확인) — ✅ 완료
- [x] `@with_retry` 데코레이터가 RetryPolicy 사용하도록 전환 — ✅ 완료
- [x] RetryHandler에 `warnings.warn(DeprecationWarning)` 표시 — ✅ 완료 (`handler.py` L87-90)
- [x] 기존 테스트 통과 확인 — ✅ 187 tests passed (retry_handler 30 + resilience_policy 79 + backoff 78)
- [x] 단위 테스트 작성 — ✅ 111 tests passed (6개 파일 분리)
  - `test_retry_policy.py` — RetryPolicy 핵심 실행 흐름, Collaborator(sleeper·budget·coordinator·backoff), rate_limit 감지, context (38 tests)
  - `test_retry_policy_config.py` — RetryPolicyConfig 기본값·변환, RetryResult→PolicyResult 변환 (18 tests)
  - `test_retry_guards.py` — KillSwitchGuard·ErrorBudgetGuard 계약·동작·Fail-Open (13 tests)
  - `test_retry_hooks.py` — AuditHook·MetricsHook 계약·동작·Fail-Open, PolicyHook.on_retry Protocol (21 tests)
  - `test_retry_sinks.py` — DLQSink handle_failure·should_dlq 플래그·Fail-Open (7 tests)
  - `test_retry_handler_exports.py` — 패키지 re-export 검증, with_retry 데코레이터 RetryPolicy 전환 (14 tests)
  - 기존 레거시 테스트(`test_retry_handler_unit.py` 30 tests) 미파손 확인

> **변경 사항 (이전 체크리스트 대비)**:
> - `RetryBudgetGuard` 항목 삭제 → RetryPolicy Collaborator로 재분류 (Guard 부적합)
> - `PolicyHook.on_retry()` 항목 추가 — 225 인터페이스 보완 필요
> - `BackoffStrategy.calculate()` 서명 확장 항목 추가 — context 전달 지원
> - `DLQSink` 설명에 "Dumb Sink" 명시 — `should_dlq` 플래그 기반

## 8. 설계 논의 확정 사항

6가지 설계 질문에 대한 논의 결과를 확정한다. 모든 결정은 코드 근거에 기반한다.

### 8.1 RetryBudgetGuard → Collaborator 재분류

**질문**: `AdaptiveRetryBudget`을 PolicyComposer 레벨의 Guard(진입 전 1회)로 취급할지,
RetryPolicy의 내부 Collaborator(루프 내 매회 호출)로 정의할지.

**확정**: **Collaborator**. `RetryPolicy.__init__`에 `retry_budget: AdaptiveRetryBudget | None` 파라미터 추가.

**코드 근거**:
- `handler.py` L472: `record_request(is_retry=(attempt > 1))` — 매 시도마다 상태 변경
- `handler.py` L475: `should_allow_retry()` — 루프 내부에서 매회 확인
- `handler.py` L536: `adjust_budget_for_throttle_state(throttle_reason)` — 루프 중간에 예산 동적 삭감
- `budget.py` L49-74: `should_allow_retry()`는 윈도우 내 `current_retry_count / current_total_count` 비율 계산 (stateful)
- `resilience_policy.py` L287: `PolicyGuard.check(context)` 서명은 stateless 진입 전 1회 체크 상정

**Guard에 부적합한 이유**:
1. `record_request()`: Guard는 상태를 변경하지 않으나 RetryBudget은 매 호출마다 카운터 증가
2. `adjust_budget_for_throttle_state()`: 루프 중간에 외부 상태(throttle reason)에 따라 내부 비율 변경
3. `check(context)` 서명으로는 `is_retry` 파라미터를 전달할 수 없음

**네이밍 검증**: `retry_budget` — 기존 `handler._retry_budget` (handler.py L128)과 동일 패턴. 충돌 없음.

### 8.2 DLQ 저장 판단 — A안 (RetryPolicy가 마킹, Sink는 저장만)

**질문**: "이 실패는 DLQ에 저장해야 한다"는 판단(Flagging)을 누가 하는가.

**확정**: **A안**. `PolicyResult.metadata["should_dlq"]` 플래그 사용.
RetryPolicy가 `config.enable_dlq` 값을 기반으로 마킹하고,
`FailureSink`는 판단 로직 없는 Dumb Sink로 구현한다.

**코드 근거**:
- `handler.py` L566: `if self.config.enable_dlq:` — RetryHandler가 이미 DLQ 여부를 판정
- `handler.py` L579-580: `RetryResult(action=RetryAction.DLQ if dlq_id else RetryAction.ABORT)` — 판정 결과를 결과에 표시하는 기존 패턴
- `resilience_policy.py` L112: `metadata: dict[str, Any]` — 자유 형식 딕셔너리로 키 추가 가능
- `models.py` L57: `enable_dlq: bool = True` — 기존 Config에 이미 존재하는 필드

**should_dlq 매핑 로직**:
```python
# RetryPolicy.execute() 종료 시
metadata={"should_dlq": self._config.enable_dlq, ...}

# DLQSink.handle_failure() 구현
def handle_failure(self, error, context, policy_result) -> str | None:
    if not policy_result.metadata.get("should_dlq", False):
        return None  # DLQ 비활성화 → 저장하지 않음
    return self._store_to_dlq(error, context, policy_result)
```

**B안 기각 이유**: FailureSink가 `RetryPolicyConfig`를 알아야 하므로 Retry-specific 판단이 Sink로 누출된다 (SRP 위반).

**네이밍 검증**: `should_dlq` — 시스템에 `enable_dlq` (models.py L57)가 존재하나,
`should_dlq`는 "config 판정 결과"를 표현하므로 의미상 구분됨. 충돌 없음.

### 8.3 RateLimitCoordinator — 현재 수준의 결합 허용

**질문**: `RateLimitCoordinator`를 BackoffStrategy 내부로 숨기거나
별도 WaitStrategy로 추상화할 계획이 있는지.

**확정**: **현재 수준의 생성자 주입 유지**. 추가 추상화하지 않는다.

**코드 근거**:
- `handler.py` L480: `_wait_for_rate_limit()` — 함수 실행 **직전** 대기
- `handler.py` L487: `on_success(self._rate_limit_key)` — 성공 시 coordinator에 알림
- `handler.py` L519-520: `is_rate_limit_error(e)` + `_handle_rate_limit_error(e)` — 실패 시 429 감지 → 글로벌 쿨다운 설정
- `handler.py` L354-390: `get_combined_delay()` — 429 쿨다운 + throttle 백오프 중 `max()` 선택
- `handler.py` L73: `rate_limit_coordinator: RateLimitCoordinator | None = None` — 이미 optional 주입 패턴

**추상화하지 않는 이유**:
RateLimitCoordinator는 `wait_if_needed()`, `on_success()`, `on_rate_limited()` 3개 메서드를
루프의 **서로 다른 시점**에서 호출한다. BackoffStrategy에 숨기면 "대기 계산" 외에
"429 감지", "성공 알림", "쿨다운 설정"까지 책임지게 되어 SRP 위반.

**네이밍 검증**: `rate_limit_coordinator` — 기존 handler.py L66과 동일. 충돌 없음.

### 8.4 BackoffStrategy.calculate()에 context 전달

**질문**: `BackoffStrategy.calculate()` 메서드에 `context: PolicyContext`를 추가할지.

**확정**: `calculate(attempt, context=None)` 서명으로 확장한다.

**코드 근거**:
- `calculator.py` L101-155: `ThrottleAwareBackoffCalculator`는 `service_name`으로
  ThrottleRegistry에서 서비스별 throttle을 조회, Emergency Level에 따라 배율 적용
- `handler.py` L293-335: `get_next_delay(attempt, is_critical_tier)` — `is_critical_tier` 파라미터가
  CRITICAL 티어 grace retry 허용 판단에 사용. RetryPolicy에서는 `PolicyContext.tier_id`로 전달 필요
- `handler.py` L317-328: CRITICAL 티어 Full Stop 시 `critical_tier_full_stop_max_delay` 반환 분기

**기존 BackoffStrategy와의 관계**:
```
 core/backoff.py                    services/backoff_calculator/calculator.py
 ┌────────────────────┐             ┌────────────────────────────────┐
 │ BackoffStrategy(ABC)│             │ BackoffCalculator               │
 │  calculate(attempt) │             │  calculate(attempt, with_jitter)│
 │  reset()            │             │                                 │
 ├────────────────────┤             ├────────────────────────────────┤
 │ ExponentialBackoff  │             │ ThrottleAwareBackoffCalculator  │
 │ LinearBackoff       │             │  calculate_with_throttle_context│
 │ ConstantBackoff     │             └────────────────────────────────┘
 │ DecorrelatedJitter  │
 └────────────────────┘
```

`core/backoff.py`의 `BackoffStrategy(ABC)`(`calculate(attempt) -> float`)가 이미 존재하며
4개 구현체가 사용 중이다. **새 Protocol을 만들면 네이밍 충돌**이 발생한다.

**구현 방안**: 기존 `BackoffStrategy(ABC).calculate()` 시그니처를 확장한다:
```python
# core/backoff.py (변경 전)
class BackoffStrategy(ABC):
    @abstractmethod
    def calculate(self, attempt: int) -> float: ...

# core/backoff.py (변경 후 — L19-23) — context=None 기본값으로 하위 호환 유지
class BackoffStrategy(ABC):
    @abstractmethod
    def calculate(self, attempt: int, context: PolicyContext | None = None) -> float: ...
```

기존 4개 구현체(`ExponentialBackoff`, `LinearBackoff`, `ConstantBackoff`, `DecorrelatedJitterBackoff`)는
context를 무시하면 되므로 하위 호환성이 유지된다.

**네이밍 검증**: `BackoffStrategy` — `core/backoff.py` L19에 이미 존재. **기존 ABC를 확장**하는 방식이므로
새 이름을 만들 필요 없음. 충돌 해소.

### 8.5 Sleeper 함수 주입

**질문**: `RetryPolicy`에 `sleeper: Callable[[float], None]`을 주입할지.

**확정**: `sleeper: Callable[[float], None] | None = None` 추가. 기본값 `None`은 sleep 미수행.

**코드 근거**:
- `handler.py` L554-557:
  ```python
  logger.info(f"[RetryHandler] Will retry in {delay}s "
              f"(attempt {attempt + 1}/{effective_max_attempts})")
  # For synchronous execution, we don't actually sleep
  # The caller (usually Celery) handles the delay
  ```
  현재 `RetryHandler.execute()`는 **`time.sleep()`을 호출하지 않는다**.
- `models.py` L204: `RetryResult.next_delay`는 결과에 포함되어 caller에게 전달

**동작 매트릭스**:
| sleeper 값 | 동작 | 용도 |
|-----------|------|------|
| `None` (기본) | sleep 안 함, delay를 metadata에 포함 | 프로덕션 (Celery 위임, 현재 handler.py 동작) |
| `time.sleep` | 실제 대기 | Celery 없이 직접 사용 시 |
| `lambda _: None` | 즉시 통과 | 단위 테스트 |

**네이밍 검증**: `sleeper` — 시스템에 존재하지 않음. 충돌 없음.

### 8.6 PolicyHook.on_retry() 메서드 추가

**질문**: `PolicyHook`에 `on_retry(policy_name, attempt, delay)` 메서드를 추가할지,
아니면 `on_failure`에 재시도 정보를 함께 넘겨줄지.

**확정**: `PolicyHook.on_retry(policy_name, attempt, delay)` 메서드를 **별도 추가**한다.

**코드 근거**:
- `handler.py` L554:
  ```python
  logger.info(f"[RetryHandler] Will retry in {delay}s "
              f"(attempt {attempt + 1}/{effective_max_attempts})")
  ```
  "재시도 예정" 시점에 별도 로그를 남기는 기존 패턴.
- `handler.py` L517: `logger.warning(f"... Attempt {attempt}/{effective_max_attempts} failed: {e}")` — 이것이 on_failure에 해당
- `handler.py` L343-348: `_record_critical_tier_grace_metric()` — grace retry 시 Prometheus 메트릭 기록. "재시도 결정 시점"의 이벤트.

**on_failure vs on_retry 의미론적 차이**:
| 상황 | on_failure | on_retry |
|------|-----------|----------|
| 1차 시도 실패, 재시도 예정 | ✅ 호출 | ✅ 호출 |
| 2차 시도 실패, 재시도 예정 | ✅ 호출 | ✅ 호출 |
| 마지막 시도 실패 (DLQ 이동) | ✅ 호출 | ❌ **미호출** |
| Budget 소진으로 재시도 중단 | ✅ 호출 | ❌ **미호출** |

**on_failure에 정보를 합치지 않는 이유**:
- `will_retry: bool` 같은 인자를 on_failure에 추가하면 on_failure의 책임 범위 확대
- 기존 `resilience_policy.py` L321: `on_failure(policy_name, error, attempt)` 서명과 비호환

**225 인터페이스 보완 필요**:
```python
# resilience_policy.py — PolicyHook Protocol 변경
class PolicyHook(Protocol):
    def on_execute(self, policy_name: str, attempt: int) -> None: ...
    def on_success(self, policy_name: str, result: PolicyResult) -> None: ...
    def on_failure(self, policy_name: str, error: Exception, attempt: int) -> None: ...
    def on_reject(self, policy_name: str, reason: str) -> None: ...
    def on_retry(self, policy_name: str, attempt: int, delay: float) -> None: ...  # NEW
```

**영향 범위**: 현재 `PolicyHook`의 실 구현체는 없고 테스트 Stub만 존재 (test_resilience_policy.py L663-706). 안전하게 추가 가능.

**네이밍 검증**: `on_retry` — 시스템 전체에서 `on_retry` 검색 결과 0건. 충돌 없음.

### 8.7 네이밍 충돌 검증 종합

| 새 이름 | 시스템 존재 여부 | 판정 | 비고 |
|---------|----------------|------|------|
| `RetryPolicy` | `BatchRetryPolicy` (`utils/async_logger.py` L107) | ✅ 안전 | 완전히 다른 도메인 (비동기 로거 배치 재시도) |
| `RetryPolicyConfig` | `PolicyConfigManager` (`load_tests/` L103) | ✅ 안전 | load_tests 전용, 패키지 외부 |
| `retry_budget` | `handler._retry_budget` (handler.py L128) | ✅ 안전 | 동일 패턴 |
| `should_dlq` | `enable_dlq` (models.py L57) | ✅ 안전 | config 설정 vs 판정 결과 |
| `sleeper` | 미존재 | ✅ 안전 | |
| `on_retry` | 미존재 | ✅ 안전 | |
| `BackoffStrategy` | `core/backoff.py` L19 (ABC) | ⚠️ **기존 재활용** | 새로 만들지 않고 기존 ABC 시그니처 확장 |
| `KillSwitchGuard` | 테스트 Stub만 존재 (test_resilience_policy.py L605) | ✅ 안전 | |
| `ErrorBudgetGuard` | 테스트 Stub만 존재 (test_resilience_policy.py L621) | ✅ 안전 | |
| `DLQSink` | 테스트 Stub만 존재 (test_resilience_policy.py L755) | ✅ 안전 | |
| `AuditHook` | 미존재 | ✅ 안전 | |
| `MetricsHook` | 미존재 | ✅ 안전 | |
