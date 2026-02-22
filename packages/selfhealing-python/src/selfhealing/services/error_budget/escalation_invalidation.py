"""
Escalation Triggered Invalidation.

Emergency Level이 격상될 때 캐시를 즉시 무효화하는 Push-based Invalidation 로직입니다.
30초 캐시로 인한 '지연된 소진'을 방지합니다.

Features:
- 이벤트 버스 구독 기반 푸시 무효화
- 다중 캐시 대상 지원
- 격상(Escalation)에만 반응 (하강 시 TTL 대기)

Usage:
    from selfhealing.services.error_budget.escalation_invalidation import (
        get_escalation_triggered_invalidation,
        setup_crisis_multiplier_invalidation,
    )

    # 앱 시작 시 설정
    setup_crisis_multiplier_invalidation()

Reference:
    docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.5
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog

logger = structlog.get_logger()


# =============================================================================
# Level Order Constants
# =============================================================================

LEVEL_ORDER = {
    "NORMAL": 0,
    "LEVEL_1": 1,
    "LEVEL_2": 2,
    "LEVEL_3": 3,
}
"""Emergency Level 순서 (격상/하강 판별용)."""


# =============================================================================
# Escalation Triggered Invalidation
# =============================================================================


class EscalationTriggeredInvalidation:
    """
    Emergency 격상 시 캐시 즉시 무효화.

    Emergency Level이 격상될 때 이벤트 버스를 통해 푸시 방식으로
    모든 관련 캐시를 즉시 무효화합니다.

    Features:
    - 이벤트 버스 구독 기반 푸시 무효화
    - 다중 캐시 대상 지원
    - 격상에만 반응 (하강은 TTL로 자연 만료)
    - 무효화 기록 (메트릭/로깅)

    Reference:
        docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.5
    """

    def __init__(self):
        """EscalationTriggeredInvalidation 초기화."""
        self._invalidation_targets: list[Callable[[], None]] = []
        self._registered = False
        self._invalidation_count = 0

    def register_target(self, invalidate_fn: Callable[[], None]) -> None:
        """
        무효화 대상 등록.

        Args:
            invalidate_fn: 캐시 무효화 함수 (인자 없음)
        """
        if invalidate_fn not in self._invalidation_targets:
            self._invalidation_targets.append(invalidate_fn)
            logger.debug(
                "escalation_invalidation.target_registered",
                count=len(self._invalidation_targets),
            )

    def unregister_target(self, invalidate_fn: Callable[[], None]) -> bool:
        """
        무효화 대상 해제.

        Args:
            invalidate_fn: 해제할 캐시 무효화 함수

        Returns:
            해제 성공 여부
        """
        try:
            self._invalidation_targets.remove(invalidate_fn)
            return True
        except ValueError:
            return False

    def register_event_handler(self) -> bool:
        """
        이벤트 버스에 핸들러 등록.

        Returns:
            등록 성공 여부
        """
        if self._registered:
            return True

        try:
            from selfhealing.services.event_bus import EventType, get_event_bus

            bus = get_event_bus()
            bus.subscribe(
                EventType.EMERGENCY_LEVEL_CHANGED,
                self._on_level_changed,
            )
            self._registered = True

            logger.info("cell_registry.bulkheads_registered")
            return True

        except ImportError:
            logger.warning("escalation_invalidation.event_bus_available_falling")
            return False
        except Exception as e:
            logger.warning(
                "escalation_invalidation.registration_failed",
                error=e,
            )
            return False

    def _on_level_changed(self, event: Any) -> None:
        """
        Emergency Level 변경 이벤트 핸들러.

        Args:
            event: 이벤트 객체 (data에 old_level, new_level 포함)
        """
        data = getattr(event, "data", event) if not isinstance(event, dict) else event
        old_level = data.get("old_level")
        new_level = data.get("new_level")

        # 격상(Escalation)인 경우에만 즉시 무효화
        if self._is_escalation(old_level, new_level):
            self._invalidate_all_caches(
                reason=f"Escalation: {old_level} → {new_level}",
                namespace=data.get("namespace"),
            )

    def _is_escalation(
        self,
        old_level: str | None,
        new_level: str | None,
    ) -> bool:
        """
        격상 여부 확인.

        Args:
            old_level: 이전 레벨
            new_level: 새 레벨

        Returns:
            격상이면 True
        """
        old_order = LEVEL_ORDER.get(old_level, 0)
        new_order = LEVEL_ORDER.get(new_level, 0)

        return new_order > old_order

    def _is_deescalation(
        self,
        old_level: str | None,
        new_level: str | None,
    ) -> bool:
        """
        하강 여부 확인.

        Args:
            old_level: 이전 레벨
            new_level: 새 레벨

        Returns:
            하강이면 True
        """
        old_order = LEVEL_ORDER.get(old_level, 0)
        new_order = LEVEL_ORDER.get(new_level, 0)

        return new_order < old_order

    def _invalidate_all_caches(
        self,
        reason: str,
        namespace: str | None = None,
    ) -> int:
        """
        모든 등록된 캐시 무효화.

        Args:
            reason: 무효화 사유
            namespace: 네임스페이스

        Returns:
            성공적으로 무효화된 대상 수
        """
        logger.warning(
            "escalation_invalidation.invalidating_all_caches",
            reason=reason,
            namespace=namespace,
            count=len(self._invalidation_targets),
        )

        success_count = 0
        for invalidate_fn in self._invalidation_targets:
            try:
                invalidate_fn()
                success_count += 1
            except Exception as e:
                logger.exception(
                    "escalation_invalidation.invalidation_failed",
                    error=e,
                )

        self._invalidation_count += 1

        # 메트릭 기록
        self._record_invalidation_metric(reason)

        return success_count

    def _record_invalidation_metric(self, reason: str) -> None:
        """무효화 메트릭 기록."""
        try:
            from prometheus_client import Counter

            counter = Counter(
                "selfhealing_escalation_cache_invalidation_total",
                "Number of cache invalidations triggered by escalation",
                ["reason_type"],
            )

            # reason에서 타입 추출 (예: "Escalation: LEVEL_1 → LEVEL_3")
            reason_type = "escalation" if "Escalation" in reason else "other"
            counter.labels(reason_type=reason_type).inc()

        except Exception:
            pass  # 메트릭 실패는 무시

    def trigger_manual_invalidation(self, reason: str = "manual") -> int:
        """
        수동 무효화 트리거.

        Args:
            reason: 무효화 사유

        Returns:
            무효화된 대상 수
        """
        return self._invalidate_all_caches(reason=f"Manual: {reason}")

    def get_target_count(self) -> int:
        """등록된 대상 수."""
        return len(self._invalidation_targets)

    def get_invalidation_count(self) -> int:
        """총 무효화 횟수."""
        return self._invalidation_count

    def is_registered(self) -> bool:
        """이벤트 버스 등록 여부."""
        return self._registered


# =============================================================================
# Singleton
# =============================================================================

_escalation_invalidation: EscalationTriggeredInvalidation | None = None


def get_escalation_triggered_invalidation() -> EscalationTriggeredInvalidation:
    """EscalationTriggeredInvalidation 싱글톤 반환."""
    global _escalation_invalidation
    if _escalation_invalidation is None:
        _escalation_invalidation = EscalationTriggeredInvalidation()
        _escalation_invalidation.register_event_handler()
    return _escalation_invalidation


def reset_escalation_invalidation() -> None:
    """싱글톤 초기화 (테스트용)."""
    global _escalation_invalidation
    _escalation_invalidation = None


# =============================================================================
# CrisisMultiplierProvider 연동 (리뷰 §3.1 반영)
# =============================================================================


def setup_crisis_multiplier_invalidation() -> bool:
    """
    CrisisMultiplierProvider를 EscalationTriggeredInvalidation에 등록.

    애플리케이션 시작 시 호출하여 Push-based Invalidation을 활성화합니다.

    Returns:
        설정 성공 여부

    Usage:
        # app startup
        from selfhealing.services.error_budget.escalation_invalidation import (
            setup_crisis_multiplier_invalidation,
        )
        setup_crisis_multiplier_invalidation()

    Reference:
        리뷰 §3.1: "Emergency Level이 격상될 때, 30초 캐시를 기다리지 않고
        즉시 무효화하는 로직을 EmergencyModeTracker와 연동"
    """
    try:
        from selfhealing.services.error_budget.multiplier import (
            get_crisis_multiplier_provider,
        )

        invalidation = get_escalation_triggered_invalidation()
        provider = get_crisis_multiplier_provider()

        # CrisisMultiplierProvider의 캐시를 무효화 대상으로 등록
        invalidation.register_target(provider.invalidate_cache)

        logger.info("escalation_invalidation.crisismultiplierprovider_registered_push_based")
        return True

    except ImportError as e:
        logger.warning(
            "escalation_invalidation.crisismultiplierprovider_available",
            error=e,
        )
        return False
    except Exception as e:
        logger.exception(
            "escalation_invalidation.setup_failed",
            error=e,
        )
        return False
