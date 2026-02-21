"""
Policy Composer — 여러 ResiliencePolicy를 선언적으로 조합하는 엔진.

Guard(사전 검증) → Policy 체인(중첩 래핑) → Hook(이벤트 관찰) → Sink(최종 실패 처리)
순서로 파이프라인을 구성한다.

동기/비동기 분리:
- PolicyComposer: 동기 ResiliencePolicy만 허용
- AsyncPolicyComposer: 비동기 AsyncResiliencePolicy만 허용
  기존 SemaphoreBulkhead/AsyncSemaphoreBulkhead,
  BulkheadPolicy/AsyncBulkheadPolicy 분리 선례와 동일 패턴.

편의 함수:
- compose(): 동기 Policy 조합
- compose_async(): 비동기 Policy 조합

Hook 관찰 범위 — 2계층 구조:
- Composer Hook: 파이프라인 전체(End-to-End) 결과만 관찰
- Policy 내부: 자체 로직 또는 없음 (Retry 각 시도 등은 Policy가 처리)

Sink 처리:
- 동기(Blocking)으로 수행 (FailureSink Protocol 준수)
- DLQ 저장은 로컬 DB write이므로 수 ms 수준

FallbackPolicy 중복 실행 방지:
- Composer 체인 내에서는 execute() 대신 _apply_fallback() 호출
- func 실행 1회만 보장
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable, Generic, TypeVar

from selfhealing.interfaces.resilience_policy import (
    AsyncResiliencePolicy,
    FailureSink,
    PolicyContext,
    PolicyGuard,
    PolicyHook,
    PolicyOutcome,
    PolicyRejectedException,
    PolicyResult,
    ResiliencePolicy,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class _FallbackApplied(Exception):
    """Fallback 적용 완료를 체인 상위로 전파하는 내부 시그널.

    FallbackPolicy가 체인 내에서 대체 값을 생성했을 때,
    SUCCESS_WITH_FALLBACK outcome을 최종 PolicyResult까지 전달하기 위해 사용한다.
    소비자에게 노출되지 않는 Composer 내부 전용 예외이다.
    """

    def __init__(self, result: PolicyResult) -> None:
        self.result = result
        super().__init__("Fallback applied")


# =============================================================================
# PolicyComposer — 동기 Policy 조합 엔진
# =============================================================================


class PolicyComposer(Generic[T]):
    """
    동기 Policy 조합 엔진.

    여러 ResiliencePolicy를 선언적으로 조합하여 단일 실행 파이프라인으로 구성한다.
    Guard/Hook/Sink를 연결하여 인프라 레이어와 통합한다.

    실행 순서:
    1. Guards 검증 (Kill Switch, ErrorBudgetGate 등)
    2. Policies 순차 래핑 (추가 순서 = 바깥→안쪽 실행 순서)
    3. Hooks 호출 (Audit, Metrics 등) — 파이프라인 전체 결과만 관찰
    4. 실패 시 Sink 처리 (DLQ 등) — 동기 Blocking

    타입 안전성:
    - ResiliencePolicy(동기)만 추가 가능
    - AsyncResiliencePolicy 추가 시 런타임 TypeError 발생
    """

    def __init__(self) -> None:
        self._policies: list[ResiliencePolicy] = []
        self._guards: list[PolicyGuard] = []
        self._hooks: list[PolicyHook] = []
        self._sinks: list[FailureSink] = []

    # === Builder API ===

    def add(self, policy: ResiliencePolicy) -> PolicyComposer[T]:
        """Policy 추가. 추가 순서가 바깥→안쪽 실행 순서."""
        if isinstance(policy, AsyncResiliencePolicy) and not isinstance(policy, ResiliencePolicy):
            raise TypeError(
                f"Cannot add async policy '{policy.name}' to sync PolicyComposer. "
                f"Use AsyncPolicyComposer or compose_async() instead."
            )
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
        4. 실패 시 Sink 처리 — 동기 Blocking

        Args:
            func: 실행할 함수
            *args: 함수 위치 인자
            context: 실행 컨텍스트 (Guard/Hook/Sink에 전파).
                     None이면 Guard는 전역 상태만 체크하고,
                     Sink는 비즈니스 식별자 없이 저장한다.
            **kwargs: 함수 키워드 인자

        Returns:
            PolicyResult[T]: 통합 결과. 예외를 던지지 않는다.
        """
        start_time = time.perf_counter()

        # Step 1: Guard 검증
        for guard in self._guards:
            try:
                guard_result = guard.check(context=context)
                if not guard_result.allowed:
                    self._notify_hooks_reject(guard.name, guard_result.reason or "")
                    return PolicyResult(
                        value=None,
                        outcome=PolicyOutcome.REJECTED,
                        metadata={
                            "rejected_by": guard.name,
                            "reason": guard_result.reason,
                        },
                    )
            except Exception as e:
                # Fail-Open: Guard 실패 시 통과 허용
                logger.warning("Guard '%s' failed (fail-open): %s", guard.name, e)

        # Step 2: Policy 체인 실행
        result = self._execute_policy_chain(func, *args, context=context, **kwargs)

        # Step 3: Hook 호출 — 파이프라인 전체 결과만 관찰
        duration_ms = (time.perf_counter() - start_time) * 1000
        result.total_duration_ms = duration_ms

        if result.success:
            self._notify_hooks_success(result)
        else:
            self._notify_hooks_failure(result)

            # Step 4: Sink 처리 — 동기 Blocking
            if result.outcome == PolicyOutcome.FAILURE:
                self._process_sinks(result, context, args, kwargs)

        return result

    # === Policy Chain ===

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

        FallbackPolicy 특별 처리:
        - execute() 대신 _apply_fallback() 호출 (func 중복 실행 방지)
        - _apply_fallback()은 Composer 전용 내부 API
        """
        from selfhealing.resilience.policies.fallback import FallbackPolicy

        if not self._policies:
            # Policy 없음 → 직접 실행
            try:
                value = func(*args, **kwargs)
                return PolicyResult(value=value, outcome=PolicyOutcome.SUCCESS)
            except Exception as e:
                return PolicyResult(value=None, outcome=PolicyOutcome.FAILURE, error=e)

        # 중첩 함수 구성 (역순으로 감싸기)
        wrapped: Callable[[], T] = lambda: func(*args, **kwargs)
        executed_policies: list[str] = []

        for policy in reversed(self._policies):
            outer_fn = wrapped
            current_policy = policy

            if isinstance(current_policy, FallbackPolicy):
                # FallbackPolicy: _apply_fallback() 기반 조건부 래퍼
                # func 1회만 실행 보장 (inner() 결과 재사용)
                # _FallbackApplied 시그널로 SUCCESS_WITH_FALLBACK outcome 전파
                def fallback_wrapper(inner: Callable = outer_fn, fb: FallbackPolicy = current_policy) -> T:
                    try:
                        value = inner()
                        return value
                    except _FallbackApplied:
                        raise  # 하위 FallbackPolicy의 시그널을 그대로 전파
                    except Exception as e:
                        # predicate 확인 → _apply_fallback 직접 호출
                        check_result = PolicyResult(value=None, outcome=PolicyOutcome.FAILURE, error=e)
                        if fb._predicate(check_result):
                            fb_result = fb._apply_fallback(
                                original_error=e,
                                context=context,
                            )
                            if fb_result.success:
                                raise _FallbackApplied(fb_result)
                        raise e

                wrapped = fallback_wrapper
            else:
                # 일반 Policy: execute()로 래핑
                def policy_wrapper(inner: Callable = outer_fn, p: ResiliencePolicy = current_policy) -> T:
                    result = p.execute(inner, context=context)
                    if result.success:
                        return result.value  # type: ignore[return-value]
                    elif result.error:
                        raise result.error
                    else:
                        raise PolicyRejectedException(f"Policy '{p.name}' rejected: {result.outcome}")

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
        except _FallbackApplied as fa:
            # FallbackPolicy가 적용된 경우 — SUCCESS_WITH_FALLBACK outcome 전파
            fb_result: PolicyResult = fa.result
            return PolicyResult(
                value=fb_result.value,
                outcome=fb_result.outcome,
                error=fb_result.error,
                executed_policies=list(reversed(executed_policies)),
                metadata=fb_result.metadata,
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

    # === Hook Notification ===

    def _notify_hooks_success(self, result: PolicyResult) -> None:
        """성공 시 모든 Hook의 on_success 호출 (Fail-Open)."""
        for hook in self._hooks:
            try:
                hook.on_success("composer", result)
            except Exception as e:
                logger.warning("Hook on_success failed (fail-open): %s", e)

    def _notify_hooks_failure(self, result: PolicyResult) -> None:
        """실패 시 모든 Hook의 on_failure 호출 (Fail-Open)."""
        for hook in self._hooks:
            try:
                hook.on_failure(
                    "composer",
                    result.error or Exception("Unknown"),
                    result.total_attempts,
                )
            except Exception as e:
                logger.warning("Hook on_failure failed (fail-open): %s", e)

    def _notify_hooks_reject(self, guard_name: str, reason: str) -> None:
        """거부 시 모든 Hook의 on_reject 호출 (Fail-Open)."""
        for hook in self._hooks:
            try:
                hook.on_reject(guard_name, reason)
            except Exception as e:
                logger.warning("Hook on_reject failed (fail-open): %s", e)

    # === Sink Processing ===

    def _process_sinks(
        self,
        result: PolicyResult,
        context: PolicyContext | None,
        args: tuple,
        kwargs: dict,
    ) -> None:
        """모든 Sink에 최종 실패를 전달 (동기 Blocking)."""
        if result.error is None:
            return

        for sink in self._sinks:
            try:
                sink_id = sink.handle_failure(
                    error=result.error,
                    context=context,
                    policy_result=result,
                )
                if sink_id is not None:
                    result.metadata["sink_id"] = sink_id
            except Exception as e:
                logger.warning("Sink handle_failure failed: %s", e)


# =============================================================================
# AsyncPolicyComposer — 비동기 Policy 조합 엔진
# =============================================================================


class AsyncPolicyComposer(Generic[T]):
    """
    비동기 Policy 조합 엔진.

    AsyncResiliencePolicy만 허용하여 타입 수준에서 동기/비동기 혼용을 차단한다.
    PolicyComposer와 동일한 Guard/Hook/Sink 통합을 비동기로 제공한다.

    Guard: 동기 (빠른 체크)
    Hook: 동기 (관찰 전용)
    Sink: 동기 Blocking (FailureSink Protocol 준수)
    """

    def __init__(self) -> None:
        self._policies: list[AsyncResiliencePolicy] = []
        self._guards: list[PolicyGuard] = []
        self._hooks: list[PolicyHook] = []
        self._sinks: list[FailureSink] = []

    # === Builder API ===

    def add(self, policy: AsyncResiliencePolicy) -> AsyncPolicyComposer[T]:
        """비동기 Policy 추가. 추가 순서가 바깥→안쪽 실행 순서."""
        self._policies.append(policy)
        return self

    def add_guard(self, guard: PolicyGuard) -> AsyncPolicyComposer[T]:
        """Guard 추가. 모든 Policy 실행 전 검증."""
        self._guards.append(guard)
        return self

    def add_hook(self, hook: PolicyHook) -> AsyncPolicyComposer[T]:
        """Hook 추가. Policy 실행 이벤트 관찰."""
        self._hooks.append(hook)
        return self

    def add_sink(self, sink: FailureSink) -> AsyncPolicyComposer[T]:
        """FailureSink 추가. 최종 실패 처리."""
        self._sinks.append(sink)
        return self

    # === Execution ===

    async def execute(
        self,
        func: Callable[..., Awaitable[T]],
        *args: Any,
        context: PolicyContext | None = None,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        비동기 Policy 파이프라인 실행.

        Guard/Hook: 동기 (빠른 체크, 관찰 전용)
        Sink: 동기 Blocking (FailureSink Protocol 준수)

        Args:
            func: 실행할 비동기 함수
            *args: 함수 위치 인자
            context: 실행 컨텍스트 (Guard/Hook/Sink에 전파)
            **kwargs: 함수 키워드 인자

        Returns:
            PolicyResult[T]: 통합 결과. 예외를 던지지 않는다.
        """
        start_time = time.perf_counter()

        # Guard 검증 (동기)
        for guard in self._guards:
            try:
                guard_result = guard.check(context=context)
                if not guard_result.allowed:
                    self._notify_hooks_reject(guard.name, guard_result.reason or "")
                    return PolicyResult(
                        value=None,
                        outcome=PolicyOutcome.REJECTED,
                        metadata={
                            "rejected_by": guard.name,
                            "reason": guard_result.reason,
                        },
                    )
            except Exception:
                pass  # Fail-Open

        # Async Policy 체인 실행
        result = await self._execute_async_chain(func, *args, context=context, **kwargs)

        # Hook 호출 — 파이프라인 전체 결과만 관찰
        duration_ms = (time.perf_counter() - start_time) * 1000
        result.total_duration_ms = duration_ms

        if result.success:
            self._notify_hooks_success(result)
        else:
            self._notify_hooks_failure(result)

            # Sink 처리 — 동기 Blocking
            if result.outcome == PolicyOutcome.FAILURE:
                self._process_sinks(result, context, args, kwargs)

        return result

    # === Async Policy Chain ===

    async def _execute_async_chain(
        self,
        func: Callable[..., Awaitable[T]],
        *args: Any,
        context: PolicyContext | None = None,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        비동기 Policy 체인을 중첩 실행.

        AsyncFallbackPolicy 특별 처리:
        - execute() 대신 _apply_fallback() 호출 (func 중복 실행 방지)
        """
        from selfhealing.resilience.policies.fallback import AsyncFallbackPolicy

        if not self._policies:
            try:
                value = await func(*args, **kwargs)
                return PolicyResult(value=value, outcome=PolicyOutcome.SUCCESS)
            except Exception as e:
                return PolicyResult(value=None, outcome=PolicyOutcome.FAILURE, error=e)

        # 비동기 중첩 함수 구성 (역순으로 감싸기)
        async def initial_fn() -> T:
            return await func(*args, **kwargs)

        wrapped: Callable[[], Awaitable[T]] = initial_fn
        executed_policies: list[str] = []

        for policy in reversed(self._policies):
            outer_fn = wrapped
            current_policy = policy

            if isinstance(current_policy, AsyncFallbackPolicy):

                async def fallback_wrapper(
                    inner: Callable = outer_fn,
                    fb: AsyncFallbackPolicy = current_policy,
                ) -> T:
                    try:
                        value = await inner()
                        return value
                    except _FallbackApplied:
                        raise  # 하위 AsyncFallbackPolicy의 시그널을 그대로 전파
                    except Exception as e:
                        check_result = PolicyResult(value=None, outcome=PolicyOutcome.FAILURE, error=e)
                        if fb._predicate(check_result):
                            fb_result = await fb._apply_fallback(
                                original_error=e,
                                context=context,
                            )
                            if fb_result.success:
                                raise _FallbackApplied(fb_result)
                        raise e

                wrapped = fallback_wrapper
            else:

                async def async_policy_wrapper(
                    inner: Callable = outer_fn,
                    p: AsyncResiliencePolicy = current_policy,
                ) -> T:
                    result = await p.execute(inner, context=context)
                    if result.success:
                        return result.value  # type: ignore[return-value]
                    elif result.error:
                        raise result.error
                    else:
                        raise PolicyRejectedException(f"Policy '{p.name}' rejected: {result.outcome}")

                wrapped = async_policy_wrapper

            executed_policies.append(current_policy.name)

        # 최종 실행
        try:
            value = await wrapped()
            return PolicyResult(
                value=value,
                outcome=PolicyOutcome.SUCCESS,
                executed_policies=list(reversed(executed_policies)),
            )
        except _FallbackApplied as fa:
            # AsyncFallbackPolicy가 적용된 경우 — SUCCESS_WITH_FALLBACK outcome 전파
            fb_result: PolicyResult = fa.result
            return PolicyResult(
                value=fb_result.value,
                outcome=fb_result.outcome,
                error=fb_result.error,
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

    # === Hook Notification (동기) ===

    def _notify_hooks_success(self, result: PolicyResult) -> None:
        """성공 시 모든 Hook의 on_success 호출 (Fail-Open)."""
        for hook in self._hooks:
            try:
                hook.on_success("composer", result)
            except Exception as e:
                logger.warning("Hook on_success failed (fail-open): %s", e)

    def _notify_hooks_failure(self, result: PolicyResult) -> None:
        """실패 시 모든 Hook의 on_failure 호출 (Fail-Open)."""
        for hook in self._hooks:
            try:
                hook.on_failure(
                    "composer",
                    result.error or Exception("Unknown"),
                    result.total_attempts,
                )
            except Exception as e:
                logger.warning("Hook on_failure failed (fail-open): %s", e)

    def _notify_hooks_reject(self, guard_name: str, reason: str) -> None:
        """거부 시 모든 Hook의 on_reject 호출 (Fail-Open)."""
        for hook in self._hooks:
            try:
                hook.on_reject(guard_name, reason)
            except Exception as e:
                logger.warning("Hook on_reject failed (fail-open): %s", e)

    # === Sink Processing (동기) ===

    def _process_sinks(
        self,
        result: PolicyResult,
        context: PolicyContext | None,
        args: tuple,
        kwargs: dict,
    ) -> None:
        """모든 Sink에 최종 실패를 전달 (동기 Blocking)."""
        if result.error is None:
            return

        for sink in self._sinks:
            try:
                sink_id = sink.handle_failure(
                    error=result.error,
                    context=context,
                    policy_result=result,
                )
                if sink_id is not None:
                    result.metadata["sink_id"] = sink_id
            except Exception as e:
                logger.warning("Sink handle_failure failed: %s", e)


# =============================================================================
# 편의 함수
# =============================================================================


def compose(*policies: ResiliencePolicy) -> PolicyComposer:
    """
    동기 Policy를 선언적으로 조합하는 편의 함수.

    policies 순서 = 바깥→안쪽 실행 순서:
    - compose(Retry, CB, Bulkhead).execute(func)
    - = Retry(CB(Bulkhead(func)))

    Usage::

        result = compose(
            RetryPolicy(max_retries=3),
            CircuitBreakerPolicy(service_name="payment"),
            BulkheadPolicy(bulkhead=semaphore),
            FallbackPolicy(default_value={"status": "degraded"}),
        ).execute(lambda: call_payment_api())
    """
    composer: PolicyComposer = PolicyComposer()
    for policy in policies:
        composer.add(policy)
    return composer


def compose_async(*policies: AsyncResiliencePolicy) -> AsyncPolicyComposer:
    """
    비동기 Policy를 선언적으로 조합하는 편의 함수.

    policies 순서 = 바깥→안쪽 실행 순서.
    동기 compose()와 동일한 선언적 패턴을 비동기로 제공한다.

    Usage::

        result = await compose_async(
            AsyncBulkheadPolicy(async_bulkhead=bulkhead),
            AsyncFallbackPolicy(default_value={"degraded": True}),
        ).execute(async_func)
    """
    composer: AsyncPolicyComposer = AsyncPolicyComposer()
    for policy in policies:
        composer.add(policy)
    return composer
