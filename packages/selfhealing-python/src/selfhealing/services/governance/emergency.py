"""
Governance Services - Emergency Mode Tracker & Auto-Recovery.

긴급 모드(STRICT) 전환 추적 및 자동 복귀 기능을 제공합니다.

Features:
- EmergencyModeTracker: 긴급 모드 전환 시각, 전환자 기록
- 자동 만료 체크 및 Safe Default 복귀
- Admin 알림 발송

Reference:
- docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_ROADMAP.md
- AWS Break Glass Pattern
- Google SRE Emergency Access

Usage:
    from selfhealing.services.governance import (
        get_emergency_tracker,
        EmergencyModeTracker,
    )

    tracker = get_emergency_tracker()

    # 긴급 모드 전환 기록
    tracker.record_emergency_activation(
        activated_by="operator_kim",
        reason="High error rate detected",
    )

    # 만료 상태 확인
    status = tracker.check_expiry_status()
    if status["should_auto_restore"]:
        tracker.auto_restore_to_normal()
"""

from __future__ import annotations

import structlog
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

logger = structlog.get_logger()


# =============================================================================
# Constants
# =============================================================================

EMERGENCY_STATE_STORAGE_KEY = "governance:emergency_state"


class OperationMode(str, Enum):
    """운영 모드 정의."""

    NORMAL = "NORMAL"
    STRICT = "STRICT"


# =============================================================================
# Emergency State Dataclass
# =============================================================================


@dataclass
class GovernanceEmergencyState:
    """
    긴급 모드 상태 정보.

    Attributes:
        is_active: 긴급 모드 활성화 여부
        mode: 현재 운영 모드 (NORMAL/STRICT)
        activated_at: 긴급 모드 전환 시각 (ISO format)
        activated_by: 전환한 사용자
        reason: 전환 사유
        warning_sent_at: 경고 알림 발송 시각
        final_warning_sent_at: 최종 경고 발송 시각
        acknowledged_by: 경고 확인한 Admin
        acknowledged_at: 경고 확인 시각
    """

    is_active: bool = False
    mode: str = "NORMAL"
    activated_at: str | None = None
    activated_by: str | None = None
    reason: str | None = None
    warning_sent_at: str | None = None
    final_warning_sent_at: str | None = None
    acknowledged_by: str | None = None
    acknowledged_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GovernanceEmergencyState:
        """Create from dictionary."""
        if not data:
            return cls()
        return cls(
            is_active=data.get("is_active", False),
            mode=data.get("mode", "NORMAL"),
            activated_at=data.get("activated_at"),
            activated_by=data.get("activated_by"),
            reason=data.get("reason"),
            warning_sent_at=data.get("warning_sent_at"),
            final_warning_sent_at=data.get("final_warning_sent_at"),
            acknowledged_by=data.get("acknowledged_by"),
            acknowledged_at=data.get("acknowledged_at"),
        )


# =============================================================================
# Emergency Mode Tracker
# =============================================================================


class EmergencyModeTracker:
    """
    긴급 모드 추적기.

    긴급 모드(STRICT) 전환을 추적하고 자동 복귀를 관리합니다.

    Features:
    - 전환 시각, 전환자 기록
    - 경고 알림 상태 추적
    - 자동 복귀 판단
    - Admin 알림 발송

    Reference:
    - docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_ROADMAP.md
    """

    def __init__(self):
        """Initialize EmergencyModeTracker."""
        self._lock = threading.RLock()
        self._state: GovernanceEmergencyState | None = None
        self._notification_handlers: list[Callable] = []

    def _get_backend(self):
        """Get state backend (lazy import to avoid circular dependency)."""
        from selfhealing.core.state_backend import get_state_backend

        return get_state_backend()

    def _get_governance_config(self) -> dict[str, Any]:
        """Get governance configuration."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()
            return manager.get_governance_config()
        except Exception as e:
            logger.debug(
                "governance.failed_get_governance_config",
                error=e,
            )
            # Return defaults
            return {
                "emergency_expiry_hours": 8,
                "emergency_warning_hours": 4,
                "emergency_final_warning_hours": 6,
                "notify_on_emergency": True,
                "notify_channels": ["slack", "email"],
            }

    def _load_state(self) -> GovernanceEmergencyState:
        """Load state from backend."""
        if self._state is not None:
            return self._state

        backend = self._get_backend()
        data = backend.get(EMERGENCY_STATE_STORAGE_KEY)
        self._state = GovernanceEmergencyState.from_dict(data) if data else GovernanceEmergencyState()
        return self._state

    def _save_state(self, state: GovernanceEmergencyState) -> None:
        """Save state to backend."""
        backend = self._get_backend()
        backend.set(EMERGENCY_STATE_STORAGE_KEY, state.to_dict())
        self._state = state

    def get_current_state(self) -> GovernanceEmergencyState:
        """
        Get current emergency state.

        Returns:
            GovernanceEmergencyState: 현재 긴급 모드 상태
        """
        with self._lock:
            return self._load_state()

    def record_emergency_activation(
        self,
        activated_by: str,
        reason: str = "",
        mode: str = "STRICT",
    ) -> dict[str, Any]:
        """
        Record emergency mode activation.

        Args:
            activated_by: 전환한 사용자 (username 또는 ID)
            reason: 전환 사유
            mode: 전환할 모드 (기본: STRICT)

        Returns:
            dict: 전환 결과
        """
        with self._lock:
            state = self._load_state()

            now = datetime.now(timezone.utc)
            state.is_active = True
            state.mode = mode.upper()
            state.activated_at = now.isoformat()
            state.activated_by = activated_by
            state.reason = reason
            state.warning_sent_at = None
            state.final_warning_sent_at = None
            state.acknowledged_by = None
            state.acknowledged_at = None

            self._save_state(state)

            logger.warning(
                "governance.emergency_mode_activated",
                mode=mode,
                activated_by=activated_by,
                reason=reason,
            )

            # Send notification
            config = self._get_governance_config()
            if config.get("notify_on_emergency", True):
                self._send_notification(
                    event_type="emergency_activated",
                    state=state,
                    config=config,
                )

            return {
                "status": "activated",
                "mode": state.mode,
                "activated_at": state.activated_at,
                "activated_by": state.activated_by,
                "expiry_hours": config.get("emergency_expiry_hours", 8),
            }

    def record_normal_restoration(
        self,
        restored_by: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """
        Record normal mode restoration.

        Args:
            restored_by: 복원한 사용자
            reason: 복원 사유

        Returns:
            dict: 복원 결과
        """
        with self._lock:
            state = self._load_state()
            previous_mode = state.mode
            activated_by = state.activated_by
            activated_at = state.activated_at

            # Reset state
            state.is_active = False
            state.mode = "NORMAL"
            state.activated_at = None
            state.activated_by = None
            state.reason = None
            state.warning_sent_at = None
            state.final_warning_sent_at = None
            state.acknowledged_by = None
            state.acknowledged_at = None

            self._save_state(state)

            logger.info(
                f"[Governance] Normal mode restored: "
                f"by={restored_by}, previous_mode={previous_mode}, "
                f"previously_activated_by={activated_by}"
            )

            # Send notification
            config = self._get_governance_config()
            if config.get("notify_on_emergency", True):
                self._send_notification(
                    event_type="emergency_deactivated",
                    state=state,
                    config=config,
                    extra={
                        "restored_by": restored_by,
                        "reason": reason,
                        "previous_mode": previous_mode,
                        "previous_activated_by": activated_by,
                        "previous_activated_at": activated_at,
                    },
                )

            return {
                "status": "restored",
                "mode": "NORMAL",
                "restored_by": restored_by,
                "previous_mode": previous_mode,
            }

    def check_expiry_status(self) -> dict[str, Any]:
        """
        Check emergency mode expiry status.

        Returns:
            dict: 만료 상태 정보
                - is_active: 긴급 모드 활성화 여부
                - should_warn: 경고 발송 필요 여부
                - should_final_warn: 최종 경고 발송 필요 여부
                - should_auto_restore: 자동 복귀 필요 여부
                - hours_elapsed: 경과 시간
                - hours_remaining: 남은 시간
                - expires_at: 만료 시각 (ISO format)
                - time_remaining_hours: 남은 시간 (hours)
        """
        with self._lock:
            state = self._load_state()

            if not state.is_active or not state.activated_at:
                return {
                    "is_active": False,
                    "should_warn": False,
                    "should_final_warn": False,
                    "should_auto_restore": False,
                    "hours_elapsed": 0,
                    "hours_remaining": 0,
                    "expires_at": None,
                    "time_remaining_hours": None,
                }

            config = self._get_governance_config()
            warning_hours = config.get("emergency_warning_hours", 4)
            final_warning_hours = config.get("emergency_final_warning_hours", 6)
            expiry_hours = config.get("emergency_expiry_hours", 8)

            activated_at = datetime.fromisoformat(state.activated_at)
            now = datetime.now(timezone.utc)
            elapsed = now - activated_at
            hours_elapsed = elapsed.total_seconds() / 3600
            hours_remaining = max(0, expiry_hours - hours_elapsed)

            # Calculate expiry time
            expires_at = activated_at + timedelta(hours=expiry_hours)

            should_warn = hours_elapsed >= warning_hours and state.warning_sent_at is None
            should_final_warn = hours_elapsed >= final_warning_hours and state.final_warning_sent_at is None
            should_auto_restore = hours_elapsed >= expiry_hours

            return {
                "is_active": True,
                "mode": state.mode,
                "activated_at": state.activated_at,
                "activated_by": state.activated_by,
                "should_warn": should_warn,
                "should_final_warn": should_final_warn,
                "should_auto_restore": should_auto_restore,
                "hours_elapsed": round(hours_elapsed, 2),
                "hours_remaining": round(hours_remaining, 2),
                "expires_at": expires_at.isoformat(),
                "time_remaining_hours": round(hours_remaining, 2),
                "warning_hours": warning_hours,
                "final_warning_hours": final_warning_hours,
                "expiry_hours": expiry_hours,
            }

    def mark_warning_sent(self) -> None:
        """Mark that warning notification has been sent."""
        with self._lock:
            state = self._load_state()
            state.warning_sent_at = datetime.now(timezone.utc).isoformat()
            self._save_state(state)
            logger.info("governance.warning_notification_marked_sent")

    def mark_final_warning_sent(self) -> None:
        """Mark that final warning notification has been sent."""
        with self._lock:
            state = self._load_state()
            state.final_warning_sent_at = datetime.now(timezone.utc).isoformat()
            self._save_state(state)
            logger.info("governance.final_warning_notification_marked")

    def acknowledge_warning(self, acknowledged_by: str) -> dict[str, Any]:
        """
        Admin acknowledges the warning.

        Args:
            acknowledged_by: 경고를 확인한 Admin

        Returns:
            dict: 확인 결과
        """
        with self._lock:
            state = self._load_state()

            if not state.is_active:
                return {"status": "not_active", "error": "Emergency mode is not active"}

            state.acknowledged_by = acknowledged_by
            state.acknowledged_at = datetime.now(timezone.utc).isoformat()
            self._save_state(state)

            logger.info(
                "governance.emergency_warning_acknowledged",
                acknowledged_by=acknowledged_by,
            )

            return {
                "status": "acknowledged",
                "acknowledged_by": acknowledged_by,
                "acknowledged_at": state.acknowledged_at,
            }

    def auto_restore_to_normal(self) -> dict[str, Any]:
        """
        Automatically restore to normal mode (called by Celery Beat task).

        Returns:
            dict: 복원 결과
        """
        result = self.record_normal_restoration(
            restored_by="system:auto_expiry",
            reason="Emergency mode auto-expired after configured duration",
        )

        logger.warning("governance.emergency_mode_auto_expired")

        return result

    def _send_notification(
        self,
        event_type: str,
        state: GovernanceEmergencyState,
        config: dict[str, Any],
        extra: dict[str, Any] | None = None,
    ) -> None:
        """
        Send notification (to be extended with actual notification service).

        Args:
            event_type: 이벤트 유형 (emergency_activated, emergency_deactivated, ...)
            state: 현재 상태
            config: 거버넌스 설정
            extra: 추가 정보
        """
        try:
            channels = config.get("notify_channels", ["slack", "email"])
            message = self._build_notification_message(event_type, state, extra)

            logger.info(
                "governance.notification",
                event_type=event_type,
                channels=channels,
                message=message[:100],
            )

            # Call registered notification handlers
            for handler in self._notification_handlers:
                try:
                    handler(event_type, message, channels, config)
                except Exception as e:
                    logger.error(
                        "governance.notification_handler_error",
                        error=e,
                    )

        except Exception as e:
            logger.error(
                "governance.failed_send_notification",
                error=e,
            )

    def _build_notification_message(
        self,
        event_type: str,
        state: GovernanceEmergencyState,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """Build notification message."""
        if event_type == "emergency_activated":
            return (
                f"🚨 [긴급 모드 활성화]\n"
                f"모드: {state.mode}\n"
                f"전환자: {state.activated_by}\n"
                f"사유: {state.reason or 'N/A'}\n"
                f"시각: {state.activated_at}"
            )
        elif event_type == "emergency_deactivated":
            extra = extra or {}
            return (
                f"✅ [정상 모드 복구]\n"
                f"복구자: {extra.get('restored_by', 'N/A')}\n"
                f"이전 모드: {extra.get('previous_mode', 'N/A')}\n"
                f"이전 전환자: {extra.get('previous_activated_by', 'N/A')}"
            )
        elif event_type == "warning":
            return (
                f"⚠️ [긴급 모드 경고]\n"
                f"긴급 모드가 4시간 이상 유지되고 있습니다.\n"
                f"전환자: {state.activated_by}\n"
                f"시각: {state.activated_at}\n"
                f"Admin 확인이 필요합니다."
            )
        elif event_type == "final_warning":
            return (
                f"🔴 [긴급 모드 최종 경고]\n"
                f"2시간 후 자동으로 NORMAL 모드로 복귀합니다.\n"
                f"전환자: {state.activated_by}\n"
                f"시각: {state.activated_at}"
            )
        else:
            return f"[Governance] Event: {event_type}"

    def register_notification_handler(self, handler: Callable) -> None:
        """
        Register a notification handler.

        Args:
            handler: Callable(event_type, message, channels, config)
        """
        self._notification_handlers.append(handler)


# =============================================================================
# Singleton Access
# =============================================================================

_emergency_tracker: EmergencyModeTracker | None = None
_tracker_lock = threading.Lock()


def get_emergency_tracker() -> EmergencyModeTracker:
    """Get singleton EmergencyModeTracker instance."""
    global _emergency_tracker
    if _emergency_tracker is None:
        with _tracker_lock:
            if _emergency_tracker is None:
                _emergency_tracker = EmergencyModeTracker()
    return _emergency_tracker


def is_emergency_mode_active() -> bool:
    """Check if emergency mode is currently active."""
    tracker = get_emergency_tracker()
    state = tracker.get_current_state()
    return state.is_active


def get_current_operation_mode() -> str:
    """Get current operation mode (NORMAL or STRICT)."""
    tracker = get_emergency_tracker()
    state = tracker.get_current_state()
    return state.mode
