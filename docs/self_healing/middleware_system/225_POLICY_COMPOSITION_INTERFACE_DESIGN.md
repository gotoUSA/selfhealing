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

### 2.2 PolicyContext — 실행 컨텍스트 (Immutable)

Guard(ErrorBudgetGate 등), Hook(Audit/Metrics), Sink(DLQ)가 올바르게 작동하려면
단순 함수 인자 외에 메타데이터(Trace ID, User ID, Request Context 등)가 필요하다.

현재 코드에서 이미 명시적 `context: dict` 파라미터를 사용하는 증거:

| 사용처 | 시그니처 | 활용 필드 |
|--------|---------|----------|
| `RetryHandler.execute()` | `context: dict[str, Any] \| None = None` (`handler.py` L386) | `order_id`, `payment_id`, `user_id`, `snapshot_data`, `request_data` → DLQ/Audit 전달 |
| `AdaptiveThrottle.check()` | `context: dict \| None = None` (`adaptive.py` L1572) | `service_id` → Load Shedding 대상 식별, DLQ 저장 |
| `_move_to_dlq()` | `context: dict[str, Any] \| None` (`handler.py` L578) | `context.get("order_id")`, `context.get("user_id")` 등 직접 추출 |

이를 공식 타입으로 승격한다:

```python
from __future__ import annotations
from dataclasses import dataclass, replace, field
from typing import Any


@dataclass(frozen=True)
class PolicyContext:
    """
    Policy 파이프라인 실행 컨텍스트 (Immutable).

    frozen=True로 설정하여 파이프라인 내 사이드 이펙트를 방지한다.
    수정 필요 시 dataclasses.replace()로 복사본을 생성한다 (Copy-on-Write).

    기존 시스템 frozen 선례:
    - CachedMetrics  (services/system_metrics_cache.py L24)
      → "frozen=True로 설정하여 읽기 시 동시성 문제를 원천 방지"
    - TaskResult     (interfaces/task_queue.py L56)
    - ExecutionMode  (core/execution_mode.py L40)
    - config.py의 Pydantic Settings 5건 (config.py L63, L100, L430, L530)
    """

    # 비즈니스 식별자 — handler.py L616-627 store_to_dlq()에서 사용
    order_id: str | None = None
    payment_id: str | None = None
    user_id: str | None = None

    # Policy 판정 기준 — ErrorBudgetGate.check(tier_id=, region=) 참조
    tier_id: str | None = None   # "critical" | "standard" | "non_essential"
    region: str | None = None

    # 도메인/추적 — RetryConfig.domain, OTel trace_id
    domain: str = ""
    trace_id: str | None = None

    # 확장 필드 — snapshot_data, request_data 등
    extra: dict[str, Any] = field(default_factory=dict)

    def with_updates(self, **kwargs: Any) -> PolicyContext:
        """Copy-on-Write: 변경된 필드만 교체한 새 인스턴스 반환."""
        return replace(self, **kwargs)
```

**Immutability 원칙**:

- `frozen=True`: 필드 직접 대입 시 `FrozenInstanceError` 발생
- 수정 필요 시 반드시 `context.with_updates(tier_id="critical")` 사용
- `extra: dict`의 내부 값은 Python 한계로 완전 불변 보장 불가하나,
  기존 `CachedMetrics` 선례와 동일하게 참조 불변 + Copy-on-Write 원칙으로 운영

### 2.3 ResiliencePolicy / AsyncResiliencePolicy — 핵심 Protocol

현재 코드베이스에서 동기/비동기는 **별도 클래스**로 구현되어 있다:

| 패턴 | 동기 | 비동기 | 비고 |
|------|------|--------|------|
| Retry | `RetryHandler` — `async def` 0건 | 없음 | 순수 동기 |
| Circuit Breaker | `CircuitBreakerService` — `async def` 0건 | 없음 | 순수 동기 |
| Hedging | `HedgingStrategy` — `async def` 0건 | 없음 | 순수 동기 |
| Throttle | `AdaptiveThrottle` — `async def` 0건 | 없음 | 순수 동기 |
| Fallback | `SimpleFallback` — `async def` 0건 | 없음 | 순수 동기 |
| Bulkhead | `Bulkhead(ABC)` → `SemaphoreBulkhead` | `AsyncSemaphoreBulkhead` (**별도 클래스**, Bulkhead 미상속) | **유일한 분리** |

7개 전환 대상 중 async를 지원하는 것은 **Bulkhead 하나**뿐이며,
그것도 `Bulkhead(ABC)`를 상속하지 않는 완전 독립 클래스(`async_semaphore.py`)이다.
하나의 Protocol에 `execute` + `execute_async`를 모두 넣으면 6개 구현체가
`execute_async`를 `NotImplementedError`로 두게 되어 Protocol의 의미가 퇴색된다.

따라서 동기/비동기 Protocol을 **분리**한다:

```python
from typing import Protocol, Callable, TypeVar, Any

T = TypeVar("T")


class ResiliencePolicy(Protocol[T]):
    """
    동기 resilience 패턴이 구현하는 핵심 Protocol.

    각 Policy는 함수를 래핑하여 resilience 로직을 적용한다.
    Policy 간 조합은 PolicyComposer가 담당한다.

    예외 처리 컨트랙트:
    - 모든 비즈니스 예외는 PolicyResult(outcome=FAILURE, error=e)에 포장하여 반환한다.
    - except Exception 패턴을 사용하여 KeyboardInterrupt/SystemExit은 통과시킨다.
      (Python에서 KeyboardInterrupt/SystemExit은 Exception이 아닌 BaseException
       직속 하위 클래스이므로 except Exception에 catch되지 않는다.)
    - 기존 코드 근거: RetryHandler.execute()가 except Exception으로 catch 후
      RetryResult(success=False)로 반환 (handler.py L492).
      SimpleFallback.execute()도 동일 (fallback_strategy.py L69-L113).
    - Bulkhead의 BulkheadFullError만 유일한 예외 기반 → Policy 래퍼에서
      catch하여 PolicyResult(outcome=REJECTED)로 변환.
    """

    @property
    def name(self) -> str:
        """Policy 식별자 (예: 'retry', 'circuit_breaker', 'bulkhead')."""
        ...

    def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        context: PolicyContext | None = None,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        함수를 Policy로 감싸서 실행.

        Args:
            func: 실행할 함수
            *args: 함수 위치 인자
            context: 실행 컨텍스트 (Guard/Hook/Sink에 전파)
            **kwargs: 함수 키워드 인자

        Returns:
            PolicyResult[T]: 통합 결과. 예외를 던지지 않는다.
        """
        ...


class AsyncResiliencePolicy(Protocol[T]):
    """
    비동기 resilience 패턴이 구현하는 Protocol.

    현재 해당하는 구현: AsyncSemaphoreBulkhead (async_semaphore.py).
    동일한 예외 처리 컨트랙트를 따른다.
    """

    @property
    def name(self) -> str:
        """Policy 식별자."""
        ...

    async def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        context: PolicyContext | None = None,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        비동기 함수를 Policy로 감싸서 실행.

        Returns:
            PolicyResult[T]: 통합 결과. 예외를 던지지 않는다.
        """
        ...
```

### 2.4 Guard — 사전 검증 훅

현재 `RetryHandler.execute()` 내부에 하드코딩된 pre-check 로직을 독립 Guard로 분리한다.

**Guard에 context가 필요한 코드 근거**:

| Guard 후보 | 인자 의존성 | 코드 근거 |
|-----------|-----------|----------|
| `KillSwitchGuard` | 인자 없음 — 전역 상태만 | `SystemControlManager().is_enabled()` 파라미터 없음 (`handler.py` L25-34) |
| `ErrorBudgetGuard` | `tier_id`, `region` 의존 | `ErrorBudgetGate.check(tier_id=, region=)` — tier_id에 따라 판정이 달라짐 (`gate.py` L228-249) |
| `RetryBudgetGuard` | 재시도 여부 의존 | `record_request(is_retry=(attempt > 1))` (`handler.py` L461-465) |

현재 `RetryHandler._check_error_budget_gate()`는 `check_automation_allowed()`를 **인자 없이** 호출하여
(`handler.py` L175) `tier_id`/`region` 정보를 전달하지 않는 상태이다.
이는 현재 코드의 미비점으로, `PolicyContext`를 통해 해소한다.

```python
class PolicyGuard(Protocol):
    """
    Policy 실행 전 사전 검증.

    현재 하드코딩 위치:
    - Kill Switch: handler.py L419 `_is_system_enabled()`
    - ErrorBudgetGate: handler.py L428 `_check_error_budget_gate()`

    context: PolicyContext | None 설계 원칙:
    - context가 None이면 전역 상태만 체크 (KillSwitchGuard)
    - context가 있으면 tier_id/region 등 활용 가능 (ErrorBudgetGuard)
    """

    @property
    def name(self) -> str:
        """Guard 식별자."""
        ...

    def check(self, context: PolicyContext | None = None) -> GuardResult:
        """
        실행 허용 여부 확인.

        Args:
            context: 실행 컨텍스트. None이면 전역 상태만 검증.

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

**context=None 기본 동작 (Null Safety)**:

Guard 구현 시 `context=None`에 대한 기본 동작을 반드시 정의해야 한다.
기존 코드가 이미 동일 패턴을 사용 중이다:

| Guard | context=None 시 기본 동작 | 코드 근거 |
|-------|-------------------------|----------|
| `KillSwitchGuard` | context 무시, 전역 체크 | `SystemControlManager().is_enabled()` — 인자 없음 |
| `ErrorBudgetGuard` | `tier_id=None` → **글로벌 판정** (티어 무관) | `ErrorBudgetGate.check(tier_id=None)` → 글로벌 캐시 키 사용 (`gate.py` L262) |
| `RetryBudgetGuard` | 기본 예산 기준으로 판정 | `should_allow_retry()` — 인자 없음 |

> **주의**: `tier_id=None`은 `"standard"`가 아니라 **글로벌**(티어 무관) 판정이다.
> 현재 `ErrorBudgetGate.check(tier_id=None)`이 글로벌 캐시 키로 판정하는 것과 일치.

```python
# ErrorBudgetGuard 구현 예시
class ErrorBudgetGuard:
    def check(self, context: PolicyContext | None = None) -> GuardResult:
        tier_id = context.tier_id if context else None   # None = 글로벌 판정
        region = context.region if context else None
        result = self._gate.check(tier_id=tier_id, region=region)
        return GuardResult(allowed=result.allowed, reason=result.reason)
```

**현재 코드에서의 Guard 후보**:

| Guard | 현재 위치 | 현재 사용처 |
|-------|-----------|-------------|
| `KillSwitchGuard` | `services/system_control.py` → `SystemControlManager.is_enabled()` | `handler.py` L419 `_is_system_enabled()` 내부에서 lazy import |
| `ErrorBudgetGuard` | `services/error_budget_gate/` → `check_automation_allowed()` | `handler.py` L157 `_check_error_budget_gate()` 내부에서 lazy import |
| `RetryBudgetGuard` | `services/backoff_calculator/` → `AdaptiveRetryBudget.should_allow_retry()` | `handler.py` L463 루프 내부 |

### 2.5 PolicyHook — 실행 이벤트 옵저버

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

### 2.6 FailureSink — 최종 실패 처리

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
        context: PolicyContext | None,
        policy_result: PolicyResult,
    ) -> str | None:
        """
        최종 실패 처리.

        Args:
            error: 최종 실패 예외
            context: PolicyContext (order_id, user_id 등 DLQ 저장에 필요)
            policy_result: 파이프라인 전체 결과

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
└── resilience_policy.py  # NEW: 아래 타입 모두 포함
    # - PolicyContext (frozen=True, Immutable 실행 컨텍스트)
    # - PolicyOutcome, PolicyResult (통합 결과 타입)
    # - ResiliencePolicy (동기 Protocol)
    # - AsyncResiliencePolicy (비동기 Protocol)
    # - PolicyGuard, GuardResult (사전 검증)
    # - PolicyHook (실행 이벤트 옵저버)
    # - FailureSink (최종 실패 처리)
```

**네이밍 충돌 검증**:

| 새 이름 | 기존 시스템 존재 여부 | 판정 |
|---------|---------------------|------|
| `PolicyContext` | 미존재 | ✅ 안전 — `*Context` 패턴 20건+ 존재 (`RequestContext`, `ActorContext` 등) |
| `PolicyResult` | 미존재 | ✅ 안전 |
| `PolicyOutcome` | 미존재 | ✅ 안전 |
| `ResiliencePolicy` | 미존재 | ✅ 안전 — `*Policy` 패턴 다수 (`NotificationPolicy`, `RollbackPolicy` 등) |
| `AsyncResiliencePolicy` | 미존재 | ✅ 안전 |
| `PolicyGuard` | 미존재 | ✅ 안전 — `*Guard` 패턴 11건 존재 (`SafetyGuard`, `AutoRollbackGuard` 등) |
| `GuardResult` | 미존재 | ✅ 안전 — `*Result` 패턴 다수 (`GateCheckResult`, `SafetyCheckResult` 등) |
| `PolicyHook` | 미존재 | ✅ 안전 — `HookInfo` (core/hooks.py)와 별개 |
| `FailureSink` | 미존재 | ✅ 안전 — `PostgreSQLSinkConfig/Consumer` (kafka_consumer.py)와 별개 |

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

## 7. 설계 약속 요약

논의를 통해 확정된 인터페이스 설계 약속:

### 7.1 Execution Context — 명시적 PolicyContext 전달

- `args/kwargs`에 암시적 포함이 **아닌**, `context: PolicyContext | None` 명시적 파라미터
- 기존 `RetryHandler.execute(context=dict)`, `AdaptiveThrottle.check(context=dict)` 패턴의 공식 타입 승격
- `frozen=True`로 파이프라인 내 사이드 이펙트 방지 (기존 `CachedMetrics` Copy-on-Write 선례)
- Guard/Hook/Sink 모두 동일한 `PolicyContext` 참조

### 7.2 예외 처리 — Swallow + Fatal Pass-through

- **강제 사항**: 모든 Policy는 비즈니스 예외를 `PolicyResult(outcome=FAILURE, error=e)`로 포장 반환
- `except Exception` 패턴으로 `KeyboardInterrupt`/`SystemExit` 자동 통과 (Python 언어 보장)
- Bulkhead의 `BulkheadFullError`만 유일한 예외 기반 → Policy 래퍼에서 catch → `PolicyResult(outcome=REJECTED)` 변환
- 기존 코드 5개 패턴 중 4개가 이미 swallow 패턴, Bulkhead만 예외 기반 (완전 호환)

### 7.3 Guard context 전달 — Optional PolicyContext

- `check(context: PolicyContext | None = None)` 서명으로 전역/컨텍스트 의존 Guard 모두 수용
- `context=None` 기본 동작: `tier_id=None` → 글로벌 판정 (기존 `ErrorBudgetGate.check(tier_id=None)` 동작 유지)
- `"standard"` 기본값이 **아닌** `None`(글로벌)이 현재 코드와 일관된 기본 동작

### 7.4 Sync/Async 분리 — 별도 Protocol

- `ResiliencePolicy` (동기) / `AsyncResiliencePolicy` (비동기) 분리
- 기존 Bulkhead의 `Bulkhead(ABC)` + `AsyncSemaphoreBulkhead`(별도 클래스) 선례 일치
- Composer 타입 체크는 231번 문서에서 정의 → `compose()` / `compose_async()` 시그니처 분리
