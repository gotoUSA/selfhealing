# 225. Policy Composition 인터페이스 설계

## 1. 개요

이 문서는 Policy Composition의 핵심 인터페이스를 정의한다.
모든 resilience 패턴이 동일한 Protocol을 구현하여 선언적 조합이 가능하게 한다.

**참조 구현**: resilience4j Decorators, Polly PolicyWrap, Microsoft.Extensions.Http.Resilience

## 2. 핵심 인터페이스

### 2.1 PolicyResult — 통합 결과 타입

현재 각 패턴이 서로 다른 결과 타입을 사용한다:

| 패턴 | 현재 결과 타입 | 위치 |
|------|---------------|------|
| Retry | `RetryResult(success, action, attempt, value, error, dlq_id)` | `services/retry_handler/models.py` |
| Fallback | `FallbackResult(value, used_fallback, fallback_mode, original_error)` | `core/fallback_strategy.py` |
| Circuit Breaker | `CircuitBreakerResult(success, previous_state, new_state, message)` | `services/circuit_breaker/config.py` |
| Bulkhead | 예외 기반 (`BulkheadFullError`) | `resilience/bulkhead/exceptions.py` |
| Hedging | `FallbackResult` 재사용 (상속) | `core/hedging/strategy.py` |

통합 결과 타입 설계:

```python
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class PolicyOutcome(str, Enum):
    """Policy 실행 결과 종류."""
    SUCCESS = "success"              # 정상 성공
    SUCCESS_WITH_FALLBACK = "fallback"  # Fallback으로 성공
    REJECTED = "rejected"            # Policy에 의해 거부 (CB open, Bulkhead full 등)
    FAILURE = "failure"              # 모든 시도 실패
    TIMEOUT = "timeout"              # 시간 초과


@dataclass
class PolicyResult(Generic[T]):
    """모든 Policy의 통합 결과 타입."""

    value: T | None = None
    outcome: PolicyOutcome = PolicyOutcome.SUCCESS
    error: Exception | None = None

    # 실행 메타데이터
    executed_policies: list[str] = field(default_factory=list)
    total_attempts: int = 1
    total_duration_ms: float = 0.0

    # 패턴별 상세 정보 (선택적)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.outcome in (PolicyOutcome.SUCCESS, PolicyOutcome.SUCCESS_WITH_FALLBACK)

    @property
    def rejected(self) -> bool:
        return self.outcome == PolicyOutcome.REJECTED
```

### 2.2 ResiliencePolicy — 핵심 Protocol

```python
from typing import Protocol, Callable, TypeVar

T = TypeVar("T")


class ResiliencePolicy(Protocol[T]):
    """
    모든 resilience 패턴이 구현하는 핵심 Protocol.

    각 Policy는 함수를 래핑하여 resilience 로직을 적용한다.
    Policy 간 조합은 PolicyComposer가 담당한다.
    """

    @property
    def name(self) -> str:
        """Policy 식별자 (예: 'retry', 'circuit_breaker', 'bulkhead')."""
        ...

    def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        함수를 Policy로 감싸서 실행.

        Args:
            func: 실행할 함수
            *args, **kwargs: 함수 인자

        Returns:
            PolicyResult[T]: 통합 결과
        """
        ...

    async def execute_async(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """비동기 함수 실행. 동기 전용 Policy는 NotImplementedError."""
        ...
```

### 2.3 Guard — 사전 검증 훅

현재 `RetryHandler.execute()` 내부에 하드코딩된 pre-check 로직을 독립 Guard로 분리한다.

```python
class PolicyGuard(Protocol):
    """
    Policy 실행 전 사전 검증.

    현재 하드코딩 위치:
    - Kill Switch: handler.py L419 `_is_system_enabled()`
    - ErrorBudgetGate: handler.py L428 `_check_error_budget_gate()`
    """

    @property
    def name(self) -> str:
        """Guard 식별자."""
        ...

    def check(self) -> GuardResult:
        """
        실행 허용 여부 확인.

        Returns:
            GuardResult: allowed=True면 통과, False면 거부
        """
        ...


@dataclass
class GuardResult:
    """Guard 검증 결과."""
    allowed: bool
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

**현재 코드에서의 Guard 후보**:

| Guard | 현재 위치 | 현재 사용처 |
|-------|-----------|-------------|
| `KillSwitchGuard` | `services/system_control.py` → `SystemControlManager.is_enabled()` | `handler.py` L419 `_is_system_enabled()` 내부에서 lazy import |
| `ErrorBudgetGuard` | `services/error_budget_gate/` → `check_automation_allowed()` | `handler.py` L157 `_check_error_budget_gate()` 내부에서 lazy import |
| `RetryBudgetGuard` | `services/backoff_calculator/` → `AdaptiveRetryBudget.should_allow_retry()` | `handler.py` L463 루프 내부 |

### 2.4 PolicyHook — 실행 이벤트 옵저버

```python
class PolicyHook(Protocol):
    """
    Policy 실행 중 이벤트를 관찰하는 훅.

    Fail-Open 원칙: 훅 실패가 비즈니스 로직을 중단시키지 않는다.

    현재 하드코딩 위치:
    - Audit: handler.py L138 `_log_retry_audit()` — Fail-Open lazy import
    - Metrics: handler.py L290 `_record_critical_tier_grace_metric()` — ImportError 무시
    - EventBus: hedging/strategy.py L108 EventBus 구독
    """

    def on_execute(self, policy_name: str, attempt: int) -> None:
        """실행 시작 시."""
        ...

    def on_success(self, policy_name: str, result: PolicyResult) -> None:
        """성공 시."""
        ...

    def on_failure(self, policy_name: str, error: Exception, attempt: int) -> None:
        """실패 시."""
        ...

    def on_reject(self, policy_name: str, reason: str) -> None:
        """거부 시 (CB open, Bulkhead full 등)."""
        ...
```

### 2.5 FailureSink — 최종 실패 처리

```python
class FailureSink(Protocol):
    """
    모든 Policy가 소진된 후 최종 실패를 처리하는 인터페이스.

    현재 하드코딩 위치:
    - DLQ: handler.py L587 `_move_to_dlq()` — dlq_service.store_to_dlq() lazy import
    """

    def handle_failure(
        self,
        error: Exception,
        context: dict[str, Any],
        policy_result: PolicyResult,
    ) -> str | None:
        """
        최종 실패 처리.

        Returns:
            실패 기록 ID (예: DLQ ID) 또는 None
        """
        ...
```

## 3. 기존 타입과의 매핑

### 3.1 RetryResult → PolicyResult 매핑

```python
# 현재 (services/retry_handler/models.py)
@dataclass
class RetryResult:
    success: bool
    action: RetryAction      # SUCCESS, RETRY, DLQ, ABORT
    attempt: int
    value: Any = None
    error: Exception | None = None
    dlq_id: int | None = None

# Policy Composition 전환 매핑
# RetryAction.SUCCESS → PolicyOutcome.SUCCESS
# RetryAction.DLQ     → PolicyOutcome.FAILURE + FailureSink 처리
# RetryAction.ABORT   → PolicyOutcome.FAILURE 또는 REJECTED
```

### 3.2 FallbackResult → PolicyResult 매핑

```python
# 현재 (core/fallback_strategy.py)
@dataclass
class FallbackResult(Generic[T]):
    value: T | None
    used_fallback: bool
    fallback_mode: FallbackMode | None
    original_error: str | None

# Policy Composition 전환 매핑
# used_fallback=False → PolicyOutcome.SUCCESS
# used_fallback=True  → PolicyOutcome.SUCCESS_WITH_FALLBACK
# value=None + FAIL_FAST → PolicyOutcome.FAILURE
```

### 3.3 CircuitBreakerResult 변환 없음

`CircuitBreakerResult`는 **상태 변경 결과**(force_open/force_close 등)이지 함수 실행 결과가 아니다.
`CircuitBreakerPolicy.execute()`는 `should_allow()` 기반으로 `PolicyResult`를 직접 생성한다.

```python
# 현재 (services/circuit_breaker/config.py)
@dataclass
class CircuitBreakerResult:
    success: bool
    previous_state: str
    new_state: str
    message: str
# → 상태 관리용으로 유지, Policy 인터페이스와 별개
```

## 4. 기존 인터페이스 패턴과의 일관성

현재 시스템은 `selfhealing/interfaces/` 디렉토리에 **Repository/Adapter 패턴**의 인터페이스를 정의한다:

```
interfaces/
├── audit_adapter.py      # AuditLogAdapter
├── cache_provider.py     # CacheProviderInterface
├── repositories.py       # FailedOperationRepository, CircuitBreakerStateRepository
├── task_queue.py         # TaskQueueInterface
└── web_framework.py      # WebFrameworkInterface
```

이 구조를 따라 Policy 인터페이스도 동일 위치에 배치한다:

```
interfaces/
├── (기존 파일들 유지)
└── resilience_policy.py  # NEW: ResiliencePolicy, PolicyResult, PolicyGuard, PolicyHook, FailureSink
```

## 5. Hedging 커스터마이징을 위한 내부 Policy 주입

이전 논의에서 지적된 대로, Hedging은 내부에서 Bulkhead를 후보별/전체 단위로 세밀하게 제어한다.
Policy Composition에서 이를 지원하기 위해, HedgingPolicy에 **내부 하위 Policy 주입** 인터페이스를 제공한다:

```python
class HedgingPolicy(ResiliencePolicy[T]):
    """
    Hedging 전략의 Policy 래퍼.

    내부 하위 Policy를 주입하여 각 후보별 또는 전체 실행에
    별도의 resilience 정책을 적용할 수 있다.

    현재 하드코딩:
    - bulkhead_name 파라미터 (hedging/config.py L66)
    - acquire_bulkhead_per_candidate 분기 (hedging/config.py L69-L75)
    - _acquire_bulkhead()/_release_bulkhead() (hedging/strategy.py L189-L216)
    """

    def __init__(
        self,
        candidates: list[Callable[..., T]],
        config: HedgingConfig,
        # NEW: 내부 하위 Policy 주입
        per_candidate_policy: ResiliencePolicy[T] | None = None,
        overall_policy: ResiliencePolicy[T] | None = None,
    ):
        """
        Args:
            per_candidate_policy: 각 후보 실행에 적용할 Policy (예: 개별 Bulkhead)
            overall_policy: 전체 헷징 실행에 적용할 Policy (예: 전체 Bulkhead)
        """
        ...
```

이 설계로 현재의 `acquire_bulkhead_per_candidate` 분기 로직을 선언적으로 표현 가능:

```python
# 현재 (하드코딩)
config = HedgingConfig(bulkhead_name="api", acquire_bulkhead_per_candidate=True)

# Policy Composition (선언적)
hedging = HedgingPolicy(
    candidates=[fn_a, fn_b],
    per_candidate_policy=BulkheadPolicy("api", max_concurrent=5),  # 후보별 격벽
)

# 또는
hedging = HedgingPolicy(
    candidates=[fn_a, fn_b],
    overall_policy=BulkheadPolicy("api", max_concurrent=10),  # 전체 격벽
)
```

## 6. 기존 코드 유지 원칙

Policy 인터페이스는 **기존 구현을 감싸는 래퍼**이지 대체가 아니다:

```python
# RetryPolicy 내부는 기존 RetryHandler를 그대로 사용
class RetryPolicy:
    def __init__(self, config: RetryConfig):
        self._handler = RetryHandler(config=config)  # 기존 구현 재사용

    def execute(self, func, *args, **kwargs) -> PolicyResult[T]:
        result = self._handler.execute(func, *args, **kwargs)
        return self._convert_to_policy_result(result)
```

단, `RetryHandler` 내부의 하드코딩 의존성(Kill Switch, ErrorBudgetGate 등)은
생성자 주입 또는 Guard/Hook으로 외부화하는 리팩토링이 필요하다. → 226번 문서 참조
