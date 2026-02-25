"""
Rate Limit 에스컬레이션 핸들러.

연속 429 임계치 도달 시 PagerDuty/Slack 알림을 발송합니다.

기능:
- RATE_LIMIT_429 이벤트 구독
- 연속 429 횟수 기반 에스컬레이션 판단
- 중복 에스컬레이션 방지
- 복구 후 상태 초기화
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from selfhealing.meta.escalation import (
    EscalationEvent,
    EscalationLevel,
)

if TYPE_CHECKING:
    from selfhealing.meta.escalation import EscalationManager

logger = structlog.get_logger()

# 에스컬레이션 발동 임계값 (연속 429 횟수)
ESCALATION_THRESHOLD_CONSECUTIVE_429S = 10


class RateLimitEscalationHandler:
    """
    Rate Limit 429 에스컬레이션 핸들러.

    EventBus에서 RATE_LIMIT_429 이벤트를 구독하고,
    consecutive_429s가 임계치를 초과하면 PagerDuty로 에스컬레이션합니다.

    사용 예시:
        handler = RateLimitEscalationHandler()
        handler.subscribe()  # EventBus 구독 시작

        # 복구 후 상태 초기화
        handler.reset_escalation("payment_api")
    """

    def __init__(
        self,
        escalation_manager: EscalationManager | None = None,
        threshold: int = ESCALATION_THRESHOLD_CONSECUTIVE_429S,
    ):
        """
        초기화.

        Args:
            escalation_manager: 기존 EscalationManager 인스턴스 (None이면 생성)
            threshold: 에스컬레이션 발동 임계값 (연속 429 횟수)
        """
        self._escalation_manager = escalation_manager
        self._threshold = threshold
        self._escalated_keys: set[str] = set()  # 중복 에스컬레이션 방지

    def _get_escalation_manager(self) -> EscalationManager:
        """EscalationManager lazy 초기화."""
        if self._escalation_manager is None:
            from selfhealing.meta.escalation import EscalationManager

            self._escalation_manager = EscalationManager()
        return self._escalation_manager

    def subscribe(self) -> bool:
        """
        EventBus에 RATE_LIMIT_429 이벤트 구독.

        Returns:
            구독 성공 여부
        """
        try:
            from selfhealing.services.event_bus import EventType, get_event_bus

            bus = get_event_bus()
            bus.subscribe(
                EventType.RATE_LIMIT_429,
                self._handle_rate_limit_429,
                subscriber_id="rate_limit_escalation_handler",
            )
            logger.info(
                "rate_limit_escalation_handler.subscribed",
                threshold=self._threshold,
            )
            return True
        except ImportError:
            logger.debug("rate_limit_escalation_handler.eventbus_available")
            return False
        except Exception as e:
            logger.warning(
                "rate_limit_escalation_handler.subscribe_failed",
                error=e,
            )
            return False

    def _handle_rate_limit_429(self, event_data: dict) -> None:
        """
        429 이벤트 처리 - 임계치 초과 시 에스컬레이션.

        Args:
            event_data: 이벤트 데이터 (key, consecutive_429s, cooldown_until 등)
        """
        key = event_data.get("key", "unknown")
        consecutive = event_data.get("consecutive_429s", 0)

        # 임계치 미만이면 무시
        if consecutive < self._threshold:
            logger.debug(
                "rate_limit_escalation_handler.skipping_escalation",
                escalation_key=key,
                consecutive=consecutive,
                threshold=self._threshold,
            )
            return

        # 이미 에스컬레이션 했으면 중복 방지
        if key in self._escalated_keys:
            logger.debug(
                "rate_limit_escalation_handler.already_escalated_skipping_duplicate",
                escalation_key=key,
            )
            return

        # 에스컬레이션 마킹 (중복 방지)
        self._escalated_keys.add(key)

        # 에스컬레이션 이벤트 생성
        event = EscalationEvent(
            level=EscalationLevel.CRITICAL,
            title=f"Rate Limit Critical: {key}",
            description=(
                f"External API '{key}'에서 연속 {consecutive}회 429 응답 발생. "
                f"시스템이 방어 모드로 전환되었습니다. "
                f"외부 API 상태 확인 및 조치가 필요합니다."
            ),
            component="rate_limit_coordinator",
            details={
                "key": key,
                "consecutive_429s": consecutive,
                "threshold": self._threshold,
                "cooldown_until": event_data.get("cooldown_until"),
                "calculated_delay": event_data.get("calculated_delay"),
            },
        )

        # 에스컬레이션 실행
        manager = self._get_escalation_manager()
        result = manager.escalate(event)

        if result.success:
            logger.critical(
                "rate_limit_escalation_handler.escalated",
                escalation_key=key,
                consecutive=consecutive,
                channels_sent=result.channels_sent,
            )
        else:
            logger.error(
                "rate_limit_escalation_handler.escalation_failed",
                escalation_key=key,
                result_error=result.error_message,
            )

    def reset_escalation(self, key: str) -> None:
        """
        에스컬레이션 상태 초기화 (복구 후 호출).

        Args:
            key: Rate limit key
        """
        if key in self._escalated_keys:
            self._escalated_keys.discard(key)
            logger.info(
                "rate_limit_escalation_handler.reset_escalation",
                escalation_key=key,
            )

    def reset_all_escalations(self) -> None:
        """모든 에스컬레이션 상태 초기화."""
        count = len(self._escalated_keys)
        self._escalated_keys.clear()
        logger.info(
            "rate_limit_escalation_handler.reset_all_escalations_keys",
            escalated_keys_count=count,
        )

    @property
    def escalated_keys(self) -> frozenset[str]:
        """현재 에스컬레이션된 key 목록 (읽기 전용)."""
        return frozenset(self._escalated_keys)

    @property
    def threshold(self) -> int:
        """에스컬레이션 임계값."""
        return self._threshold


# 모듈 레벨 싱글톤 (선택적 사용)
_handler_instance: RateLimitEscalationHandler | None = None


def get_rate_limit_escalation_handler(
    threshold: int = ESCALATION_THRESHOLD_CONSECUTIVE_429S,
) -> RateLimitEscalationHandler:
    """
    싱글톤 RateLimitEscalationHandler 인스턴스 반환.

    Args:
        threshold: 에스컬레이션 임계값

    Returns:
        RateLimitEscalationHandler 인스턴스
    """
    global _handler_instance
    if _handler_instance is None:
        _handler_instance = RateLimitEscalationHandler(threshold=threshold)
    return _handler_instance
