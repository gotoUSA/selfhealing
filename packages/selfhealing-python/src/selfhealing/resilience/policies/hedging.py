"""
Hedging Policy — 병렬 경쟁 실행 기반 Tail Latency 감소.

동일 요청을 여러 후보에 동시 전송하여 가장 빠른 응답을 채택하는
독립 Policy 구현. FallbackStrategy 상속 없이 ResiliencePolicy Protocol을
직접 구현한다.

기존 HedgingExecutor/AsyncHedgingExecutor를 그대로 재사용하며,
Bulkhead 제어는 per_candidate_policy/overall_policy 주입으로 표현한다.
EventBus 구독은 HedgingConfigUpdateHook으로 분리하여
HedgingPolicy가 EventBus 없이도 동작한다.

구성:
- HedgingPolicy: 동기 Hedging (ResiliencePolicy Protocol 구현)
- AsyncHedgingPolicy: 비동기 Hedging (AsyncResiliencePolicy Protocol 구현)
- HedgingConfigUpdateHook: EventBus CONFIG_UPDATED → HedgingPolicy 설정 갱신 중개

Backpressure 로직(부하 레벨에 따른 delay 조정/비활성화)은 Hedging 고유 로직이므로
Policy 내부에 유지한다. 부하 레벨의 갱신 방법만 HedgingConfigUpdateHook으로 분리한다.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, TypeVar

import structlog

from selfhealing.core.hedging.async_executor import AsyncHedgingExecutor
from selfhealing.core.hedging.config import (
    HedgingCandidate,
    HedgingConfig,
    HedgingMode,
)
from selfhealing.core.hedging.exceptions import HedgingError
from selfhealing.core.hedging.executor import HedgingExecutor
from selfhealing.interfaces.resilience_policy import (
    AsyncResiliencePolicy,
    PolicyContext,
    PolicyOutcome,
    PolicyResult,
    ResiliencePolicy,
)

logger = structlog.get_logger()

T = TypeVar("T")

# BackpressureLevel 순서 (낮음 → 높음)
_LOAD_LEVEL_ORDER: dict[str, int] = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


# =============================================================================
# HedgingPolicy — 동기 Hedging Policy
# =============================================================================


class HedgingPolicy(ResiliencePolicy[T]):
    """
    동기 Hedging Policy — 병렬 경쟁 실행.

    FallbackStrategy 상속 없이 ResiliencePolicy Protocol을 직접 구현한다.
    내부적으로 기존 HedgingExecutor를 재사용한다.

    Bulkhead 제어는 per_candidate_policy/overall_policy 외부 주입으로 표현:
    - per_candidate_policy: 각 후보에 적용할 Policy (Bulkhead, Timeout 등)
    - overall_policy: 전체 헷징에 적용할 Policy (Bulkhead, Timeout 등)

    Backpressure 로직(부하 기반 delay 조정/비활성화)은 Hedging 고유 로직이므로
    내부에 유지한다. 부하 레벨 갱신은 on_config_updated()로 외부에서 주입한다.

    사용 예시::

        hedging = HedgingPolicy(
            candidates=[fetch_region_b, fetch_region_c],
            config=HedgingConfig(mode=HedgingMode.DELAYED, delay=0.1),
            overall_policy=BulkheadPolicy(
                bulkhead=SemaphoreBulkhead("api_bulkhead", max_concurrent=10),
            ),
        )
        result = hedging.execute(fetch_region_a)
    """

    def __init__(
        self,
        candidates: list[Callable[[], T]] | None = None,
        candidate_names: list[str] | None = None,
        config: HedgingConfig | None = None,
        default_value: T | None = None,
        per_candidate_policy: ResiliencePolicy[T] | None = None,
        overall_policy: ResiliencePolicy[T] | None = None,
        initial_load_level: str = "none",
    ):
        """
        Args:
            candidates: 후보 함수 목록.
            candidate_names: 후보 이름 목록 (선택).
            config: 헷징 설정.
            default_value: 모든 후보 실패 시 기본값.
            per_candidate_policy: 각 후보에 적용할 Policy (Bulkhead, Timeout 등).
            overall_policy: 전체 헷징에 적용할 Policy (Bulkhead, Timeout 등).
            initial_load_level: 초기 부하 레벨 ("none"|"low"|"medium"|"high"|"critical").
        """
        self._candidates = candidates or []
        self._candidate_names = candidate_names or []
        self._config = config or HedgingConfig()
        self._default_value = default_value
        self._per_candidate_policy = per_candidate_policy
        self._overall_policy = overall_policy
        self._executor = HedgingExecutor(self._config)
        self._current_load_level: str = initial_load_level

    @property
    def name(self) -> str:
        """Policy 식별자."""
        return "hedging"

    def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        context: PolicyContext | None = None,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        헷징 실행 — ResiliencePolicy Protocol 구현.

        실행 순서:
        1. Backpressure 체크 → 비활성화 시 single 실행
        2. overall_policy가 있으면 전체를 래핑 (Double Wrapping 방지)
        3. 후보 목록 구성 (func이 Primary)
        4. per_candidate_policy가 있으면 각 후보를 래핑
        5. Executor로 병렬 실행
        6. PolicyResult로 변환

        Args:
            func: Primary 실행 함수.
            *args: 함수 위치 인자.
            context: 실행 컨텍스트 (Guard/Hook/Sink에 전파).
            **kwargs: 함수 키워드 인자.

        Returns:
            PolicyResult[T]: 통합 결과. 예외를 던지지 않는다.
        """
        if self._should_disable_hedging():
            return self._execute_single(func, *args, **kwargs)

        if self._overall_policy is not None:
            return self._execute_with_overall_policy(func, *args, **kwargs)

        return self._execute_hedging(func, *args, **kwargs)

    def on_config_updated(self, event: dict[str, Any]) -> None:
        """
        외부(HedgingConfigUpdateHook)에서 설정을 갱신하는 이벤트 핸들러.

        단일 필드 대입은 CPython GIL 하에서 STORE_ATTR 단일 바이트코드
        연산이므로 tearing이 발생하지 않는다.
        단, mode와 delay가 동시에 변경되는 복합 일관성은 보장하지 않는다.

        Args:
            event: 설정 변경 이벤트 (key, value 필드 포함).
        """
        config_key = event.get("key", "")
        config_value = event.get("value")

        if config_key == "hedging.mode" and config_value:
            try:
                self._config.mode = HedgingMode(config_value)
                logger.info(
                    "hedging_policy.mode_changed",
                    config_value=config_value,
                )
            except ValueError:
                logger.warning(
                    "hedging_policy.invalid_mode",
                    config_value=config_value,
                )
        elif config_key == "hedging.delay" and config_value is not None:
            self._config.delay = float(config_value)
            logger.info(
                "hedging_policy.delay_changed",
                config_value=config_value,
            )
        elif config_key == "backpressure.level" and config_value:
            self._current_load_level = config_value.lower()
            logger.info(
                "hedging_policy.load_level_updated",
                self=self._current_load_level,
            )

    # -------------------------------------------------------------------------
    # 내부 실행 메서드
    # -------------------------------------------------------------------------

    def _execute_with_overall_policy(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        overall_policy 적용 — Double Wrapping 방지.

        hedging_as_single()에서 raw 값(T)만 반환하고,
        hedging metadata는 클로저로 캡처하여 최종 result에 병합한다.
        overall_policy.execute()가 PolicyResult(value=PolicyResult(...))로
        이중 포장하는 문제를 방지한다.
        """
        hedging_metadata: dict[str, Any] = {}

        def hedging_as_single() -> T:
            inner = self._execute_hedging(func, *args, **kwargs)
            hedging_metadata.update(inner.metadata)
            if inner.success:
                return inner.value
            raise inner.error or HedgingError("All candidates failed")

        try:
            result = self._overall_policy.execute(hedging_as_single)
        except HedgingError as e:
            if self._default_value is not None:
                return PolicyResult(
                    value=self._default_value,
                    outcome=PolicyOutcome.SUCCESS_WITH_FALLBACK,
                    error=e,
                    executed_policies=["hedging"],
                    metadata={"hedging_all_failed": True},
                )
            return PolicyResult(
                value=None,
                outcome=PolicyOutcome.FAILURE,
                error=e,
                executed_policies=["hedging"],
            )

        # overall_policy REJECTED/TIMEOUT → 그대로 반환 (hedging 미실행)
        if not result.success:
            result.executed_policies.append("hedging")
            return result

        # SUCCESS → hedging metadata 병합
        result.metadata.update(hedging_metadata)
        result.executed_policies.append("hedging")
        return result

    def _execute_hedging(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        실제 헷징 실행 로직.

        Backpressure에 따라 effective delay를 적용하고,
        후보 목록을 구성하여 Executor로 병렬 실행한다.
        """
        original_delay = self._config.delay
        try:
            self._config.delay = self._get_effective_delay()

            candidates = self._build_candidates(func, *args, **kwargs)

            if len(candidates) == 1:
                return self._execute_single(func, *args, **kwargs)

            if self._per_candidate_policy is not None:
                candidates = self._wrap_candidates_with_policy(candidates)

            result = self._executor.execute(candidates)

            return PolicyResult(
                value=result.value,
                outcome=PolicyOutcome.SUCCESS,
                executed_policies=["hedging"],
                metadata={
                    "hedged": result.hedged,
                    "winner": result.source,
                    "latency_ms": result.latency_ms,
                    "hedging_benefit_ms": result.hedging_benefit_ms,
                },
            )
        except HedgingError as e:
            if self._default_value is not None:
                return PolicyResult(
                    value=self._default_value,
                    outcome=PolicyOutcome.SUCCESS_WITH_FALLBACK,
                    error=e,
                    executed_policies=["hedging"],
                    metadata={"hedging_all_failed": True},
                )
            return PolicyResult(
                value=None,
                outcome=PolicyOutcome.FAILURE,
                error=e,
                executed_policies=["hedging"],
            )
        finally:
            self._config.delay = original_delay

    def _wrap_candidates_with_policy(
        self,
        candidates: list[HedgingCandidate],
    ) -> list[HedgingCandidate]:
        """
        각 후보를 per_candidate_policy로 래핑.

        PolicyResult → Raw 값 변환 (Double Wrapping 방지):
        - result.success → result.value 반환 (SUCCESS + SUCCESS_WITH_FALLBACK)
        - REJECTED → RuntimeError raise → Executor가 후보 실패로 처리
        - TIMEOUT → TimeoutError raise
        - 기타 → RuntimeError raise
        """
        wrapped: list[HedgingCandidate] = []
        for candidate in candidates:
            original_fn = candidate.fn
            policy = self._per_candidate_policy

            def policy_wrapped(fn: Callable = original_fn, p: Any = policy) -> T:
                result = p.execute(fn)
                if result.success:
                    return result.value
                if result.outcome == PolicyOutcome.REJECTED:
                    raise RuntimeError(f"Candidate rejected by policy: {result.outcome}")
                if result.outcome == PolicyOutcome.TIMEOUT:
                    raise TimeoutError("Candidate timed out in policy")
                raise RuntimeError(f"Candidate policy failed: {result.outcome}")

            wrapped.append(
                HedgingCandidate(
                    name=candidate.name,
                    fn=policy_wrapped,
                    priority=candidate.priority,
                    metadata=candidate.metadata,
                )
            )
        return wrapped

    def _build_candidates(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> list[HedgingCandidate]:
        """
        후보 목록 구성.

        execute(func, *args, **kwargs)의 func을 Primary로,
        생성자의 candidates를 Secondary로 조합한다.
        func + args를 no-arg callable로 래핑하여
        HedgingCandidate.fn 시그니처에 맞춘다.
        """
        candidates: list[HedgingCandidate] = []

        def primary_fn(f: Callable = func, a: tuple = args, kw: dict = kwargs) -> T:
            return f(*a, **kw)

        candidates.append(
            HedgingCandidate(
                name=self._get_name(0, "primary"),
                fn=primary_fn,
                priority=0,
            )
        )

        for i, fn in enumerate(self._candidates):
            candidates.append(
                HedgingCandidate(
                    name=self._get_name(i + 1, f"candidate_{i + 1}"),
                    fn=fn,
                    priority=i + 1,
                )
            )

        return candidates[: self._config.max_candidates]

    def _execute_single(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """단일 함수 실행 (헷징 없음)."""
        try:
            result = func(*args, **kwargs)
            return PolicyResult(
                value=result,
                outcome=PolicyOutcome.SUCCESS,
                executed_policies=["hedging"],
                metadata={"hedged": False},
            )
        except Exception as e:
            if self._default_value is not None:
                return PolicyResult(
                    value=self._default_value,
                    outcome=PolicyOutcome.SUCCESS_WITH_FALLBACK,
                    error=e,
                    executed_policies=["hedging"],
                    metadata={"hedged": False, "single_failed": True},
                )
            return PolicyResult(
                value=None,
                outcome=PolicyOutcome.FAILURE,
                error=e,
                executed_policies=["hedging"],
            )

    def _get_name(self, index: int, default: str) -> str:
        """후보 이름 반환."""
        if index < len(self._candidate_names):
            return self._candidate_names[index]
        return default

    def _should_disable_hedging(self) -> bool:
        """
        현재 부하 레벨이 disable_on_load_level 이상이면 True 반환.

        높은 부하 상태에서는 헷징을 비활성화하여 리소스 사용을 줄인다.
        """
        current_order = _LOAD_LEVEL_ORDER.get(self._current_load_level, 0)
        disable_order = _LOAD_LEVEL_ORDER.get(self._config.disable_on_load_level, 3)
        return current_order >= disable_order

    def _get_effective_delay(self) -> float:
        """
        현재 부하 레벨에 따른 실제 delay 반환.

        NONE/LOW: 기본 delay
        MEDIUM: delay * delay_multiplier_on_medium
        HIGH: delay * delay_multiplier_on_high
        """
        if self._current_load_level == "medium":
            return self._config.delay * self._config.delay_multiplier_on_medium
        elif self._current_load_level == "high":
            return self._config.delay * self._config.delay_multiplier_on_high
        return self._config.delay


# =============================================================================
# AsyncHedgingPolicy — 비동기 Hedging Policy
# =============================================================================


class AsyncHedgingPolicy:
    """
    비동기 Hedging Policy — AsyncResiliencePolicy Protocol 구현.

    동기 HedgingPolicy와 동일한 구조이나 비동기 실행을 지원한다.
    내부적으로 기존 AsyncHedgingExecutor를 재사용한다.

    소비자 책임(Consumer Responsibility):
    candidates에 전달하는 함수는 반드시 async def여야 한다.
    동기 함수를 혼용하려면 소비자가 asyncio.to_thread()로 래핑하여 주입한다.

    사용 예시::

        hedging = AsyncHedgingPolicy(
            candidates=[async_fetch_b, async_fetch_c],
            config=HedgingConfig(mode=HedgingMode.DELAYED, delay=0.1),
        )
        result = await hedging.execute(async_fetch_a)

    Raises:
        TypeError: per_candidate_policy/overall_policy가
                   AsyncResiliencePolicy Protocol을 준수하지 않을 때.
    """

    def __init__(
        self,
        candidates: list[Callable[[], Awaitable[T]]] | None = None,
        candidate_names: list[str] | None = None,
        config: HedgingConfig | None = None,
        default_value: T | None = None,
        per_candidate_policy: AsyncResiliencePolicy[T] | None = None,
        overall_policy: AsyncResiliencePolicy[T] | None = None,
        initial_load_level: str = "none",
    ):
        """
        Args:
            candidates: 후보 코루틴 함수 목록.
            candidate_names: 후보 이름 목록 (선택).
            config: 헷징 설정.
            default_value: 모든 후보 실패 시 기본값.
            per_candidate_policy: 각 후보에 적용할 비동기 Policy.
            overall_policy: 전체 헷징에 적용할 비동기 Policy.
            initial_load_level: 초기 부하 레벨 ("none"|"low"|"medium"|"high"|"critical").

        Raises:
            TypeError: per_candidate_policy/overall_policy가
                       AsyncResiliencePolicy Protocol을 준수하지 않을 때.
        """
        if per_candidate_policy is not None:
            if not isinstance(per_candidate_policy, AsyncResiliencePolicy):
                raise TypeError(
                    f"per_candidate_policy must implement AsyncResiliencePolicy, "
                    f"got {type(per_candidate_policy).__name__}. "
                    f"동기 Policy를 비동기 환경에서 사용하면 "
                    f"'await policy.execute()'에서 TypeError 발생."
                )
        if overall_policy is not None:
            if not isinstance(overall_policy, AsyncResiliencePolicy):
                raise TypeError(
                    f"overall_policy must implement AsyncResiliencePolicy, " f"got {type(overall_policy).__name__}"
                )

        self._candidates = candidates or []
        self._candidate_names = candidate_names or []
        self._config = config or HedgingConfig()
        self._default_value = default_value
        self._per_candidate_policy = per_candidate_policy
        self._overall_policy = overall_policy
        self._executor = AsyncHedgingExecutor(self._config)
        self._current_load_level: str = initial_load_level

    @property
    def name(self) -> str:
        """Policy 식별자."""
        return "hedging"

    async def execute(
        self,
        func: Callable[..., Awaitable[T]],
        *args: Any,
        context: PolicyContext | None = None,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        비동기 헷징 실행 — AsyncResiliencePolicy Protocol 구현.

        동기 HedgingPolicy.execute()와 동일한 흐름:
        1. Backpressure 체크
        2. overall_policy 적용 (Double Wrapping 방지)
        3. 헷징 실행 (AsyncHedgingExecutor 재사용)

        Args:
            func: Primary 비동기 실행 함수.
            *args: 함수 위치 인자.
            context: 실행 컨텍스트.
            **kwargs: 함수 키워드 인자.

        Returns:
            PolicyResult[T]: 통합 결과. 예외를 던지지 않는다.
        """
        if self._should_disable_hedging():
            return await self._execute_single(func, *args, **kwargs)

        if self._overall_policy is not None:
            return await self._execute_with_overall_policy(func, *args, **kwargs)

        return await self._execute_hedging(func, *args, **kwargs)

    def on_config_updated(self, event: dict[str, Any]) -> None:
        """
        외부(HedgingConfigUpdateHook)에서 설정을 갱신하는 이벤트 핸들러.

        동기 HedgingPolicy.on_config_updated()와 동일한 로직.

        Args:
            event: 설정 변경 이벤트 (key, value 필드 포함).
        """
        config_key = event.get("key", "")
        config_value = event.get("value")

        if config_key == "hedging.mode" and config_value:
            try:
                self._config.mode = HedgingMode(config_value)
                logger.info(
                    "async_hedging_policy.mode_changed",
                    config_value=config_value,
                )
            except ValueError:
                logger.warning(
                    "async_hedging_policy.invalid_mode",
                    config_value=config_value,
                )
        elif config_key == "hedging.delay" and config_value is not None:
            self._config.delay = float(config_value)
            logger.info(
                "async_hedging_policy.delay_changed",
                config_value=config_value,
            )
        elif config_key == "backpressure.level" and config_value:
            self._current_load_level = config_value.lower()
            logger.info(
                "async_hedging_policy.load_level_updated",
                self=self._current_load_level,
            )

    # -------------------------------------------------------------------------
    # 내부 실행 메서드
    # -------------------------------------------------------------------------

    async def _execute_with_overall_policy(
        self,
        func: Callable[..., Awaitable[T]],
        *args: Any,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        overall_policy 적용 — Double Wrapping 방지 (비동기 버전).

        hedging_as_single()에서 raw 값(T)만 반환하고,
        hedging metadata는 클로저로 캡처하여 최종 result에 병합한다.
        """
        hedging_metadata: dict[str, Any] = {}

        async def hedging_as_single() -> T:
            inner = await self._execute_hedging(func, *args, **kwargs)
            hedging_metadata.update(inner.metadata)
            if inner.success:
                return inner.value
            raise inner.error or HedgingError("All candidates failed")

        try:
            result = await self._overall_policy.execute(hedging_as_single)
        except HedgingError as e:
            if self._default_value is not None:
                return PolicyResult(
                    value=self._default_value,
                    outcome=PolicyOutcome.SUCCESS_WITH_FALLBACK,
                    error=e,
                    executed_policies=["hedging"],
                    metadata={"hedging_all_failed": True},
                )
            return PolicyResult(
                value=None,
                outcome=PolicyOutcome.FAILURE,
                error=e,
                executed_policies=["hedging"],
            )

        if not result.success:
            result.executed_policies.append("hedging")
            return result

        result.metadata.update(hedging_metadata)
        result.executed_policies.append("hedging")
        return result

    async def _execute_hedging(
        self,
        func: Callable[..., Awaitable[T]],
        *args: Any,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """실제 비동기 헷징 실행 로직."""
        original_delay = self._config.delay
        try:
            self._config.delay = self._get_effective_delay()

            candidates = self._build_candidates(func, *args, **kwargs)

            if len(candidates) == 1:
                return await self._execute_single(func, *args, **kwargs)

            if self._per_candidate_policy is not None:
                candidates = self._wrap_candidates_with_policy(candidates)

            result = await self._executor.execute(candidates)

            return PolicyResult(
                value=result.value,
                outcome=PolicyOutcome.SUCCESS,
                executed_policies=["hedging"],
                metadata={
                    "hedged": result.hedged,
                    "winner": result.source,
                    "latency_ms": result.latency_ms,
                    "hedging_benefit_ms": result.hedging_benefit_ms,
                },
            )
        except HedgingError as e:
            if self._default_value is not None:
                return PolicyResult(
                    value=self._default_value,
                    outcome=PolicyOutcome.SUCCESS_WITH_FALLBACK,
                    error=e,
                    executed_policies=["hedging"],
                    metadata={"hedging_all_failed": True},
                )
            return PolicyResult(
                value=None,
                outcome=PolicyOutcome.FAILURE,
                error=e,
                executed_policies=["hedging"],
            )
        finally:
            self._config.delay = original_delay

    def _wrap_candidates_with_policy(
        self,
        candidates: list[HedgingCandidate],
    ) -> list[HedgingCandidate]:
        """
        각 후보를 per_candidate_policy로 래핑 (비동기 버전).

        PolicyResult → Raw 값 변환 (Double Wrapping 방지).
        비동기 Policy의 execute()를 await하여 결과를 벗긴다.
        """
        wrapped: list[HedgingCandidate] = []
        for candidate in candidates:
            original_fn = candidate.fn
            policy = self._per_candidate_policy

            async def policy_wrapped(fn: Callable = original_fn, p: Any = policy) -> T:
                result = await p.execute(fn)
                if result.success:
                    return result.value
                if result.outcome == PolicyOutcome.REJECTED:
                    raise RuntimeError(f"Candidate rejected by policy: {result.outcome}")
                if result.outcome == PolicyOutcome.TIMEOUT:
                    raise TimeoutError("Candidate timed out in policy")
                raise RuntimeError(f"Candidate policy failed: {result.outcome}")

            wrapped.append(
                HedgingCandidate(
                    name=candidate.name,
                    fn=policy_wrapped,
                    priority=candidate.priority,
                    metadata=candidate.metadata,
                )
            )
        return wrapped

    def _build_candidates(
        self,
        func: Callable[..., Awaitable[T]],
        *args: Any,
        **kwargs: Any,
    ) -> list[HedgingCandidate]:
        """
        후보 목록 구성 (비동기 버전).

        func + args를 no-arg async callable로 래핑한다.
        """
        candidates: list[HedgingCandidate] = []

        async def primary_fn(f: Callable = func, a: tuple = args, kw: dict = kwargs) -> T:
            return await f(*a, **kw)

        candidates.append(
            HedgingCandidate(
                name=self._get_name(0, "primary"),
                fn=primary_fn,
                priority=0,
            )
        )

        for i, fn in enumerate(self._candidates):
            candidates.append(
                HedgingCandidate(
                    name=self._get_name(i + 1, f"candidate_{i + 1}"),
                    fn=fn,
                    priority=i + 1,
                )
            )

        return candidates[: self._config.max_candidates]

    async def _execute_single(
        self,
        func: Callable[..., Awaitable[T]],
        *args: Any,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """단일 함수 실행 (헷징 없음, 비동기)."""
        try:
            result = await func(*args, **kwargs)
            return PolicyResult(
                value=result,
                outcome=PolicyOutcome.SUCCESS,
                executed_policies=["hedging"],
                metadata={"hedged": False},
            )
        except Exception as e:
            if self._default_value is not None:
                return PolicyResult(
                    value=self._default_value,
                    outcome=PolicyOutcome.SUCCESS_WITH_FALLBACK,
                    error=e,
                    executed_policies=["hedging"],
                    metadata={"hedged": False, "single_failed": True},
                )
            return PolicyResult(
                value=None,
                outcome=PolicyOutcome.FAILURE,
                error=e,
                executed_policies=["hedging"],
            )

    def _get_name(self, index: int, default: str) -> str:
        """후보 이름 반환."""
        if index < len(self._candidate_names):
            return self._candidate_names[index]
        return default

    def _should_disable_hedging(self) -> bool:
        """현재 부하 레벨이 disable_on_load_level 이상이면 True 반환."""
        current_order = _LOAD_LEVEL_ORDER.get(self._current_load_level, 0)
        disable_order = _LOAD_LEVEL_ORDER.get(self._config.disable_on_load_level, 3)
        return current_order >= disable_order

    def _get_effective_delay(self) -> float:
        """현재 부하 레벨에 따른 실제 delay 반환."""
        if self._current_load_level == "medium":
            return self._config.delay * self._config.delay_multiplier_on_medium
        elif self._current_load_level == "high":
            return self._config.delay * self._config.delay_multiplier_on_high
        return self._config.delay


# =============================================================================
# HedgingConfigUpdateHook — EventBus → HedgingPolicy 설정 갱신 중개
# =============================================================================


class HedgingConfigUpdateHook:
    """
    EventBus CONFIG_UPDATED 이벤트를 HedgingPolicy/AsyncHedgingPolicy에 전달하는 Hook.

    HedgingPolicy가 EventBus에 직접 의존하지 않도록 중개한다.
    Fail-Open 원칙: Hook 실패 시 HedgingPolicy 동작에 영향 없다.

    사용 예시::

        hook = HedgingConfigUpdateHook()
        hook.register(hedging_policy)
        hook.start()  # EventBus 구독 시작
    """

    def __init__(self) -> None:
        self._policies: list[HedgingPolicy | AsyncHedgingPolicy] = []

    def register(self, policy: HedgingPolicy | AsyncHedgingPolicy) -> None:
        """설정 갱신을 수신할 Policy 등록."""
        self._policies.append(policy)

    def start(self) -> None:
        """
        EventBus CONFIG_UPDATED 구독 시작.

        EventBus가 없는 환경에서는 아무 동작도 하지 않는다 (Fail-Open).
        """
        try:
            from selfhealing.services.event_bus import EventType, get_event_bus

            bus = get_event_bus()
            bus.subscribe(EventType.CONFIG_UPDATED, self._dispatch)
            logger.debug("hedging_config_update_hook.subscribed")
        except ImportError:
            logger.debug("hedging_config_update_hook.eventbus_available")
        except Exception as e:
            logger.warning(
                "hedging_config_update_hook.failed_subscribe",
                error=e,
            )

    def _dispatch(self, event: Any) -> None:
        """이벤트를 등록된 모든 Policy에 전달 (Fail-Open)."""
        event_data = event.data if hasattr(event, "data") else event
        for policy in self._policies:
            try:
                policy.on_config_updated(event_data)
            except Exception:
                pass  # Fail-Open: Hook 실패가 Policy 동작을 중단시키지 않음
