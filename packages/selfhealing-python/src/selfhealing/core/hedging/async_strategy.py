"""
Async Hedging Strategy - 비동기 FallbackStrategy 확장.

비동기 환경(FastAPI, aiohttp 등)에서 사용하는 헷징 전략입니다.
Bulkhead/Backpressure 연동 및 동적 설정 변경을 지원합니다.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, TypeVar

from selfhealing.core.fallback_strategy import (
    FallbackMode,
    FallbackResult,
)
from selfhealing.core.hedging.async_executor import AsyncHedgingExecutor
from selfhealing.core.hedging.config import (
    HedgingCandidate,
    HedgingConfig,
)
from selfhealing.core.hedging.exceptions import (
    HedgingError,
)
from selfhealing.core.hedging.metrics import (
    record_hedging_disabled,
    record_hedging_execution,
    record_hedging_failure,
    record_hedging_hedged,
    record_hedging_success,
    record_hedging_benefit,
)
from selfhealing.core.hedging.otel import hedging_span, record_hedging_result

logger = logging.getLogger(__name__)

T = TypeVar("T")

# BackpressureLevel 순서 (낮음 → 높음)
_LOAD_LEVEL_ORDER = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


class AsyncHedgingStrategy:
    """
    비동기 헷징 전략 - Bulkhead/Backpressure 연동.

    비동기 환경(FastAPI, aiohttp 등)에서 사용하는 헷징 전략입니다.

    Usage:
        strategy = AsyncHedgingStrategy(
            candidates=[
                lambda: async_fetch_from_region_a(),
                lambda: async_fetch_from_region_b(),
            ],
            config=HedgingConfig(
                mode=HedgingMode.DELAYED,
            ),
        )

        result = await strategy.execute(
            primary_fn=lambda: async_fetch_from_region_a()
        )
    """

    def __init__(
        self,
        candidates: list[Callable[[], Awaitable[T]]] | None = None,
        candidate_names: list[str] | None = None,
        config: HedgingConfig | None = None,
        default_value: T | None = None,
    ):
        """
        Args:
            candidates: 후보 코루틴 함수 목록 (첫 번째가 Primary)
            candidate_names: 후보 이름 목록 (선택)
            config: 헷징 설정
            default_value: 모든 후보 실패 시 기본값
        """
        self._candidates = candidates or []
        self._candidate_names = candidate_names or []
        self._config = config or HedgingConfig()
        self._default_value = default_value
        self._executor = AsyncHedgingExecutor(self._config)

        # Bulkhead 레지스트리 (선택적)
        self._bulkhead_registry = None
        if self._config.bulkhead_name:
            try:
                from selfhealing.resilience.bulkhead.registry import (
                    get_bulkhead_registry,
                )

                self._bulkhead_registry = get_bulkhead_registry()
            except ImportError:
                logger.debug("[AsyncHedgingStrategy] Bulkhead registry not available")

        # 현재 Backpressure 레벨
        self._current_load_level: str = "none"

        # EventBus 구독 (동적 설정 변경)
        self._subscribe_config_updates()

    def _subscribe_config_updates(self) -> None:
        """EventBus CONFIG_UPDATED 구독."""
        try:
            from selfhealing.services.event_bus import (
                EventType,
                get_event_bus,
            )

            bus = get_event_bus()
            bus.subscribe(
                EventType.CONFIG_UPDATED,
                self._on_config_updated,
            )
            logger.debug("[AsyncHedgingStrategy] Subscribed to CONFIG_UPDATED")
        except ImportError:
            logger.debug("[AsyncHedgingStrategy] EventBus not available")
        except Exception as e:
            logger.warning(f"[AsyncHedgingStrategy] Failed to subscribe: {e}")

    def _on_config_updated(self, event: any) -> None:
        """설정 변경 이벤트 처리."""
        try:
            event_data = event.data if hasattr(event, "data") else event
            config_key = event_data.get("key", "") if isinstance(event_data, dict) else ""
            config_value = event_data.get("value") if isinstance(event_data, dict) else None

            if config_key == "hedging.mode" and config_value:
                from selfhealing.core.hedging.config import HedgingMode

                try:
                    self._config.mode = HedgingMode(config_value)
                    logger.info(f"[AsyncHedgingStrategy] Mode changed to: {config_value}")
                except ValueError:
                    logger.warning(f"[AsyncHedgingStrategy] Invalid mode: {config_value}")

            elif config_key == "hedging.delay" and config_value is not None:
                self._config.delay = float(config_value)
                logger.info(f"[AsyncHedgingStrategy] Delay changed to: {config_value}")

            elif config_key == "backpressure.level" and config_value:
                self._current_load_level = config_value.lower()
                logger.info(f"[AsyncHedgingStrategy] Load level updated: " f"{self._current_load_level}")
        except Exception as e:
            logger.warning(f"[AsyncHedgingStrategy] Config update error: {e}")

    def _get_effective_delay(self) -> float:
        """현재 부하 레벨에 따른 실제 delay 반환."""
        if self._current_load_level == "medium":
            return self._config.delay * self._config.delay_multiplier_on_medium
        elif self._current_load_level == "high":
            return self._config.delay * self._config.delay_multiplier_on_high
        return self._config.delay

    def _should_disable_hedging(self) -> bool:
        """현재 부하 레벨이 disable_on_load_level 이상인지 확인."""
        current_order = _LOAD_LEVEL_ORDER.get(self._current_load_level, 0)
        disable_order = _LOAD_LEVEL_ORDER.get(self._config.disable_on_load_level, 3)
        return current_order >= disable_order

    async def _acquire_bulkhead_async(self) -> bool:
        """
        Bulkhead 슬롯 획득 (비동기).

        Returns:
            획득 성공 여부
        """
        if not self._bulkhead_registry or not self._config.bulkhead_name:
            return True

        try:
            bulkhead = self._bulkhead_registry.get(self._config.bulkhead_name)
            if bulkhead:
                # async 메서드가 있으면 사용
                if hasattr(bulkhead, "try_acquire_async"):
                    return await bulkhead.try_acquire_async()
                return bulkhead.try_acquire()
        except Exception as e:
            logger.warning(f"[AsyncHedgingStrategy] Bulkhead acquire error: {e}")
        return True

    def _release_bulkhead(self) -> None:
        """Bulkhead 슬롯 해제."""
        if not self._bulkhead_registry or not self._config.bulkhead_name:
            return

        try:
            bulkhead = self._bulkhead_registry.get(self._config.bulkhead_name)
            if bulkhead:
                bulkhead.release()
        except Exception as e:
            logger.warning(f"[AsyncHedgingStrategy] Bulkhead release error: {e}")

    async def execute(
        self,
        primary_fn: Callable[[], Awaitable[T]],
        fallback_fn: Callable[[], Awaitable[T]] | None = None,
        default_value: T | None = None,
    ) -> FallbackResult[T]:
        """
        비동기 헷징 실행 - Bulkhead/Backpressure 연동 포함.

        Args:
            primary_fn: Primary 코루틴 함수
            fallback_fn: Fallback 코루틴 함수 (선택)
            default_value: 기본값 (선택)

        Returns:
            FallbackResult
        """
        # Backpressure 체크: 높은 부하 시 헷징 비활성화
        if self._should_disable_hedging():
            logger.warning(f"[AsyncHedgingStrategy] Hedging disabled due to load: " f"{self._current_load_level}")
            record_hedging_disabled(self._current_load_level)
            return await self._execute_single(primary_fn, default_value)

        # Bulkhead 획득
        if not self._config.acquire_bulkhead_per_candidate:
            if not await self._acquire_bulkhead_async():
                logger.warning("[AsyncHedgingStrategy] Bulkhead full, fallback to single")
                return await self._execute_single(primary_fn, default_value)

        original_delay = self._config.delay

        try:
            # 실제 delay를 부하 레벨에 따라 조정
            effective_delay = self._get_effective_delay()
            self._config.delay = effective_delay

            # 후보 목록 구성
            candidates = self._build_candidates(primary_fn, fallback_fn)

            # 후보가 1개뿐이면 일반 실행
            if len(candidates) == 1:
                return await self._execute_single(candidates[0].fn, default_value)

            # 메트릭 기록
            record_hedging_execution(self._config.mode.value)

            # OTel span으로 감싸서 헷징 실행
            with hedging_span(
                mode=self._config.mode.value,
                candidates_count=len(candidates),
            ) as span:
                result = await self._executor.execute(candidates)

                # OTel 속성 추가
                record_hedging_result(
                    span,
                    winner=result.source,
                    latency_ms=result.latency_ms,
                    hedged=result.hedged,
                    benefit_ms=result.hedging_benefit_ms,
                )

                # 메트릭 기록
                record_hedging_success(result.source, result.latency_ms / 1000.0)
                if result.hedged:
                    record_hedging_hedged()
                if result.hedging_benefit_ms:
                    record_hedging_benefit(result.hedging_benefit_ms)

                return FallbackResult(
                    value=result.value,
                    used_fallback=result.hedged,
                    fallback_mode=FallbackMode.HEDGE if result.hedged else None,
                    original_error=None,
                )

        except HedgingError as e:
            logger.warning(f"[AsyncHedgingStrategy] All candidates failed: {e}")
            record_hedging_failure()

            final_default = default_value or self._default_value
            if final_default is not None:
                return FallbackResult(
                    value=final_default,
                    used_fallback=True,
                    fallback_mode=FallbackMode.USE_DEFAULT,
                    original_error=str(e),
                )

            return FallbackResult(
                value=None,
                used_fallback=True,
                fallback_mode=FallbackMode.FAIL_FAST,
                original_error=str(e),
            )

        finally:
            # Bulkhead 해제
            if not self._config.acquire_bulkhead_per_candidate:
                self._release_bulkhead()

            # delay 복원
            self._config.delay = original_delay

    def _build_candidates(
        self,
        primary_fn: Callable[[], Awaitable[T]],
        fallback_fn: Callable[[], Awaitable[T]] | None,
    ) -> list[HedgingCandidate]:
        """후보 목록 구성."""
        candidates: list[HedgingCandidate] = []

        # Primary 추가
        candidates.append(
            HedgingCandidate(
                name=self._get_name(0, "primary"),
                fn=primary_fn,
                priority=0,
            )
        )

        # 등록된 후보들 추가
        for i, fn in enumerate(self._candidates):
            if fn != primary_fn:
                candidates.append(
                    HedgingCandidate(
                        name=self._get_name(i + 1, f"candidate_{i + 1}"),
                        fn=fn,
                        priority=i + 1,
                    )
                )

        # fallback_fn 추가
        if fallback_fn:
            candidates.append(
                HedgingCandidate(
                    name="fallback",
                    fn=fallback_fn,
                    priority=len(candidates),
                )
            )

        return candidates[: self._config.max_candidates]

    async def _execute_single(self, fn: Callable[[], Awaitable[T]], default_value: T | None) -> FallbackResult[T]:
        """단일 함수 실행 (헷징 없음)."""
        try:
            result = await fn()
            return FallbackResult(value=result, used_fallback=False)
        except Exception as e:
            final_default = default_value or self._default_value
            if final_default is not None:
                return FallbackResult(
                    value=final_default,
                    used_fallback=True,
                    fallback_mode=FallbackMode.USE_DEFAULT,
                    original_error=str(e),
                )
            return FallbackResult(
                value=None,
                used_fallback=True,
                fallback_mode=FallbackMode.FAIL_FAST,
                original_error=str(e),
            )

    def _get_name(self, index: int, default: str) -> str:
        """후보 이름 반환."""
        if index < len(self._candidate_names):
            return self._candidate_names[index]
        return default
