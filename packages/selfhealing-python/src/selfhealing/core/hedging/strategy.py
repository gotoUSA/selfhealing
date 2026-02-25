"""
Hedging Strategy - FallbackStrategy 확장.

기존 FallbackStrategy와 호환되면서 Hedging 기능을 추가합니다.
Bulkhead/Backpressure 연동 및 동적 설정 변경을 지원합니다.

.. deprecated:: 2.0
    HedgingStrategy는 deprecated 됩니다.
    HedgingPolicy(resilience/policies/hedging.py)를 사용하세요.
    HedgingPolicy는 per_candidate_policy/overall_policy를 통한
    Bulkhead/Timeout Policy 조합을 지원합니다.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

import structlog

from selfhealing.core.fallback_strategy import (
    FallbackMode,
    FallbackResult,
    FallbackStrategy,
)
from selfhealing.core.hedging.config import (
    HedgingCandidate,
    HedgingConfig,
)
from selfhealing.core.hedging.exceptions import (
    HedgingError,
)
from selfhealing.core.hedging.executor import HedgingExecutor
from selfhealing.core.hedging.metrics import (
    record_hedging_benefit,
    record_hedging_disabled,
    record_hedging_execution,
    record_hedging_failure,
    record_hedging_hedged,
    record_hedging_success,
)
from selfhealing.core.hedging.otel import hedging_span, record_hedging_result

if TYPE_CHECKING:
    from selfhealing.resilience.policies.hedging import HedgingPolicy

logger = structlog.get_logger()

T = TypeVar("T")

# BackpressureLevel 순서 (낮음 → 높음)
_LOAD_LEVEL_ORDER = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


class HedgingStrategy(FallbackStrategy):
    """
    헷징 전략 - Bulkhead/Backpressure 연동 및 동적 설정 변경 지원.

    FallbackStrategy를 상속하여 기존 코드와 호환되면서
    병렬 헷징 기능을 제공합니다.

    .. deprecated:: 2.0
        HedgingPolicy를 대신 사용하세요.
        HedgingPolicy는 per_candidate_policy와 overall_policy를 통한
        Bulkhead/Timeout Policy 조합을 지원합니다.

    Usage::

        strategy = HedgingStrategy(
            candidates=[
                lambda: fetch_from_region_a(),
                lambda: fetch_from_region_b(),
            ],
            config=HedgingConfig(
                mode=HedgingMode.DELAYED,
                bulkhead_name="api_bulkhead",
                disable_on_load_level="high",
            ),
        )

        result = strategy.execute(primary_fn=lambda: fetch_from_region_a())
    """

    def __init__(
        self,
        candidates: list[Callable[[], T]] | None = None,
        candidate_names: list[str] | None = None,
        config: HedgingConfig | None = None,
        default_value: T | None = None,
    ):
        """
        Args:
            candidates: 후보 함수 목록 (첫 번째가 Primary)
            candidate_names: 후보 이름 목록 (선택)
            config: 헷징 설정
            default_value: 모든 후보 실패 시 기본값
        """
        warnings.warn(
            "HedgingStrategy is deprecated. Use HedgingPolicy instead. "
            "HedgingPolicy supports per_candidate_policy and overall_policy "
            "for Bulkhead/Timeout composition.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._candidates = candidates or []
        self._candidate_names = candidate_names or []
        self._config = config or HedgingConfig()
        self._default_value = default_value
        self._executor = HedgingExecutor(self._config)

        # Bulkhead 레지스트리 (선택적)
        self._bulkhead_registry = None
        if self._config.bulkhead_name:
            try:
                from selfhealing.resilience.bulkhead.registry import (
                    get_bulkhead_registry,
                )

                self._bulkhead_registry = get_bulkhead_registry()
            except ImportError:
                logger.debug("hedging_strategy.bulkhead_registry_available")

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
            logger.debug("hedging_strategy.subscribed")
        except ImportError:
            logger.debug("hedging_strategy.eventbus_available")
        except Exception as e:
            logger.warning(
                "hedging_strategy.failed_subscribe",
                error=e,
            )

    def _on_config_updated(self, event: any) -> None:
        """
        설정 변경 이벤트 처리.

        지원 항목:
            - hedging.mode: 헷징 모드 변경
            - hedging.delay: delay 변경
            - backpressure.level: Backpressure 레벨 변경
        """
        try:
            event_data = event.data if hasattr(event, "data") else event
            config_key = event_data.get("key", "") if isinstance(event_data, dict) else ""
            config_value = event_data.get("value") if isinstance(event_data, dict) else None

            if config_key == "hedging.mode" and config_value:
                from selfhealing.core.hedging.config import HedgingMode

                try:
                    self._config.mode = HedgingMode(config_value)
                    logger.info(
                        "hedging_strategy.mode_changed",
                        config_value=config_value,
                    )
                except ValueError:
                    logger.warning(
                        "hedging_strategy.invalid_mode",
                        config_value=config_value,
                    )

            elif config_key == "hedging.delay" and config_value is not None:
                self._config.delay = float(config_value)
                logger.info(
                    "hedging_strategy.delay_changed",
                    config_value=config_value,
                )

            elif config_key == "backpressure.level" and config_value:
                self._current_load_level = config_value.lower()
                logger.info(
                    "hedging_strategy.load_level_updated",
                    current_load_level=self._current_load_level,
                )
        except Exception as e:
            logger.warning(
                "hedging_strategy.config_update_error",
                error=e,
            )

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

    def _should_disable_hedging(self) -> bool:
        """
        현재 부하 레벨이 disable_on_load_level 이상인지 확인.

        높은 부하 상태에서는 헷징을 비활성화하여
        리소스 사용을 줄입니다.
        """
        current_order = _LOAD_LEVEL_ORDER.get(self._current_load_level, 0)
        disable_order = _LOAD_LEVEL_ORDER.get(self._config.disable_on_load_level, 3)
        return current_order >= disable_order

    def _acquire_bulkhead(self) -> bool:
        """
        Bulkhead 슬롯 획득.

        Returns:
            획득 성공 여부
        """
        if not self._bulkhead_registry or not self._config.bulkhead_name:
            return True

        try:
            bulkhead = self._bulkhead_registry.get(self._config.bulkhead_name)
            if bulkhead:
                return bulkhead.try_acquire()
        except Exception as e:
            logger.warning(
                "hedging_strategy.bulkhead_acquire_error",
                error=e,
            )
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
            logger.warning(
                "hedging_strategy.bulkhead_release_error",
                error=e,
            )

    def execute(
        self,
        primary_fn: Callable[[], T],
        fallback_fn: Callable[[], T] | None = None,
        default_value: T | None = None,
    ) -> FallbackResult[T]:
        """
        헷징 실행 - Bulkhead/Backpressure 연동 포함.

        Args:
            primary_fn: Primary 함수
            fallback_fn: Fallback 함수 (선택)
            default_value: 기본값 (선택)

        Returns:
            FallbackResult
        """
        # Backpressure 체크: 높은 부하 시 헷징 비활성화
        if self._should_disable_hedging():
            logger.warning(
                "hedging_strategy.hedging_disabled_due_load",
                current_load_level=self._current_load_level,
            )
            record_hedging_disabled(self._current_load_level)
            # Primary만 실행 (헷징 없이)
            return self._execute_single(primary_fn, default_value)

        # Bulkhead 획득 (전체 헷징에 대해)
        if not self._config.acquire_bulkhead_per_candidate:
            if not self._acquire_bulkhead():
                logger.warning("hedging_strategy.bulkhead_full_fallback_single")
                return self._execute_single(primary_fn, default_value)

        original_delay = self._config.delay

        try:
            # 실제 delay를 부하 레벨에 따라 조정
            effective_delay = self._get_effective_delay()
            self._config.delay = effective_delay

            # 후보 목록 구성
            candidates = self._build_candidates(primary_fn, fallback_fn)

            # 후보가 1개뿐이면 일반 실행
            if len(candidates) == 1:
                return self._execute_single(candidates[0].fn, default_value)

            # 메트릭 기록
            record_hedging_execution(self._config.mode.value)

            # OTel span으로 감싸서 헷징 실행
            with hedging_span(
                mode=self._config.mode.value,
                candidates_count=len(candidates),
            ) as span:
                result = self._executor.execute(candidates)

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
            logger.warning(
                "hedging_strategy.all_candidates_failed",
                error=e,
            )
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
        primary_fn: Callable[[], T],
        fallback_fn: Callable[[], T] | None,
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

    def _execute_single(self, fn: Callable[[], T], default_value: T | None) -> FallbackResult[T]:
        """단일 함수 실행 (헷징 없음)."""
        try:
            result = fn()
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


class HedgingStrategyCompat:
    """
    기존 HedgingStrategy와 호환되는 래퍼.

    HedgingPolicy를 내부적으로 사용하면서 기존 HedgingStrategy의
    execute(primary_fn, fallback_fn, default_value) → FallbackResult
    시그니처를 유지한다.

    .. deprecated:: 2.0
        HedgingPolicy를 직접 사용하세요.
    """

    def __init__(self, policy: HedgingPolicy):
        """
        Args:
            policy: HedgingPolicy 인스턴스.
        """
        self._policy = policy

    def execute(
        self,
        primary_fn: Callable[[], Any],
        fallback_fn: Callable[[], Any] | None = None,
        default_value: Any | None = None,
    ) -> FallbackResult:
        """
        기존 FallbackStrategy.execute() 시그니처 호환.

        HedgingPolicy.execute()를 호출하고 결과를 FallbackResult로 변환한다.
        """
        warnings.warn(
            "HedgingStrategyCompat is deprecated. Use HedgingPolicy instead.",
            DeprecationWarning,
            stacklevel=2,
        )

        result = self._policy.execute(primary_fn)
        return self._to_fallback_result(result)

    @staticmethod
    def _to_fallback_result(result: Any) -> FallbackResult:
        """PolicyResult → FallbackResult 변환."""
        from selfhealing.interfaces.resilience_policy import PolicyOutcome

        if result.outcome == PolicyOutcome.SUCCESS:
            hedged = result.metadata.get("hedged", False)
            return FallbackResult(
                value=result.value,
                used_fallback=hedged,
                fallback_mode=FallbackMode.HEDGE if hedged else None,
            )
        elif result.outcome == PolicyOutcome.SUCCESS_WITH_FALLBACK:
            return FallbackResult(
                value=result.value,
                used_fallback=True,
                fallback_mode=FallbackMode.USE_DEFAULT,
                original_error=str(result.error) if result.error else None,
            )
        else:
            return FallbackResult(
                value=None,
                used_fallback=True,
                fallback_mode=FallbackMode.FAIL_FAST,
                original_error=str(result.error) if result.error else None,
            )
