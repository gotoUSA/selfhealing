# 231. PolicyComposer 조합 엔진 설계

## 1. 개요

PolicyComposer는 Policy Composition 전환의 **최종 통합 레이어**이다.
226~230번 문서에서 설계한 개별 Policy들을 **선언적으로 조합**하여 실행하는 엔진이다.

참조 구현:
- **resilience4j**: `Decorators.ofSupplier(supplier).withRetry(...).withCircuitBreaker(...).get()`
- **Polly**: `Policy.WrapAsync(retryPolicy, circuitBreakerPolicy, bulkheadPolicy)`
- **Microsoft.Extensions.Http.Resilience**: `builder.AddStandardResilienceHandler()`

## 2. 현재 상태 — 조합이 불가능한 이유

### 2.1 Shopping App의 위치 — 테스트베드

> **전제**: Shopping 앱은 selfhealing 시스템의 **테스트베드(testbed)**이다.
> 최종 목표는 Shopping 앱 전환이 아니라, **임의의 외부 앱이 selfhealing을 pip install하여
> 선언적으로 resilience 패턴을 조합할 수 있는 프레임워크**를 제공하는 것이다.
> Shopping은 해당 프레임워크의 통합 검증 환경으로만 사용된다.

테스트베드(Shopping)에서 확인된 소비자 관점 문제점:

| 패턴 | Shopping에서 사용 | 방식 | 문제점 |
|------|------------------|------|--------|
| Circuit Breaker | ✅ | `ProviderRegistry.get_circuit_breaker_repo()` 직접 접근 | Policy API 아닌 Repository 직접 조작 |
| Retry | ⚠️ tenacity/Celery | selfhealing `RetryHandler` 미사용 | selfhealing Retry가 조합 불가하여 외부 라이브러리 사용 |
| DLQ/Replay | ✅ | `ReplayHandler` 상속 구현 | 정상 — 인프라 레이어 사용 |
| Hedging | ❌ | 미사용 | 조합 불가로 채택 자체를 안 함 |
| Fallback | ❌ | 미사용 | 동일 |
| Bulkhead | ❌ | 미사용 | 동일 |

**테스트베드에서 검증된 근본 원인**: 패턴 조합이 하드코딩이라 소비자가 "Retry + CB + Fallback"을 선언적으로 사용할 수 없다.
이 문제는 Shopping 특유의 것이 아니라, selfhealing을 사용하는 **모든 외부 앱**에서 동일하게 발생한다.

### 2.2 현재 수동 조합의 한계

`shopping/tasks/payment_recovery_tasks.py`의 `retry_failed_payment` 태스크:

```python
# 현재: 소비자가 수동으로 패턴 조합을 구현
class PaymentRecoveryService:
    def check_circuit_breaker(self, gateway):     # CB 직접 체크
        repo = ProviderRegistry.get_circuit_breaker_repo()
        state = repo.get_state(gateway)
        return state != "open"

    def record_circuit_breaker_result(self, gateway, success):  # CB 결과 기록
        repo = ProviderRegistry.get_circuit_breaker_repo()
        if success:
            repo.record_success(gateway)
        else:
            repo.record_failure(gateway)

# Celery task에서 수동 조합:
@shared_task(bind=True, max_retries=3, retry_backoff=True)
def retry_failed_payment(self, payment_id):
    recovery = PaymentRecoveryService()

    if recovery.is_circuit_breaker_blocking("payment_api"):  # Step 1: CB 체크
        return {"status": "circuit_open"}

    try:
        result = process_payment(payment_id)                  # Step 2: 실행
        recovery.record_circuit_breaker_result("payment_api", True)
        return result
    except Exception as e:
        recovery.record_circuit_breaker_result("payment_api", False)
        self.retry(exc=e)                                     # Step 3: Celery retry
```

**문제점**: CB 체크 → 실행 → CB 기록 → Retry를 소비자가 직접 작성.
각 조합마다 보일러플레이트가 반복되며, 패턴 추가(Bulkhead, Fallback) 시 코드가 기하급수적으로 복잡해진다.

## 3. PolicyComposer 설계

### 3.1 핵심 인터페이스

```python
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)


class PolicyComposer(Generic[T]):
    """
    Policy 조합 엔진.

    여러 ResiliencePolicy를 선언적으로 조합하여 단일 실행 파이프라인으로 구성한다.
    Guard/Hook/Sink를 연결하여 인프라 레이어와 통합한다.

    실행 순서:
    1. Guards 검증 (Kill Switch, ErrorBudgetGate 등)
    2. Policies 순차 래핑 (바깥 → 안쪽 순으로 실행)
    3. Hooks 호출 (Audit, Metrics 등)
    4. 실패 시 Sink 처리 (DLQ 등)
    """

    def __init__(self):
        self._policies: list[ResiliencePolicy] = []
        self._guards: list[PolicyGuard] = []
        self._hooks: list[PolicyHook] = []
        self._sinks: list[FailureSink] = []

    # === Builder API ===

    def add(self, policy: ResiliencePolicy) -> PolicyComposer[T]:
        """Policy 추가. 추가 순서가 바깥→안쪽 실행 순서."""
        self._policies.append(policy)
        return self

    def add_guard(self, guard: PolicyGuard) -> PolicyComposer[T]:
        """Guard 추가. 모든 Policy 실행 전 검증."""
        self._guards.append(guard)
        return self

    def add_hook(self, hook: PolicyHook) -> PolicyComposer[T]:
        """Hook 추가. Policy 실행 이벤트 관찰."""
        self._hooks.append(hook)
        return self

    def add_sink(self, sink: FailureSink) -> PolicyComposer[T]:
        """FailureSink 추가. 최종 실패 처리."""
        self._sinks.append(sink)
        return self

    # === Execution ===

    def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        context: PolicyContext | None = None,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        조합된 Policy 파이프라인 실행.

        실행 흐름:
        1. Guard 검증 → 하나라도 거부 시 REJECTED
        2. Policy 체인 실행 (바깥→안쪽 중첩)
        3. Hook 호출 (on_success / on_failure / on_reject)
           — **파이프라인 전체(End-to-End)** 결과만 관찰한다.
             개별 Policy 내부 이벤트(예: Retry 각 시도)는 Policy 자체가 처리하며,
             Composer Hook에는 전파하지 않는다. (2계층 Hook 구조)
        4. 실패 시 Sink 처리
           — **동기적(Blocking)** 으로 수행한다.
             기존 RetryHandler._move_to_dlq() (handler.py L567)가 동기 Blocking이었으며,
             FailureSink Protocol (interfaces/resilience_policy.py L333)도 동기 시그니처이다.
             DLQ 저장은 로컬 DB(Django ORM) write이므로 수 ms 수준으로 완료된다.

        Args:
            func: 실행할 함수
            *args: 함수 위치 인자
            context: 실행 컨텍스트 (Guard/Hook/Sink에 전파).
                     PolicyContext(frozen=True)로 파이프라인 내 사이드 이펙트를 방지한다.
                     None이면 Guard는 전역 상태만 체크하고, Sink는 비즈니스 식별자 없이 저장한다.
            **kwargs: 함수 키워드 인자

        Returns:
            PolicyResult[T]: 통합 결과. 예외를 던지지 않는다.
        """
        start_time = time.monotonic()

        # Step 1: Guard 검증 — context를 전달하여 tier_id/region 기반 판정 지원
        for guard in self._guards:
            try:
                result = guard.check(context=context)
                if not result.allowed:
                    self._notify_hooks_reject(guard.name, result.reason)
                    return PolicyResult(
                        value=None,
                        outcome=PolicyOutcome.REJECTED,
                        metadata={
                            "rejected_by": guard.name,
                            "reason": result.reason,
                        },
                    )
            except Exception as e:
                # Fail-Open: Guard 실패 시 통과 허용
                logger.warning(f"Guard '{guard.name}' failed (fail-open): {e}")

        # Step 2: Policy 체인 구성 (중첩 실행) — context 전파
        result = self._execute_policy_chain(func, *args, context=context, **kwargs)

        # Step 3: Hook 호출 — 파이프라인 전체 결과에 대해서만 호출 (2계층 구조)
        duration_ms = (time.monotonic() - start_time) * 1000
        result.total_duration_ms = duration_ms

        if result.success:
            self._notify_hooks_success(result)
        else:
            self._notify_hooks_failure(result)

            # Step 4: Sink 처리 — 동기 Blocking (FailureSink Protocol 준수)
            if result.outcome == PolicyOutcome.FAILURE:
                self._process_sinks(result, context, args, kwargs)

        return result
```

### 3.2 Policy 체인 실행 메커니즘

Policy 조합의 핵심은 **중첩 래핑(Nesting)** 방식이다.
`policies = [RetryPolicy, CircuitBreakerPolicy, BulkheadPolicy]` 순서로 추가하면:

```
RetryPolicy.execute(
    CircuitBreakerPolicy.execute(
        BulkheadPolicy.execute(
            func()
        )
    )
)
```

즉 RetryPolicy가 **가장 바깥**, BulkheadPolicy가 **가장 안쪽**에서 실행된다.

```python
def _execute_policy_chain(
    self,
    func: Callable[..., T],
    *args: Any,
    context: PolicyContext | None = None,
    **kwargs: Any,
) -> PolicyResult[T]:
    """
    Policy 체인을 중첩 실행.

    policies = [P1, P2, P3]일 때:
    P1.execute(lambda: P2.execute(lambda: P3.execute(func)))

    FallbackPolicy는 특별 처리:
    - 이전 결과가 실패일 때 _apply_fallback(original_error)를 호출한다.
    - execute()를 호출하지 않는다 (func 중복 실행 방지).
    - _apply_fallback()은 PolicyComposer 전용 내부 API이며,
      일반 소비자는 execute()를 통해 FallbackPolicy를 단독 사용한다.

    229번 문서 §3.1-3.2 확정 사항:
    - execute(): 단독 사용 — func 실행 후 실패 시 _apply_fallback 위임
    - _apply_fallback(): Composer 전용 — func 재실행 없이 Fallback만 시도
    """
    if not self._policies:
        # Policy 없음 → 직접 실행
        try:
            value = func(*args, **kwargs)
            return PolicyResult(value=value, outcome=PolicyOutcome.SUCCESS)
        except Exception as e:
            return PolicyResult(value=None, outcome=PolicyOutcome.FAILURE, error=e)

    # 중첩 함수 구성 (역순으로 감싸기)
    wrapped = lambda: func(*args, **kwargs)
    executed_policies: list[str] = []

    for policy in reversed(self._policies):
        outer_fn = wrapped
        current_policy = policy

        if isinstance(current_policy, FallbackPolicy):
            # FallbackPolicy는 _apply_fallback() 기반 조건부 래퍼
            #
            # 중복 실행 방지 (229번 문서 §3.2 확정):
            # - AS-IS: fb.execute(lambda: inner()) → inner()를 2회 실행 (버그)
            # - TO-BE: fb._apply_fallback(original_error=e) → func 미실행, 대체 값만 산출
            #
            # _apply_fallback()은 PolicyComposer 전용 내부 API이다.
            # 일반 소비자(외부 앱)는 이 메서드를 직접 호출하지 않는다.
            # 단독 사용 시에는 FallbackPolicy.execute(func)를 호출한다.
            def fallback_wrapper(inner=outer_fn, fb=current_policy):
                try:
                    value = inner()  # 내부 체인 실행 (1회만)
                    return value
                except Exception as e:
                    # 실패 감지 → predicate 확인 → _apply_fallback 직접 호출
                    result = PolicyResult(
                        value=None, outcome=PolicyOutcome.FAILURE, error=e
                    )
                    if fb._predicate(result):
                        fb_result = fb._apply_fallback(
                            original_error=e,
                            context=context,
                        )
                        if fb_result.success:
                            return fb_result.value
                    # Fallback 미적용 또는 실패 → 원본 예외 전파
                    raise e

            wrapped = fallback_wrapper
        else:
            # 일반 Policy: execute()로 래핑 — context 전파
            def policy_wrapper(inner=outer_fn, p=current_policy):
                result = p.execute(inner, context=context)
                if result.success:
                    return result.value
                elif result.error:
                    # raise result.error 패턴 유지 (리뷰 Q6 확정):
                    # - 기존 코드베이스 전체 raise...from 0건
                    # - except as e 캡처 시 __traceback__ 속성이 유지됨
                    # - traceback.print_exception(result.error)로 원본 위치 확인 가능
                    raise result.error
                else:
                    raise PolicyRejectedException(
                        f"Policy '{p.name}' rejected: {result.outcome}"
                    )

            wrapped = policy_wrapper

        executed_policies.append(current_policy.name)

    # 최종 실행
    try:
        value = wrapped()
        return PolicyResult(
            value=value,
            outcome=PolicyOutcome.SUCCESS,
            executed_policies=list(reversed(executed_policies)),
        )
    except PolicyRejectedException as e:
        return PolicyResult(
            value=None,
            outcome=PolicyOutcome.REJECTED,
            error=e,
            executed_policies=list(reversed(executed_policies)),
        )
    except Exception as e:
        return PolicyResult(
            value=None,
            outcome=PolicyOutcome.FAILURE,
            error=e,
            executed_policies=list(reversed(executed_policies)),
        )
```

**FallbackPolicy 실행 흐름 비교** (229번 문서 §3.2 확정):

| 사용 모드 | 호출 경로 | func 실행 횟수 |
|-----------|---------|---------------|
| 단독 사용 | `FallbackPolicy.execute(func)` → func() → 실패 시 `_apply_fallback()` | 1회 |
| Composer 체인 | Composer → inner() 실행 → 실패 감지 → `_apply_fallback()` 직접 호출 | 1회 (내부 체인) |
| ~~기존 설계 (삭제)~~ | ~~Composer → inner() 실행 → `execute(lambda: inner())` → inner() 재실행~~ | ~~2회 (중복)~~ |

### 3.3 `compose()` 편의 함수

```python
def compose(*policies: ResiliencePolicy) -> PolicyComposer:
    """
    Policy를 선언적으로 조합하는 편의 함수.

    policies 순서 = 바깥→안쪽 실행 순서:
    - compose(Retry, CB, Bulkhead).execute(func)
    - = Retry(CB(Bulkhead(func)))

    Usage:
        result = compose(
            RetryPolicy(max_retries=3),
            CircuitBreakerPolicy(service_name="payment"),
            BulkheadPolicy(bulkhead=semaphore),
            FallbackPolicy(default_value={"status": "degraded"}),
        ).execute(lambda: call_payment_api())
    """
    composer = PolicyComposer()
    for policy in policies:
        composer.add(policy)
    return composer
```

### 3.4 Sync/Async 타입 안전성 — Composer 분리

225번 문서에서 `ResiliencePolicy`(동기)와 `AsyncResiliencePolicy`(비동기) Protocol을 분리했다.
이에 따라 Composer도 동기/비동기를 분리하여, **타입 수준에서 혼용을 차단**한다.

**기존 코드 근거** — Bulkhead의 동기/비동기 완전 분리 선례:

| 동기 | 비동기 | 관계 |
|------|--------|------|
| `Bulkhead(ABC)` → `SemaphoreBulkhead` (`bulkhead/base.py`) | `AsyncSemaphoreBulkhead` (`bulkhead/async_semaphore.py`) | **별도 클래스, 상속 없음** |

동일 원리로:

```python
# 동기 Composer — ResiliencePolicy만 허용
class PolicyComposer(Generic[T]):
    """동기 Policy 조합 엔진."""

    def add(self, policy: ResiliencePolicy) -> PolicyComposer[T]:
        """ResiliencePolicy(동기)만 추가 가능."""
        self._policies.append(policy)
        return self

    def execute(self, func: Callable[..., T], *args,
                context: PolicyContext | None = None, **kwargs) -> PolicyResult[T]:
        ...


# 비동기 Composer — AsyncResiliencePolicy만 허용
class AsyncPolicyComposer(Generic[T]):
    """비동기 Policy 조합 엔진."""

    def add(self, policy: AsyncResiliencePolicy) -> AsyncPolicyComposer[T]:
        """AsyncResiliencePolicy(비동기)만 추가 가능."""
        self._policies.append(policy)
        return self

    async def execute(self, func: Callable[..., T], *args,
                      context: PolicyContext | None = None, **kwargs) -> PolicyResult[T]:
        ...


# 편의 함수도 분리
def compose(*policies: ResiliencePolicy) -> PolicyComposer:
    """동기 Policy 조합."""
    composer = PolicyComposer()
    for policy in policies:
        composer.add(policy)
    return composer


def compose_async(*policies: AsyncResiliencePolicy) -> AsyncPolicyComposer:
    """비동기 Policy 조합."""
    composer = AsyncPolicyComposer()
    for policy in policies:
        composer.add(policy)
    return composer
```

**타입 안전성 효과**:

```python
# ✅ 정상 — 동기 Policy끼리 조합
compose(RetryPolicy(...), CircuitBreakerPolicy(...), BulkheadPolicy(...))

# ✅ 정상 — 비동기 Policy끼리 조합
await compose_async(AsyncBulkheadPolicy(...)).execute(async_func)

# ❌ Mypy 타입 에러 — 동기 Composer에 비동기 Policy 혼용
compose(RetryPolicy(...), AsyncBulkheadPolicy(...))
# error: Argument 2 to "compose" has incompatible type "AsyncBulkheadPolicy";
#        expected "ResiliencePolicy[T]"

# ❌ 런타임 에러 — 타입 힌트 무시 시 방어
class PolicyComposer:
    def add(self, policy: ResiliencePolicy) -> PolicyComposer[T]:
        if isinstance(policy, AsyncResiliencePolicy) and not isinstance(policy, ResiliencePolicy):
            raise TypeError(
                f"Cannot add async policy '{policy.name}' to sync PolicyComposer. "
                f"Use AsyncPolicyComposer or compose_async() instead."
            )
        self._policies.append(policy)
        return self
```

> **참고**: Mypy 정적 분석으로 컴파일 타임에 잡히는 것이 이상적이며,
> 런타임 `isinstance` 체크는 타입 힌트를 무시하는 환경을 위한 추가 방어선이다.

**확정 사항** (리뷰 Q4):

- **어댑터 제공 없음**: 동기 → 비동기 자동 래핑 어댑터를 제공하지 않는다.
- **엄격한 타입 분리**: 동기 `PolicyComposer`에 `AsyncResiliencePolicy`를 추가하면 Mypy 타입 에러 + 런타임 `TypeError`.
- **소비자 책임**: 비동기 파이프라인에서 동기 함수를 사용하려면 소비자가 `asyncio.to_thread()`로 직접 래핑하여 주입한다.
- **코드 근거**: 기존 코드베이스 3건 선례 — `SemaphoreBulkhead`/`AsyncSemaphoreBulkhead`, `BulkheadPolicy`/`AsyncBulkheadPolicy`, `HedgingStrategy`/`AsyncHedgingStrategy` 모두 어댑터 없이 별도 클래스로 완전 분리 (`bulkhead/base.py`, `bulkhead/async_semaphore.py`, `bulkhead/policy.py` L49/L174, `core/hedging/strategy.py`/`async_strategy.py`).

## 4. Guard / Hook / Sink 통합

### 4.1 Guard — 사전 검증

현재 `RetryHandler.execute()`에 하드코딩된 pre-check를 Guard로 분리:

```python
class KillSwitchGuard(PolicyGuard):
    """Kill Switch 가드 — SystemControlManager 래핑."""

    @property
    def name(self) -> str:
        return "kill_switch"

    def check(self, context: PolicyContext | None = None) -> GuardResult:
        """Kill Switch는 전역 상태만 체크 — context 무시."""
        try:
            from selfhealing.services.system_control import get_system_control_manager
            mgr = get_system_control_manager()
            if not mgr.is_enabled():
                return GuardResult(
                    allowed=False,
                    reason="System kill switch is disabled",
                )
        except ImportError:
            pass
        return GuardResult(allowed=True)


class ErrorBudgetGuard(PolicyGuard):
    """ErrorBudgetGate 가드."""

    @property
    def name(self) -> str:
        return "error_budget_gate"

    def check(self, context: PolicyContext | None = None) -> GuardResult:
        """
        context.tier_id/region 기반 판정.
        context=None이면 글로벌 판정 (tier_id=None → 전역 에러 버짓).
        코드 근거: ErrorBudgetGate.check(tier_id=None) → 글로벌 캐시 키 사용 (gate.py L262)
        """
        try:
            from selfhealing.services.error_budget_gate import check_automation_allowed
            allowed, reason = check_automation_allowed()
            return GuardResult(allowed=allowed, reason=reason)
        except ImportError:
            return GuardResult(allowed=True)
```

**현재 코드 근거**: `handler.py` L419 `_is_system_enabled()`, L428 `_check_error_budget_gate()`

### 4.2 Hook — 실행 이벤트 관찰

```python
class AuditHook(PolicyHook):
    """감사 로깅 훅 — audit_helpers 래핑."""

    def on_success(self, policy_name: str, result: PolicyResult) -> None:
        try:
            from selfhealing.services import audit_helpers
            audit_helpers.log_policy_success(
                policy_name=policy_name,
                attempts=result.total_attempts,
                duration_ms=result.total_duration_ms,
            )
        except ImportError:
            pass

    def on_failure(self, policy_name: str, error: Exception, attempt: int) -> None:
        try:
            from selfhealing.services import audit_helpers
            audit_helpers.log_policy_failure(
                policy_name=policy_name,
                error=str(error),
                attempt=attempt,
            )
        except ImportError:
            pass


class MetricsHook(PolicyHook):
    """Prometheus 메트릭 훅."""

    def on_success(self, policy_name: str, result: PolicyResult) -> None:
        try:
            from prometheus_client import Counter, Histogram
            # 메트릭 기록
        except ImportError:
            pass

    def on_reject(self, policy_name: str, reason: str) -> None:
        try:
            from prometheus_client import Counter
            # 거부 메트릭 기록
        except ImportError:
            pass
```

**현재 코드 근거**: `handler.py` L138 `_log_retry_audit()`, L290 `_record_critical_tier_grace_metric()`

### 4.3 Sink — 최종 실패 처리

```python
class DLQSink(FailureSink):
    """DLQ 실패 처리 — dlq_service 래핑.

    FailureSink Protocol (interfaces/resilience_policy.py L333) 구현.
    동기적(Blocking)으로 DLQ에 저장한다.
    """

    def handle_failure(
        self,
        error: Exception,
        context: PolicyContext | None,
        policy_result: PolicyResult,
    ) -> str | None:
        """
        최종 실패를 DLQ에 저장.

        Args:
            error: 최종 실패 예외
            context: PolicyContext (order_id, user_id 등 비즈니스 식별자).
                     None이면 식별자 없이 저장한다.
            policy_result: 파이프라인 전체 결과

        Returns:
            DLQ 레코드 ID 문자열, 또는 None
        """
        if not policy_result.metadata.get("should_dlq", False):
            return None

        try:
            from selfhealing.services.dlq import store_to_dlq

            domain = policy_result.metadata.get("domain", "default")
            order_id = context.order_id if context else None
            user_id = context.extra.get("user_id") if context and context.extra else None

            result = store_to_dlq(
                domain=domain,
                failure_type=f"MAX_RETRIES_{type(error).__name__.upper()}",
                entity_id=order_id,
                user_id=int(user_id) if user_id is not None else None,
                error_code=type(error).__name__,
                error_message=str(error)[:1000] if error else "",
                metadata={
                    "executed_policies": policy_result.executed_policies,
                    "total_attempts": policy_result.total_attempts,
                },
            )
            return str(result) if result is not None else None
        except ImportError:
            logger.warning("DLQ service not available")
            return None
```

**현재 코드 근거**: `handler.py` L587 `_move_to_dlq()`

## 5. 실행 순서 — 권장 조합 패턴

### 5.1 표준 조합 순서

resilience4j / Polly의 권장 순서를 따르며, 현재 `RetryHandler.execute()`의 고정 순서와 호환:

```
가장 바깥 (먼저 실행)
┌──────────────────────┐
│  Guard: Kill Switch   │  ← 전역 차단
├──────────────────────┤
│  Guard: ErrorBudget   │  ← 에러 버짓 검증
├──────────────────────┤
│  Policy: Retry        │  ← 재시도 (가장 바깥)
├──────────────────────┤
│  Policy: CircuitBreaker│ ← CB 상태 체크
├──────────────────────┤
│  Policy: Bulkhead     │  ← 리소스 격리
├──────────────────────┤
│  Policy: Timeout      │  ← 시간 제한
├──────────────────────┤
│  Policy: Hedging      │  ← 병렬 경쟁 (선택)
├──────────────────────┤
│  func()               │  ← 비즈니스 로직
├──────────────────────┤
│  Policy: Fallback     │  ← 실패 시 대체 (특수 처리)
├──────────────────────┤
│  Sink: DLQ            │  ← 최종 실패 저장
└──────────────────────┘
가장 안쪽 (마지막 실행)
```

이 순서의 의미:
- **Retry가 가장 바깥**: CB Open, Bulkhead Full, Timeout 모두 Retry 대상
- **CB가 Retry 안쪽**: Retry 시도마다 CB 상태를 재체크
- **Bulkhead가 CB 안쪽**: CB가 허용한 요청만 Bulkhead 슬롯 소비
- **Fallback이 특수 처리**: 전체 파이프라인 실패 후 활성화

### 5.2 현재 RetryHandler.execute() 고정 순서와의 매핑

| 현재 고정 순서 (handler.py) | PolicyComposer 매핑 |
|---------------------------|-------------------|
| L419: Kill Switch 체크 | `add_guard(KillSwitchGuard())` |
| L428: ErrorBudgetGate 체크 | `add_guard(ErrorBudgetGuard())` |
| L456-L535: while retry 루프 | `add(RetryPolicy(max_retries=N))` |
| L463: AdaptiveRetryBudget | RetryPolicy 내부 로직으로 유지 |
| L468: RateLimit 대기 | RetryPolicy 내부 로직으로 유지 (또는 Hook) |
| L479: Audit 기록 | `add_hook(AuditHook())` |
| L505: Throttle 감지 | RetryPolicy 내부 로직으로 유지 |
| L552: DLQ 이동 | `add_sink(DLQSink())` |

## 6. Before/After 적용 예시

> 아래 예시는 **테스트베드(Shopping)와 가상의 외부 앱** 모두를 포함한다.
> Shopping 예시는 기존 코드 대비 변환 효과를 보여주기 위한 것이며,
> 외부 앱 예시는 신규 도입자 관점의 사용법을 보여준다.

### 6.1 테스트베드 검증 — 결제 복구 태스크 (Shopping)

```python
# === BEFORE (현재) — shopping/tasks/payment_recovery_tasks.py ===
# Shopping은 테스트베드로서, selfhealing의 조합 불가 문제를 가장 먼저 드러냈다.
@shared_task(bind=True, max_retries=3, retry_backoff=True)
def retry_failed_payment(self, payment_id):
    recovery = PaymentRecoveryService()

    # Step 1: CB 수동 체크
    if recovery.is_circuit_breaker_blocking("payment_api"):
        return {"status": "circuit_open"}

    try:
        # Step 2: 실행
        result = process_payment(payment_id)
        # Step 3: CB 성공 기록
        recovery.record_circuit_breaker_result("payment_api", True)
        return result
    except Exception as e:
        # Step 4: CB 실패 기록
        recovery.record_circuit_breaker_result("payment_api", False)
        # Step 5: Celery retry
        self.retry(exc=e)


# === AFTER (Policy Composition) ===
from selfhealing.resilience.policies import (
    compose, RetryPolicy, CircuitBreakerPolicy, FallbackPolicy,
    KillSwitchGuard, ErrorBudgetGuard, AuditHook, DLQSink,
)

payment_pipeline = (
    compose(
        RetryPolicy(max_retries=3, backoff="exponential"),
        CircuitBreakerPolicy(service_name="payment_api"),
    )
    .add_guard(KillSwitchGuard())
    .add_guard(ErrorBudgetGuard())
    .add_hook(AuditHook())
    .add_sink(DLQSink())
)

@shared_task
def retry_failed_payment(payment_id):
    result = payment_pipeline.execute(
        lambda: process_payment(payment_id)
    )

    if result.success:
        return {"status": "success", "value": result.value}
    elif result.rejected:
        return {"status": "rejected", "reason": result.metadata.get("rejected_by")}
    else:
        return {"status": "failed", "error": str(result.error)}
```

### 6.2 외부 앱 예시 — FastAPI 마이크로서비스

selfhealing을 **신규 도입**하는 외부 앱(비-Shopping)의 사용 패턴:

```python
# === 외부 앱: order-service (FastAPI) ===
# pip install selfhealing

from selfhealing.resilience.policies import (
    compose, RetryPolicy, CircuitBreakerPolicy,
    BulkheadPolicy, TimeoutPolicy, FallbackPolicy,
)
from selfhealing.resilience.bulkhead import SemaphoreBulkhead

# 1. Policy 파이프라인 정의 (앱 시작 시 1회)
inventory_pipeline = compose(
    RetryPolicy(max_retries=2, backoff="exponential"),
    CircuitBreakerPolicy(service_name="inventory_api"),
    BulkheadPolicy(bulkhead=SemaphoreBulkhead("inventory", max_concurrent=10)),
    TimeoutPolicy(timeout_ms=3000),
    FallbackPolicy(default_value={"stock": 0, "available": False}),
)

# 2. 엔드포인트에서 사용
@app.post("/orders")
async def create_order(order: OrderRequest):
    # inventory 조회 — Retry+CB+Bulkhead+Timeout+Fallback 자동 적용
    stock_result = await inventory_pipeline.execute_async(
        lambda: http_client.get(f"http://inventory-svc/stock/{order.product_id}")
    )

    if stock_result.outcome == PolicyOutcome.SUCCESS_WITH_FALLBACK:
        # Fallback 응답 (재고 0) — 주문 보류 처리
        return {"status": "pending", "reason": "inventory_check_degraded"}

    if not stock_result.success:
        raise HTTPException(status_code=503, detail="Inventory service unavailable")

    # 정상 재고 응답으로 주문 진행
    return process_order(order, stock_result.value)
```

**외부 앱이 신경 쓸 것**: Policy 조합 선언 + `execute()` 호출 + 결과 처리.
**외부 앱이 신경 쓰지 않아도 되는 것**: CB 상태 관리, Retry 루프, Bulkhead 슬롯, Timeout 타이머.

### 6.3 외부 앱 예시 — Django 배치 서비스

```python
# === 외부 앱: report-service (Django + Celery) ===
from selfhealing.resilience.policies import compose, RetryPolicy, CircuitBreakerPolicy
from selfhealing.resilience.policies.sinks import DLQSink

report_pipeline = (
    compose(
        RetryPolicy(max_retries=5, backoff="exponential"),
        CircuitBreakerPolicy(service_name="analytics_db"),
    )
    .add_sink(DLQSink())  # 실패 시 DLQ로 자동 저장
)

@shared_task
def generate_daily_report(date_str):
    result = report_pipeline.execute(
        lambda: analytics_db.query_daily_summary(date_str)
    )
    if result.success:
        store_report(result.value)
    # 실패 시 DLQSink가 자동으로 DLQ에 저장 — 소비자 코드에 DLQ 로직 불필요
```

### 6.4 고가용성 — Hedging + Fallback 조합

```python
# 어떤 앱이든 동일한 패턴으로 고가용성 파이프라인 구성 가능
from selfhealing.resilience.policies import (
    compose, RetryPolicy, CircuitBreakerPolicy,
    HedgingPolicy, FallbackPolicy, TimeoutPolicy,
)
from selfhealing.core.hedging.config import HedgingConfig, HedgingMode

ha_pipeline = compose(
    RetryPolicy(max_retries=2),
    CircuitBreakerPolicy(service_name="product_api"),
    HedgingPolicy(
        candidates=[fetch_from_region_b, fetch_from_region_c],
        config=HedgingConfig(mode=HedgingMode.DELAYED, delay=0.1),
    ),
    FallbackPolicy(
        fallback_chain=[
            lambda: redis_cache.get("product:123"),
            lambda: local_cache.get("product:123"),
        ],
        default_value={"status": "temporarily_unavailable"},
    ),
)

result = ha_pipeline.execute(lambda: fetch_from_region_a(product_id=123))
```

## 7. 엔터프라이즈 유연성

### 7.1 프리셋 파이프라인

엔터프라이즈 고객에게 **사전 정의된 파이프라인**을 제공:

```python
# resilience/policies/presets.py

def standard_pipeline(
    service_name: str,
    max_retries: int = 3,
    timeout_ms: int = 5000,
) -> PolicyComposer:
    """표준 resilience 파이프라인."""
    return (
        compose(
            RetryPolicy(max_retries=max_retries),
            CircuitBreakerPolicy(service_name=service_name),
            TimeoutPolicy(timeout_ms=timeout_ms),
        )
        .add_guard(KillSwitchGuard())
        .add_hook(AuditHook())
        .add_sink(DLQSink())
    )


def ha_pipeline(
    service_name: str,
    candidates: list[Callable],
    max_retries: int = 2,
    timeout_ms: int = 3000,
    hedging_delay: float = 0.1,
) -> PolicyComposer:
    """고가용성 파이프라인 (Hedging 포함)."""
    return (
        compose(
            RetryPolicy(max_retries=max_retries),
            CircuitBreakerPolicy(service_name=service_name),
            BulkheadPolicy(
                bulkhead=SemaphoreBulkhead(f"{service_name}_bulkhead", max_concurrent=20),
            ),
            HedgingPolicy(
                candidates=candidates,
                config=HedgingConfig(
                    mode=HedgingMode.DELAYED,
                    delay=hedging_delay,
                ),
            ),
            TimeoutPolicy(timeout_ms=timeout_ms),
        )
        .add_guard(KillSwitchGuard())
        .add_guard(ErrorBudgetGuard())
        .add_hook(AuditHook())
        .add_hook(MetricsHook())
        .add_sink(DLQSink())
    )


# 사용
result = standard_pipeline("payment_api").execute(lambda: call_payment())
result = ha_pipeline("product_api", [fetch_b, fetch_c]).execute(lambda: fetch_a())
```

### 7.2 커스텀 조합 — Policy 선택적 사용

엔터프라이즈 고객이 필요한 패턴만 **선택적으로** 사용:

```python
# Retry만 (CB 없이)
result = compose(
    RetryPolicy(max_retries=5),
).execute(func)

# CB + Fallback만 (Retry 없이)
result = compose(
    CircuitBreakerPolicy(service_name="api"),
    FallbackPolicy(default_value=cached_data),
).execute(func)

# Bulkhead + Timeout만 (Retry/CB 없이)
result = compose(
    BulkheadPolicy(bulkhead=semaphore),
    TimeoutPolicy(timeout_ms=1000),
).execute(func)

# Guard만 (Policy 없이) — 단순 게이트 체크
result = (
    PolicyComposer()
    .add_guard(KillSwitchGuard())
    .add_guard(ErrorBudgetGuard())
).execute(func)
```

### 7.3 런타임 설정 변경

```python
# PolicyComposer는 불변(immutable) — 변경 시 새 인스턴스 생성
base = compose(
    RetryPolicy(max_retries=3),
    CircuitBreakerPolicy(service_name="api"),
)

# 운영 중 Fallback 추가
with_fallback = compose(
    RetryPolicy(max_retries=3),
    CircuitBreakerPolicy(service_name="api"),
    FallbackPolicy(default_value={"degraded": True}),
)

# 부하 시 Bulkhead 추가
under_load = compose(
    RetryPolicy(max_retries=1),  # 재시도 줄임
    CircuitBreakerPolicy(service_name="api"),
    BulkheadPolicy(bulkhead=SemaphoreBulkhead("api", max_concurrent=5)),
)
```

## 8. Async 지원

### 8.1 execute_async()

```python
class PolicyComposer(Generic[T]):
    async def execute_async(
        self,
        func: Callable[..., Awaitable[T]],
        *args: Any,
        context: PolicyContext | None = None,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        비동기 Policy 파이프라인 실행.

        각 Policy의 execute_async()를 호출한다.
        execute_async()가 없는 Policy는 execute()를 fallback 호출한다.

        Guard/Hook: 동기 (Guard는 빠른 체크, Hook은 관찰 전용)
        Sink: 동기 Blocking (FailureSink Protocol 준수)
        """
        start_time = time.monotonic()

        # Guard 검증 (동기 — Guard는 빠른 체크) — context 전달
        for guard in self._guards:
            try:
                result = guard.check(context=context)
                if not result.allowed:
                    return PolicyResult(
                        value=None,
                        outcome=PolicyOutcome.REJECTED,
                        metadata={"rejected_by": guard.name},
                    )
            except Exception:
                pass  # Fail-Open

        # Async Policy 체인 실행 — context 전파
        result = await self._execute_async_chain(func, *args, context=context, **kwargs)

        # Hook 호출 — 파이프라인 전체 결과에 대해서만 (2계층 구조)
        duration_ms = (time.monotonic() - start_time) * 1000
        result.total_duration_ms = duration_ms

        # Sink 처리 — 동기 Blocking (FailureSink Protocol은 동기 시그니처)
        if not result.success:
            self._process_sinks(result, context, args, kwargs)

        return result
```

### 8.2 Django/FastAPI 통합

```python
# Django view
from selfhealing.resilience.policies import compose, RetryPolicy, CircuitBreakerPolicy

pipeline = compose(
    RetryPolicy(max_retries=2),
    CircuitBreakerPolicy(service_name="external_api"),
)

def product_detail_view(request, product_id):
    result = pipeline.execute(lambda: fetch_product(product_id))
    if result.success:
        return JsonResponse(result.value)
    return JsonResponse({"error": "service_unavailable"}, status=503)


# FastAPI (async)
@app.get("/products/{product_id}")
async def get_product(product_id: int):
    result = await pipeline.execute_async(
        lambda: async_fetch_product(product_id)
    )
    if result.success:
        return result.value
    raise HTTPException(status_code=503)
```

## 9. 파일 구조

### 9.1 최종 디렉토리 구조

```
resilience/
├── policies/
│   ├── __init__.py          # compose() + 모든 Policy re-export
│   ├── base.py              # ResiliencePolicy, PolicyResult, PolicyOutcome (225번)
│   ├── composer.py          # PolicyComposer, compose() ← 본 문서
│   ├── retry.py             # RetryPolicy (226번)
│   ├── circuit_breaker.py   # CircuitBreakerPolicy (227번)
│   ├── bulkhead.py          # BulkheadPolicy (228번)
│   ├── fallback.py          # FallbackPolicy (229번)
│   ├── hedging.py           # HedgingPolicy, AsyncHedgingPolicy (230번)
│   ├── timeout.py           # TimeoutPolicy (신규)
│   ├── guards/
│   │   ├── __init__.py
│   │   ├── kill_switch.py   # KillSwitchGuard
│   │   └── error_budget.py  # ErrorBudgetGuard
│   ├── hooks/
│   │   ├── __init__.py
│   │   ├── audit.py         # AuditHook
│   │   ├── metrics.py       # MetricsHook
│   │   └── event_bus.py     # EventBusHook (ConfigUpdate 등)
│   ├── sinks/
│   │   ├── __init__.py
│   │   └── dlq.py           # DLQSink
│   └── presets.py           # standard_pipeline(), ha_pipeline()
└── bulkhead/                # 기존 유지
    ├── base.py
    ├── semaphore.py
    └── ...
```

### 9.2 re-export 구조

```python
# resilience/policies/__init__.py
from .base import ResiliencePolicy, PolicyResult, PolicyOutcome
from .composer import PolicyComposer, compose
from .retry import RetryPolicy
from .circuit_breaker import CircuitBreakerPolicy
from .bulkhead import BulkheadPolicy
from .fallback import FallbackPolicy
from .hedging import HedgingPolicy, AsyncHedgingPolicy
from .timeout import TimeoutPolicy
from .guards import KillSwitchGuard, ErrorBudgetGuard
from .hooks import AuditHook, MetricsHook
from .sinks import DLQSink
from .presets import standard_pipeline, ha_pipeline
```

## 10. 내부 기능 안전성 — 분리 시 기존 연결 보장

### 10.1 핵심 원칙: 래핑이지 교체가 아니다

Policy Composition은 기존 구현체를 **교체하지 않고 래핑**한다.
각 Phase별로 기존 기능의 안전성을 보장하는 방식:

| Phase | 기존 코드 상태 | 내부 기능 보장 방식 |
|-------|-------------|-------------------|
| Phase 1 (Policy 생성) | **수정 없음** | RetryPolicy 내부에 RetryHandler가 **그대로 존재**. 12건 하드코딩도 유지. |
| Phase 2 (테스트베드 검증) | **수정 없음** | 기존 태스크와 v2 태스크 병행. 기존 동작 100% 보존. |
| Phase 3 (소비자 전환) | **수정 없음** | 소비자만 PolicyComposer 사용으로 전환. 내부 구현체 변경 없음. |
| Phase 4 (하드코딩 분리) | ⚠️ **변경** | 아래 안전장치 적용 |
| Phase 5 (deprecated) | ⚠️ **변경** | 경고만 추가, 기능 제거 안 함 |

### 10.2 Phase 4 위험 분석 및 안전장치

Phase 4에서 `RetryHandler.execute()` 내부의 12건 하드코딩을 분리할 때,
**기존 RetryHandler 직접 사용자에게 미치는 영향**을 방지해야 한다.

#### 위험: Kill Switch/ErrorBudgetGate 보호 상실

```python
# 현재: RetryHandler.execute() 내부에 Kill Switch 체크 포함 (handler.py L419)
# Phase 4에서 이를 KillSwitchGuard로 이동하면,
# RetryHandler를 직접 사용하는 코드가 Kill Switch 보호를 잃음

# 기존 직접 사용자:
handler = RetryHandler(config=config)
result = handler.execute(func)  # ← Kill Switch 체크가 사라지면 위험
```

#### 안전장치: 기본값 보존 패턴

RetryHandler 리팩토링 시, **기존 하드코딩을 생성자 기본값으로 보존**:

```python
class RetryHandler:
    def __init__(
        self,
        config: RetryConfig,
        # 기존 하드코딩 → 생성자 주입 (기본값 = 기존 동작 유지)
        kill_switch_check: Callable[[], bool] | None = _default_kill_switch,
        error_budget_check: Callable[[], tuple[bool, str]] | None = _default_error_budget,
        audit_hook: Callable | None = _default_audit_hook,
        dlq_sink: Callable | None = _default_dlq_sink,
    ):
        ...

def _default_kill_switch() -> bool:
    """기존 handler.py L419의 _is_system_enabled()과 동일."""
    try:
        from selfhealing.services.system_control import get_system_control_manager
        return get_system_control_manager().is_enabled()
    except ImportError:
        return True  # Fail-Open

def _default_error_budget() -> tuple[bool, str]:
    """기존 handler.py L428의 _check_error_budget_gate()과 동일."""
    try:
        from selfhealing.services.error_budget_gate import check_automation_allowed
        return check_automation_allowed()
    except ImportError:
        return (True, "")  # Fail-Open
```

**효과**:
- `RetryHandler(config=config)` — **기존과 100% 동일** (기본값이 하드코딩과 같은 동작)
- `RetryHandler(config=config, kill_switch_check=None)` — Kill Switch 비활성화 (Policy Composition에서 Guard로 처리)
- `RetryPolicy`는 `RetryHandler(kill_switch_check=None, error_budget_check=None, ...)`로 생성하여 Guard/Hook/Sink 분리

#### 영향 받는 내부 기능 전체 목록

| 내부 기능 | 현재 위치 | Phase 4 처리 | 안전장치 |
|-----------|----------|------------|--------|
| Kill Switch | handler.py L419 | 생성자 주입 | 기본값 = `_default_kill_switch` (기존 동작) |
| ErrorBudgetGate | handler.py L428 | 생성자 주입 | 기본값 = `_default_error_budget` (기존 동작) |
| RateLimitCoordinator | handler.py L468 | 생성자 주입 | 기본값 = lazy import (기존 동작) |
| ThrottleAwareBackoff | handler.py L510 | 생성자 주입 | 기본값 = lazy import (기존 동작) |
| AdaptiveRetryBudget | handler.py L463 | 생성자 주입 | 기본값 = 자동 생성 (기존 동작) |
| Audit logging | handler.py L479 | 생성자 주입 | 기본값 = `_default_audit_hook` (기존 동작) |
| Prometheus metrics | handler.py L290 | 생성자 주입 | 기본값 = lazy import + ImportError 무시 (기존 동작) |
| DLQ | handler.py L587 | 생성자 주입 | 기본값 = `_default_dlq_sink` (기존 동작) |
| BulkheadRegistry | strategy.py L97 | 생성자 주입 | 기본값 = lazy import (기존 동작) |
| EventBus | strategy.py L108 | 생성자 주입 | 기본값 = lazy import (기존 동작) |
| FallbackStrategy 상속 | strategy.py L50 | 상속 해체 | `HedgingStrategyCompat` 어댑터 (230번) |
| CB 내장 Fallback | service.py L293 | deprecated | 기존 메서드 유지 + DeprecationWarning |

**원칙**: Phase 4에서 `None`으로 설정할 수 있게 하되, **기본값은 항상 기존 동작을 유지**.

### 10.3 @with_retry, @hedged, @bulkhead 데코레이터 안전성

기존 데코레이터는 내부적으로 PolicyComposer를 사용하도록 전환하되,
**외부 API는 변경하지 않는다**:

```python
# 전환 전: @with_retry 데코레이터
@with_retry(domain="payment", max_attempts=3)
def process_payment(payment_id): ...

# 전환 후: 동일한 데코레이터, 내부만 RetryPolicy 사용
@with_retry(domain="payment", max_attempts=3)  # ← API 변경 없음
def process_payment(payment_id): ...
```

데코레이터 내부 구현만 변경:
```python
# decorators.py — 전환 후 (내부만 변경)
def with_retry(domain="default", max_attempts=None, ...):
    def decorator(func):
        def wrapper(*args, **kwargs):
            # 기존: handler = RetryHandler(config); handler.execute(func)
            # 전환: policy = RetryPolicy(config); policy.execute(func)
            policy = RetryPolicy(config=RetryPolicyConfig(...))
            result = policy.execute(func, *args, **kwargs)
            if result.success:
                return result.value
            raise MaxRetriesExceededError(...)
        return wrapper
    return decorator
```

## 11. 외부 앱 통합 가이드

### 11.1 설치

```bash
pip install selfhealing
```

### 11.2 최소 설정 — Policy만 사용 (Provider 불필요)

selfhealing의 Policy 레이어는 **Provider/Repository 설정 없이** 사용 가능하다.
Circuit Breaker 상태를 인메모리로 관리하는 기본 구현이 포함된다:

```python
# 외부 앱: 설치 후 바로 사용 가능
from selfhealing.resilience.policies import compose, RetryPolicy, CircuitBreakerPolicy

# Provider 설정 없이 즉시 사용
pipeline = compose(
    RetryPolicy(max_retries=3),
    CircuitBreakerPolicy(service_name="my_api"),  # 인메모리 CB 상태
)

result = pipeline.execute(lambda: call_my_api())
```

### 11.3 영속적 CB 상태 — Provider 연결

CB 상태를 Redis/DB에 영속화하려면 Provider 설정이 필요:

```python
# settings.py (Django) 또는 config.py (FastAPI)
SELFHEALING = {
    "PROVIDERS": {
        "circuit_breaker": {
            "backend": "redis",
            "redis_url": "redis://localhost:6379/1",
        },
        "dlq": {
            "backend": "database",  # Django ORM 사용
        },
    },
}
```

### 11.4 프레임워크별 통합 패턴

#### Django — Middleware + Task

```python
# settings.py
INSTALLED_APPS = [
    ...
    "selfhealing",  # Django 앱 등록
]

# 파이프라인 정의 (앱 레벨)
# myapp/resilience.py
from selfhealing.resilience.policies import compose, RetryPolicy, CircuitBreakerPolicy
from selfhealing.resilience.policies.hooks import AuditHook
from selfhealing.resilience.policies.sinks import DLQSink

external_api_pipeline = (
    compose(
        RetryPolicy(max_retries=3),
        CircuitBreakerPolicy(service_name="external_api"),
    )
    .add_hook(AuditHook())
    .add_sink(DLQSink())
)

# view에서 사용
# myapp/views.py
from myapp.resilience import external_api_pipeline

def order_view(request):
    result = external_api_pipeline.execute(lambda: call_external_api())
    ...
```

#### FastAPI — Dependency Injection

```python
# dependencies.py
from selfhealing.resilience.policies import compose, RetryPolicy, CircuitBreakerPolicy

def get_payment_pipeline() -> PolicyComposer:
    return compose(
        RetryPolicy(max_retries=3),
        CircuitBreakerPolicy(service_name="payment_gateway"),
    )

# routes.py
from fastapi import Depends

@app.post("/payments")
async def create_payment(
    data: PaymentRequest,
    pipeline: PolicyComposer = Depends(get_payment_pipeline),
):
    result = await pipeline.execute_async(
        lambda: payment_gateway.charge(data.amount)
    )
    if result.success:
        return {"payment_id": result.value["id"]}
    raise HTTPException(status_code=503)
```

#### Flask — 팩토리 패턴

```python
# extensions.py
from selfhealing.resilience.policies import compose, RetryPolicy, CircuitBreakerPolicy

api_pipeline = compose(
    RetryPolicy(max_retries=2),
    CircuitBreakerPolicy(service_name="api"),
)

# routes.py
from extensions import api_pipeline

@app.route("/data")
def get_data():
    result = api_pipeline.execute(lambda: fetch_data())
    return jsonify(result.value) if result.success else abort(503)
```

### 11.5 Guard/Hook/Sink 선택적 사용

외부 앱은 필요한 것만 **선택적으로** 연결:

```python
# 최소: Policy만
pipeline = compose(RetryPolicy(max_retries=3))

# + Audit 로깅
pipeline = compose(RetryPolicy(max_retries=3)).add_hook(AuditHook())

# + DLQ (selfhealing DB 필요)
pipeline = compose(RetryPolicy(max_retries=3)).add_sink(DLQSink())

# + Kill Switch (selfhealing 운영 기능)
pipeline = compose(RetryPolicy(max_retries=3)).add_guard(KillSwitchGuard())

# 풀 스택
pipeline = (
    compose(RetryPolicy(max_retries=3), CircuitBreakerPolicy(service_name="api"))
    .add_guard(KillSwitchGuard())
    .add_guard(ErrorBudgetGuard())
    .add_hook(AuditHook())
    .add_hook(MetricsHook())
    .add_sink(DLQSink())
)
```

## 12. 마이그레이션 전략

### 12.1 Phase 1 — PolicyComposer 생성 (기존 코드 수정 없음)

1. `resilience/policies/` 디렉토리 생성
2. `base.py`, `composer.py` 구현
3. 각 Policy 래퍼 구현 (내부에서 기존 구현체 재사용)
4. Guard/Hook/Sink 구현
5. unit test 작성

### 12.2 Phase 2 — 테스트베드(Shopping) 검증

```python
# Shopping 테스트베드에서 기존 코드와 병행 검증
# payment_recovery_tasks.py에 v2 태스크 추가 (기존 태스크 유지)

@shared_task
def retry_failed_payment_v2(payment_id):
    """Policy Composition 기반 결제 복구 (테스트베드 검증용)."""
    result = payment_pipeline.execute(
        lambda: process_payment(payment_id)
    )
    ...

# A/B 비교로 동일 결과 확인 후 v1 제거
```

### 12.3 Phase 3 — 테스트베드 소비자 코드 전환

1. `PaymentRecoveryService`의 수동 CB 조합 코드를 `PolicyComposer`로 대체
2. tenacity `@retry`를 `RetryPolicy`로 대체
3. Celery 내장 retry를 `RetryPolicy`로 대체

### 12.4 Phase 4 — 하드코딩 의존성 정리 (안전장치 적용)

**10.2절의 기본값 보존 패턴 적용** 후:

1. `RetryHandler.execute()` 내부의 12건 하드코딩을 **생성자 주입으로 전환** (226번)
   - 기본값 = 기존 동작 유지 → 직접 사용자 영향 없음
2. `HedgingStrategy`의 FallbackStrategy 상속 해체 (230번)
   - `HedgingStrategyCompat` 어댑터 제공 → 기존 사용자 영향 없음
3. `should_allow_with_fallback()` deprecated (227번, 229번)
   - 기존 메서드 유지 + DeprecationWarning → 즉시 삭제 안 함
4. `@bulkhead(fallback=...)` deprecated (228번)
   - 기존 파라미터 유지 + DeprecationWarning → 즉시 삭제 안 함

### 12.5 Phase 5 — 기존 클래스 deprecated (삭제하지 않음)

```python
# services/retry_handler/handler.py
class RetryHandler:
    """
    .. deprecated:: 2.0
        Use compose(RetryPolicy(...), ...).execute(func) instead.
        This class is preserved for backward compatibility.
        All internal features (Kill Switch, ErrorBudget, etc.) continue to work.
    """

# core/hedging/strategy.py
class HedgingStrategy(FallbackStrategy):
    """
    .. deprecated:: 2.0
        Use HedgingPolicy instead.
        HedgingStrategyCompat adapter is available for migration.
    """
```

## 13. 참조

- **224번 문서**: 마스터 플랜 — 전환 대상/비대상 분류, 인프라 유지 원칙
- **225번 문서**: `ResiliencePolicy`, `PolicyResult`, `PolicyGuard`, `PolicyHook`, `FailureSink` 인터페이스
- **226번 문서**: `RetryPolicy` — 12건 하드코딩 분리 상세 설계
- **227번 문서**: `CircuitBreakerPolicy` — `should_allow()` → `execute()` 변환
- **228번 문서**: `BulkheadPolicy` — 독립 래핑, `@bulkhead(fallback=...)` 분리
- **229번 문서**: `FallbackPolicy` — 3곳 분산 Fallback 통합, CB 내장 Fallback 분리
- **230번 문서**: `HedgingPolicy` — FallbackStrategy 상속 해체, Bulkhead 외부 주입

## 14. 리뷰 결정 요약

231번 문서에 대한 6가지 리뷰 질문과 확정 결과를 정리한다.

### 14.1 [수정] FallbackPolicy 중복 실행 방지 — §3.2 전면 수정

| 항목 | 내용 |
|------|------|
| **문제** | §3.2 `fallback_wrapper`에서 `fb.execute(lambda: inner())`를 호출하면 `inner()`가 2회 실행됨 |
| **원인** | `FallbackPolicy.execute(func)`는 내부에서 `func()`를 실행하므로, Composer가 이미 실행한 `inner()`를 다시 실행함 |
| **수정** | `fb.execute(lambda: inner())` → `fb._apply_fallback(original_error=e, context=context)` |
| **코드 근거** | 229번 문서 §3.1-3.2에서 `_apply_fallback()` 메서드를 Composer 전용 내부 API로 확정. 구현 완료: `resilience/policies/fallback.py` L163 |
| **API 구분** | 일반 소비자(외부 앱)는 `FallbackPolicy.execute(func)` 사용, Composer만 `_apply_fallback()` 호출 |

### 14.2 [수정] PolicyContext 전파 — §3.1, §3.2, §4, §8 전면 보완

| 항목 | 내용 |
|------|------|
| **문제** | §3.1 `execute()` 시그니처에 `context` 인자가 없어 Guard/Hook/Sink에 비즈니스 식별자 전달 불가 |
| **수정** | `execute(func, *args, context: PolicyContext \| None = None, **kwargs)` 시그니처 통일 |
| **전파 흐름** | `execute(context=)` → `guard.check(context=)` → `_execute_policy_chain(context=)` → `policy.execute(inner, context=)` → `_process_sinks(result, context, ...)` |
| **코드 근거** | 225번 문서 §2.2-2.6 — `ResiliencePolicy.execute(context=)`, `PolicyGuard.check(context=)`, `FailureSink.handle_failure(context=)` 모두 context 인자 포함. 구현 완료: `interfaces/resilience_policy.py` L222, L269, L333 |
| **context=None 동작** | Guard는 전역 상태만 체크, Sink는 식별자 없이 저장 (기존 `ErrorBudgetGate.check(tier_id=None)` 글로벌 판정과 일관) |

### 14.3 [확정] Composer Hook 관찰 범위 — 2계층 구조

| 항목 | 내용 |
|------|------|
| **결정** | Composer Hook은 **파이프라인 전체(End-to-End)** 결과만 관찰 |
| **근거** | 개별 Policy 내부 이벤트(예: Retry 각 시도)는 Policy 자체가 처리하며, Composer Hook에 전파하지 않음 |
| **코드 근거** | RetryPolicy (`services/retry_handler/policy.py` L49)는 내부에 Hook 호출이 없음. 기존 `RetryHandler._log_retry_audit()` (handler.py L145)는 Policy 분리 시 제거 대상이며 Composer의 `add_hook(AuditHook())`이 대체함 |
| **구조** | Composer Hook = 전체 흐름 관찰 / Policy 내부 = 자체 로직 또는 없음 (2계층) |

### 14.4 [확정] AsyncPolicyComposer 타입 분리 — 어댑터 미제공

| 항목 | 내용 |
|------|------|
| **결정** | 동기/비동기 엄격 분리. 자동 래핑 어댑터 제공하지 않음 |
| **근거** | 기존 코드베이스 3건 선례 — `SemaphoreBulkhead`/`AsyncSemaphoreBulkhead`, `BulkheadPolicy`/`AsyncBulkheadPolicy`, `HedgingStrategy`/`AsyncHedgingStrategy` 모두 별도 클래스, 상속 없음, 어댑터 없음 |
| **코드 근거** | `bulkhead/policy.py` L49/L174, `bulkhead/base.py`/`async_semaphore.py`, `core/hedging/strategy.py`/`async_strategy.py` L49 |
| **소비자 책임** | 비동기 파이프라인에서 동기 함수 사용 시 `asyncio.to_thread()`로 소비자가 직접 래핑 (`AsyncFallbackPolicy` docstring 선례: `resilience/policies/fallback.py` L335) |

### 14.5 [확정] Sink 처리 — 동기 Blocking

| 항목 | 내용 |
|------|------|
| **결정** | Sink 처리는 **동기적(Blocking)** 으로 수행 |
| **근거** | 기존 `RetryHandler._move_to_dlq()` (handler.py L567)가 동기 Blocking이었으며, 반환된 `dlq_id`를 `RetryResult`에 포함하는 패턴 |
| **코드 근거** | `FailureSink` Protocol (`interfaces/resilience_policy.py` L333)이 동기 시그니처 (`def handle_failure() -> str \| None`). 구현체 `DLQSink` (`services/retry_handler/sinks.py` L27)도 동기 |
| **성능 근거** | DLQ 저장은 로컬 DB(Django ORM) write이므로 수 ms 수준. 비동기 분리의 복잡도 대비 이점 없음 |

### 14.6 [확정] Exception Chaining — raise result.error 패턴 유지

| 항목 | 내용 |
|------|------|
| **결정** | `raise result.error` 패턴 유지. `raise ... from ...` 또는 `with_traceback` 추가하지 않음 |
| **근거** | 기존 코드베이스 전체에서 `raise ... from` 패턴 0건. `except Exception as e` → 변수 저장 → `PolicyResult.error = e` 패턴이 표준 |
| **코드 근거** | handler.py L510 `last_error = e`, policy.py L143 `last_error = e` — 모든 기존 구현이 단순 저장 방식. `sys.exc_info()` 캡처 0건 |
| **디버깅 보존** | Python은 `except Exception as e`로 캡처된 예외 객체의 `__traceback__` 속성을 유지하므로, `traceback.print_exception(result.error)`로 원본 위치 확인 가능 |

---

## 15. 구현 현황

> 이 섹션은 설계 문서에 대한 구현 결과를 기록한다.

### 15.1 구현 완료 파일 목록

| 파일 | 설명 |
|------|------|
| `interfaces/resilience_policy.py` | `PolicyRejectedException` 추가 (Guard 거부/Timeout 시 사용) |
| `resilience/policies/composer.py` | PolicyComposer(동기), AsyncPolicyComposer(비동기), compose(), compose_async() |
| `resilience/policies/guards/__init__.py` | Guard 패키지 re-export |
| `resilience/policies/guards/kill_switch.py` | KillSwitchGuard — SystemControlManager 연동 |
| `resilience/policies/guards/error_budget.py` | ErrorBudgetGuard — ErrorBudgetGate 연동 |
| `resilience/policies/hooks/__init__.py` | Hook 패키지 re-export |
| `resilience/policies/hooks/audit.py` | AuditHook — Python logger 기반 감사 로깅 |
| `resilience/policies/hooks/metrics.py` | MetricsHook — Prometheus Counter/Histogram |
| `resilience/policies/hooks/event_bus.py` | EventBusHook — EventBus 이벤트 발행 |
| `resilience/policies/sinks/__init__.py` | Sink 패키지 re-export |
| `resilience/policies/sinks/dlq.py` | DLQSink re-export (services/retry_handler/sinks.py) |
| `resilience/policies/presets.py` | standard_pipeline(), ha_pipeline() 프리셋 팩토리 |
| `resilience/policies/__init__.py` | 통합 re-export (__getattr__ lazy import for hedging) |

### 15.2 설계 대비 변경 사항 (문서-코드 차이)

| # | 문서 기술 | 실제 구현 | 사유 |
|---|----------|----------|------|
| 1 | `get_system_control_manager()` | `get_system_control()` | 실제 함수명 (core/coordinator.py) |
| 2 | `check_automation_allowed()` → tuple 반환 | `GateCheckResult` dataclass 반환 | 실제 반환 타입 (regional_gate.py) |
| 3 | `audit_helpers.log_policy_success/failure()` | Python logger 사용 | audit_helpers 모듈 미존재 — Fail-Open 원칙 |
| 4 | `PolicyComposer.execute_async()` 단일 클래스 | `AsyncPolicyComposer` 별도 클래스 | §14.4 확정 결정 + 기존 코드베이스 3건 선례 |
| 5 | FallbackPolicy outcome 직접 반환 | `_FallbackApplied` 내부 시그널 예외 | 체인 내 SUCCESS_WITH_FALLBACK outcome 전파 보장 |

### 15.3 통합 테스트 현황

기존 integration 디렉토리(`tests/self_healing/integration/`, `packages/selfhealing-python/tests/integration/`)에
PolicyComposer 관련 통합 테스트는 **0건**이다.
기존 통합 테스트는 Circuit Breaker Redis 분산 테스트, 스토리지 복원력 등 인프라 레벨 테스트만 존재한다.
단위 테스트는 사용자 요청에 따라 이번 구현에서 생성하지 않았다.
