# 226. RetryPolicy 전환 설계

## 1. 개요

RetryPolicy는 전환 대상 중 **가장 복잡한 패턴**이다.
현재 `RetryHandler.execute()`에 12건의 하드코딩 크로스-패턴 의존성이 존재하며,
이를 Guard/Hook/Sink 인터페이스로 분리해야 한다.

## 2. 현재 구현 분석

### 2.1 파일 위치

| 파일 | 역할 | 라인 수 |
|------|------|---------|
| `services/retry_handler/handler.py` | 핵심 재시도 로직 | 642줄 |
| `services/retry_handler/models.py` | RetryConfig, RetryResult, RetryAction | 143줄 |
| `services/retry_handler/decorators.py` | `@with_retry` 데코레이터 | 64줄 |
| `services/retry_handler/__init__.py` | 패키지 퍼사드 | re-export |

### 2.2 handler.py의 execute() 실행 흐름 (고정 순서)

```
handler.py L419: Kill Switch 체크 ← SystemControlManager lazy import (L29)
    ↓
handler.py L428: ErrorBudgetGate 체크 ← check_automation_allowed lazy import (L157)
    ↓
handler.py L456: while attempt < effective_max_attempts
    │
    ├─ L461: AdaptiveRetryBudget.record_request() ← 직접 생성 (L107)
    ├─ L463: AdaptiveRetryBudget.should_allow_retry()
    ├─ L468: RateLimit 대기 ← get_rate_limit_coordinator lazy import (L118)
    ├─ L470: func(*args, **kwargs) 실행
    │
    │  [성공 시]
    ├─ L474: RateLimitCoordinator.on_success()
    ├─ L479: _log_retry_audit() ← audit_helpers lazy import (L138)
    │
    │  [실패 시]
    ├─ L505: is_rate_limit_error() + _handle_rate_limit_error()
    ├─ L510: get_combined_delay() ← ThrottleAwareBackoffCalculator (L89)
    ├─ L515: Full Stop → break
    ├─ L519: _log_retry_audit() (실패 기록)
    └─ L535: should_retry() 판단

handler.py L552: _move_to_dlq() ← dlq_service.store_to_dlq lazy import (L587)
```

### 2.3 하드코딩 의존성 12건 분류

#### Guard로 분리 (3건)

| # | 의존 대상 | 현재 위치 | 분리 후 |
|---|-----------|-----------|---------|
| 1 | `SystemControlManager.is_enabled()` | handler.py L29, L419 | `KillSwitchGuard.check()` |
| 2 | `check_automation_allowed()` | handler.py L157, L428 | `ErrorBudgetGuard.check()` |
| 3 | `AdaptiveRetryBudget.should_allow_retry()` | handler.py L107, L463 | `RetryBudgetGuard.check()` |

#### Hook으로 분리 (3건)

| # | 의존 대상 | 현재 위치 | 분리 후 |
|---|-----------|-----------|---------|
| 4 | `audit_helpers.log_retry_audit()` | handler.py L138 | `PolicyHook.on_success()` / `.on_failure()` |
| 5 | `retry_critical_tier_grace_retries_total` (Prometheus) | handler.py L290 | `PolicyHook.on_execute()` |
| 6 | `RateLimitCoordinator.on_success()` | handler.py L474 | `PolicyHook.on_success()` 내부 |

#### Sink로 분리 (1건)

| # | 의존 대상 | 현재 위치 | 분리 후 |
|---|-----------|-----------|---------|
| 7 | `dlq_service.store_to_dlq()` | handler.py L587 | `FailureSink.handle_failure()` |

#### 생성자 주입으로 변경 (3건)

| # | 의존 대상 | 현재 위치 | 분리 후 |
|---|-----------|-----------|---------|
| 8 | `ThrottleAwareBackoffCalculator` | handler.py L89 | `BackoffStrategy` 인터페이스 주입 |
| 9 | `AdaptiveRetryBudget` | handler.py L107 | 생성자 파라미터 |
| 10 | `RateLimitCoordinator` | handler.py L118 | 생성자 파라미터 (이미 optional) |

#### 유지 (2건, 경미한 유틸리티 의존)

| # | 의존 대상 | 현재 위치 | 판단 |
|---|-----------|-----------|------|
| 11 | `core.timezone.now` | handler.py L14 | 유틸리티, 커플링 경미. 유지 |
| 12 | `BackoffConfig` (데이터 객체) | handler.py L16 | 데이터 전달용, 유지 |

## 3. 전환 설계

### 3.1 RetryPolicy 클래스

```python
class RetryPolicy(ResiliencePolicy[T]):
    """
    순수 재시도 Policy.

    현재 RetryHandler.execute()에서 하드코딩된 외부 의존성을
    모두 제거하고, 순수한 재시도 로직만 담당한다.

    외부 관심사는 Guard/Hook/Sink로 주입받는다.
    """

    def __init__(
        self,
        config: RetryConfig,
        backoff: BackoffStrategy | None = None,
        rate_limit_coordinator: RateLimitCoordinator | None = None,
    ):
        self._config = config
        self._backoff = backoff or BackoffCalculator(
            BackoffConfig(
                base=config.backoff_base,
                max_delay=config.backoff_max,
                jitter_percent=config.jitter_percent,
            )
        )
        self._rate_limit_coordinator = rate_limit_coordinator

    @property
    def name(self) -> str:
        return "retry"

    def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        순수 재시도 실행.

        Kill Switch, ErrorBudgetGate, Audit, DLQ는
        이 메서드 내에 없다. PolicyComposer가 처리한다.
        """
        attempt = 0
        last_error = None

        while attempt < self._config.max_attempts:
            attempt += 1

            # Rate limit 대기 (선택적)
            if self._rate_limit_coordinator:
                self._rate_limit_coordinator.wait_if_needed(
                    self._config.rate_limit_key or self._config.domain
                )

            try:
                result = func(*args, **kwargs)
                return PolicyResult(
                    value=result,
                    outcome=PolicyOutcome.SUCCESS,
                    total_attempts=attempt,
                    executed_policies=["retry"],
                )
            except Exception as e:
                last_error = e

                if not self._should_retry(e, attempt):
                    break

                # Backoff 계산
                delay = self._backoff.calculate(attempt)
                # Note: 실제 대기는 caller(Celery 등)가 처리

        return PolicyResult(
            outcome=PolicyOutcome.FAILURE,
            error=last_error,
            total_attempts=attempt,
            executed_policies=["retry"],
            metadata={
                "max_attempts": self._config.max_attempts,
                "domain": self._config.domain,
            },
        )
```

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
# 현재 (models.py L55-72)
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
    # enable_dlq → FailureSink에서 처리
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
        elif self.action == RetryAction.DLQ:
            outcome = PolicyOutcome.FAILURE
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

## 5. decorators.py 전환

```python
# 현재 (decorators.py)
def with_retry(domain="default", max_attempts=None, ...):
    def decorator(func):
        def wrapper(*args, **kwargs):
            handler = RetryHandler(config=config, domain=domain)
            result = handler.execute(func, *args, **kwargs)
            ...

# 전환 후 — 내부적으로 RetryPolicy 사용
def with_retry(domain="default", max_attempts=None, ...):
    def decorator(func):
        def wrapper(*args, **kwargs):
            policy = RetryPolicy(config=RetryPolicyConfig(...))
            result = policy.execute(func, *args, **kwargs)
            if result.success:
                return result.value
            else:
                raise MaxRetriesExceededError(...)
```

## 6. 영향 범위

### 6.1 현재 `RetryHandler` 직접 사용처

| 위치 | 사용 방식 |
|------|-----------|
| `services/retry_handler/decorators.py` L48 | `handler.execute()` |
| `services/retry_handler/__init__.py` | re-export |
| `services/__init__.py` | public API 노출 없음 (v2.0에서 제거됨) |

### 6.2 `@with_retry` 데코레이터 사용처

| 위치 | 사용 방식 |
|------|-----------|
| `services/retry_handler/decorators.py` L34 (docstring 예시) | 예시 코드 |
| 테스트: `tests/unit/services/test_retry_handler_unit.py` L414, L427 | 단위 테스트 |

### 6.3 `RetryConfig.from_settings()` 사용처

`models.py` L77에서 정의. `handler.py` L78에서 호출, `decorators.py` L49에서 호출.

## 7. 체크리스트

- [ ] `interfaces/resilience_policy.py` 생성 (225번 인터페이스)
- [ ] `RetryPolicyConfig` 데이터 클래스 생성
- [ ] `RetryPolicy` 클래스 구현 (순수 Retry 로직)
- [ ] `KillSwitchGuard` 구현
- [ ] `ErrorBudgetGuard` 구현
- [ ] `RetryBudgetGuard` 구현 (AdaptiveRetryBudget 래핑)
- [ ] `AuditHook` 구현 (log_retry_audit 래핑)
- [ ] `MetricsHook` 구현 (Prometheus 메트릭 래핑)
- [ ] `DLQSink` 구현 (store_to_dlq 래핑)
- [ ] `@with_retry` 데코레이터가 RetryPolicy 사용하도록 전환
- [ ] RetryHandler에 `@deprecated` 표시
- [ ] 기존 테스트 통과 확인
