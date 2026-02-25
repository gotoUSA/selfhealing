"""
Governance Service

비상 모드(Emergency Mode) 만료 체크 및 자동 복구를 위한 서비스.

Thin Task, Fat Service 원칙:
    - Celery Task (governance.py)는 단순히 이 서비스를 호출
    - 모든 비즈니스 로직과 상태 관리는 이 서비스에서 수행

Features:
    - 비상 모드 만료 자동 체크
    - 4시간/6시간/8시간 경과에 따른 경고 및 자동 복구
    - 알림 발송 (Slack, Email)
    - 감사 로깅

Reference:
- docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_ROADMAP.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from selfhealing.services.governance.checks import GovernanceCheckMixin

logger = structlog.get_logger()


# =============================================================================
# Result Types
# =============================================================================


@dataclass
class ExpiryCheckResult:
    """비상 모드 만료 체크 결과."""

    is_active: bool
    """비상 모드 활성화 여부."""

    actions_taken: list[dict[str, Any]] = field(default_factory=list)
    """수행된 액션 목록."""

    hours_elapsed: float = 0.0
    """경과 시간 (시간 단위)."""

    hours_remaining: float = 8.0
    """자동 복구까지 남은 시간."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "is_active": self.is_active,
            "actions_taken": self.actions_taken,
            "hours_elapsed": self.hours_elapsed,
            "hours_remaining": self.hours_remaining,
        }


@dataclass
class GovernanceNotificationResult:
    """알림 발송 결과."""

    sent: bool
    channels: list[str] = field(default_factory=list)
    error: str | None = None


# =============================================================================
# Governance Service
# =============================================================================


class GovernanceService(GovernanceCheckMixin):
    """
    거버넌스 서비스.

    비상 모드 만료 체크, 자동 복구, 알림 발송을 담당합니다.

    Usage:
        service = get_governance_service()

        # 만료 체크 및 자동 복구
        result = service.check_emergency_mode_expiry()

        # 수동 비상 모드 활성화
        service.activate_emergency(level=2, reason="DB Overload", actor="admin")

        # 수동 비상 모드 해제
        service.deactivate_emergency(actor="admin")
    """

    _instance: GovernanceService | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._config = self._load_governance_config()
        self._initialized = True

    def _load_governance_config(self) -> dict[str, Any]:
        """거버넌스 설정 로드."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()
            return manager.get_config("governance") or {}
        except Exception as e:
            logger.warning(
                "governance_service.load_config",
                error=e,
            )
            return self._get_default_config()

    def _get_default_config(self) -> dict[str, Any]:
        """기본 거버넌스 설정."""
        return {
            "emergency_expiry_hours": 8,
            "warning_hours": 4,
            "final_warning_hours": 6,
            "notify_channels": ["slack", "email"],
        }

    def _get_emergency_tracker(self):
        """EmergencyTracker 인스턴스 가져오기."""
        from selfhealing.services.governance import get_emergency_tracker

        return get_emergency_tracker()

    # =========================================================================
    # Emergency Mode Expiry Check (Main Logic)
    # =========================================================================

    def check_emergency_mode_expiry(self) -> ExpiryCheckResult:
        """
        비상 모드 만료 체크 및 자동 복구 수행.

        이 메서드는 Celery Beat을 통해 15분 주기로 호출됩니다.

        Actions:
        1. 비상 모드 비활성 → 조기 반환
        2. 4시간 경과 → 경고 알림 발송
        3. 6시간 경과 → 최종 경고 알림 발송
        4. 8시간 경과 → 자동 NORMAL 모드 복구

        Returns:
            ExpiryCheckResult
        """
        tracker = self._get_emergency_tracker()
        status = tracker.check_expiry_status()

        result = ExpiryCheckResult(
            is_active=status["is_active"],
            hours_elapsed=status.get("hours_elapsed", 0),
            hours_remaining=status.get("hours_remaining", 8),
        )

        if not status["is_active"]:
            logger.debug("governance_service.emergency_mode_active_skipping")
            return result

        logger.info(
            "governance_service.emergency_mode_expiry_check",
            hours_elapsed=result.hours_elapsed,
            hours_remaining=result.hours_remaining,
        )

        # Action 1: Auto-restore (highest priority - 8시간)
        if status.get("should_auto_restore", False):
            logger.warning("governance_service.emergency_mode_expired_auto")
            restore_result = tracker.auto_restore_to_normal()
            result.actions_taken.append(
                {
                    "action": "auto_restore",
                    "result": restore_result,
                }
            )
            self._send_notification(
                event_type="auto_restore",
                title="🔄 긴급 모드 자동 복구",
                message=self._build_auto_restore_message(status),
                status=status,
            )
            return result

        # Action 2: Final warning (6시간)
        if status.get("should_final_warn", False):
            logger.warning("governance_service.final_warning_hours_until")
            tracker.mark_final_warning_sent()
            result.actions_taken.append(
                {
                    "action": "final_warning_sent",
                    "hours_remaining": result.hours_remaining,
                }
            )
            self._send_notification(
                event_type="final_warning",
                title="🔴 긴급 모드 최종 경고",
                message=self._build_final_warning_message(status),
                status=status,
            )

        # Action 3: Warning (4시간)
        if status.get("should_warn", False):
            logger.warning(
                "governance_service.warning_hours_emergency_mode",
                hours_elapsed=result.hours_elapsed,
            )
            tracker.mark_warning_sent()
            result.actions_taken.append(
                {
                    "action": "warning_sent",
                    "hours_elapsed": result.hours_elapsed,
                }
            )
            self._send_notification(
                event_type="warning",
                title="⚠️ 긴급 모드 경고",
                message=self._build_warning_message(status),
                status=status,
            )

        return result

    # =========================================================================
    # Emergency Mode Control
    # =========================================================================

    def activate_emergency(
        self,
        level: int,
        reason: str,
        actor: str,
    ) -> dict[str, Any]:
        """
        비상 모드 활성화.

        Args:
            level: 비상 레벨 (1, 2, 3)
            reason: 활성화 사유
            actor: 활성화한 사용자

        Returns:
            결과 딕셔너리
        """
        try:
            from selfhealing.services.emergency_mode import get_emergency_manager
            from selfhealing.services.emergency_mode.enums import EmergencyLevel

            manager = get_emergency_manager()
            level_enum = EmergencyLevel(level)

            result = manager.activate(
                level=level_enum,
                reason=reason,
                activated_by=actor,
            )

            logger.warning(
                "governance_service.emergency_mode_activated",
                governance_level=level,
                reason=reason,
                actor_id=actor,
            )

            # 알림 발송
            self._send_notification(
                event_type="activated",
                title=f"🚨 긴급 모드 활성화 (Level {level})",
                message=f"사유: {reason}\n활성화: {actor}",
                status={"level": level, "reason": reason, "actor": actor},
            )

            return {
                "success": True,
                "level": level,
                "message": f"Emergency mode activated at level {level}",
            }

        except Exception as e:
            logger.exception(
                "governance_service.failed_activate_emergency",
                error=e,
            )
            return {
                "success": False,
                "error": str(e),
            }

    def deactivate_emergency(
        self,
        actor: str,
        reason: str = "Manual deactivation",
    ) -> dict[str, Any]:
        """
        비상 모드 해제.

        Args:
            actor: 해제한 사용자
            reason: 해제 사유

        Returns:
            결과 딕셔너리
        """
        try:
            from selfhealing.services.emergency_mode import get_emergency_manager

            manager = get_emergency_manager()
            result = manager.deactivate(
                reason=reason,
                deactivated_by=actor,
            )

            logger.info(
                "governance_service.emergency_mode_deactivated",
                actor_id=actor,
                reason=reason,
            )

            # 알림 발송
            self._send_notification(
                event_type="deactivated",
                title="✅ 긴급 모드 해제",
                message=f"해제자: {actor}\n사유: {reason}",
                status={"actor": actor, "reason": reason},
            )

            return {
                "success": True,
                "message": "Emergency mode deactivated",
            }

        except Exception as e:
            logger.exception(
                "governance_service.failed_deactivate_emergency",
                error=e,
            )
            return {
                "success": False,
                "error": str(e),
            }

    # =========================================================================
    # Notification Helpers
    # =========================================================================

    def _build_warning_message(self, status: dict[str, Any]) -> str:
        """4시간 경고 메시지 생성."""
        return (
            f"긴급 모드가 {status.get('hours_elapsed', 0):.1f}시간 동안 유지되고 있습니다.\n"
            f"전환자: {status.get('activated_by', 'N/A')}\n"
            f"자동 복귀까지: {status.get('hours_remaining', 0):.1f}시간\n"
            f"Admin 확인이 필요합니다."
        )

    def _build_final_warning_message(self, status: dict[str, Any]) -> str:
        """6시간 최종 경고 메시지 생성."""
        return (
            f"2시간 후 자동으로 NORMAL 모드로 복귀합니다.\n"
            f"전환자: {status.get('activated_by', 'N/A')}\n"
            f"경과 시간: {status.get('hours_elapsed', 0):.1f}시간\n"
            f"즉시 조치가 필요합니다."
        )

    def _build_auto_restore_message(self, status: dict[str, Any]) -> str:
        """자동 복구 메시지 생성."""
        return (
            f"긴급 모드가 만료되어 자동으로 NORMAL 모드로 복귀했습니다.\n"
            f"이전 전환자: {status.get('activated_by', 'N/A')}\n"
            f"경과 시간: {status.get('hours_elapsed', 0):.1f}시간\n"
            f"Safe Default가 적용되었습니다."
        )

    def _send_notification(
        self,
        event_type: str,
        title: str,
        message: str,
        status: dict[str, Any],
    ) -> GovernanceNotificationResult:
        """
        알림 발송.

        TODO: 실제 Slack/Email 연동 구현 필요
        """
        try:
            channels = self._config.get("notify_channels", ["slack", "email"])

            logger.info(
                "governance_service.notification",
                event_type=event_type,
                title=title,
                channels=channels,
            )

            # TODO: 실제 알림 서비스 연동
            # from selfhealing.services.notification import send_notification
            # send_notification(
            #     title=title,
            #     message=message,
            #     channels=channels,
            #     severity="critical" if event_type == "auto_restore" else "warning",
            # )

            return GovernanceNotificationResult(sent=True, channels=channels)

        except Exception as e:
            logger.exception(
                "governance_service.failed_send_notification",
                error=e,
            )
            return GovernanceNotificationResult(sent=False, error=str(e))


# =============================================================================
# Factory Function
# =============================================================================


_governance_service_instance: GovernanceService | None = None


def get_governance_service() -> GovernanceService:
    """
    GovernanceService 싱글톤 인스턴스 반환.

    Returns:
        GovernanceService instance
    """
    global _governance_service_instance
    if _governance_service_instance is None:
        _governance_service_instance = GovernanceService()
    return _governance_service_instance


__all__ = [
    "GovernanceService",
    "ExpiryCheckResult",
    "GovernanceNotificationResult",
    "get_governance_service",
]
