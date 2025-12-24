"""
Governance Celery Tasks - Emergency Mode Auto-Recovery.

긴급 모드 자동 복귀 및 알림 발송을 위한 Celery 태스크입니다.

Celery Beat 스케줄:
    - check_emergency_mode_expiry: 15분 주기

Usage:
    # settings.py 또는 celery.py에 추가
    CELERY_BEAT_SCHEDULE = {
        "check-emergency-mode-expiry": {
            "task": "selfhealing.tasks.governance.check_emergency_mode_expiry",
            "schedule": 900.0,  # 15분
        },
    }

Reference:
- docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_ROADMAP.md
"""

from __future__ import annotations

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


def check_emergency_mode_expiry() -> Dict[str, Any]:
    """
    Check emergency mode expiry and perform auto-recovery if needed.

    This task should be scheduled via Celery Beat (every 15 minutes).

    Actions:
    1. Check if emergency mode is active
    2. If 4 hours elapsed: Send warning to Admin
    3. If 6 hours elapsed: Send final warning ("2 hours until auto-restore")
    4. If 8 hours elapsed: Auto-restore to NORMAL mode

    Returns:
        dict: 실행 결과
    """
    from selfhealing.services.governance import get_emergency_tracker

    tracker = get_emergency_tracker()
    status = tracker.check_expiry_status()

    result = {
        "is_active": status["is_active"],
        "actions_taken": [],
    }

    if not status["is_active"]:
        logger.debug("[Governance] Emergency mode is not active, skipping expiry check")
        return result

    logger.info(
        f"[Governance] Emergency mode expiry check: "
        f"hours_elapsed={status['hours_elapsed']}, "
        f"hours_remaining={status['hours_remaining']}"
    )

    # Action 1: Auto-restore (highest priority)
    if status["should_auto_restore"]:
        logger.warning(
            "[Governance] Emergency mode expired, performing auto-restore"
        )
        restore_result = tracker.auto_restore_to_normal()
        result["actions_taken"].append({
            "action": "auto_restore",
            "result": restore_result,
        })
        _send_auto_restore_notification(status)
        return result

    # Action 2: Final warning (6 hours)
    if status["should_final_warn"]:
        logger.warning(
            "[Governance] Emergency mode final warning: "
            f"2 hours until auto-restore"
        )
        tracker.mark_final_warning_sent()
        result["actions_taken"].append({
            "action": "final_warning_sent",
            "hours_remaining": status["hours_remaining"],
        })
        _send_final_warning_notification(status)

    # Action 3: Warning (4 hours)
    if status["should_warn"]:
        logger.warning(
            "[Governance] Emergency mode warning: "
            f"4+ hours in emergency mode"
        )
        tracker.mark_warning_sent()
        result["actions_taken"].append({
            "action": "warning_sent",
            "hours_elapsed": status["hours_elapsed"],
        })
        _send_warning_notification(status)

    return result


def _send_warning_notification(status: Dict[str, Any]) -> None:
    """Send 4-hour warning notification."""
    _send_notification(
        event_type="warning",
        title="⚠️ 긴급 모드 경고",
        message=(
            f"긴급 모드가 {status['hours_elapsed']:.1f}시간 동안 유지되고 있습니다.\n"
            f"전환자: {status.get('activated_by', 'N/A')}\n"
            f"자동 복귀까지: {status['hours_remaining']:.1f}시간\n"
            f"Admin 확인이 필요합니다."
        ),
        status=status,
    )


def _send_final_warning_notification(status: Dict[str, Any]) -> None:
    """Send 6-hour final warning notification."""
    _send_notification(
        event_type="final_warning",
        title="🔴 긴급 모드 최종 경고",
        message=(
            f"2시간 후 자동으로 NORMAL 모드로 복귀합니다.\n"
            f"전환자: {status.get('activated_by', 'N/A')}\n"
            f"경과 시간: {status['hours_elapsed']:.1f}시간\n"
            f"즉시 조치가 필요합니다."
        ),
        status=status,
    )


def _send_auto_restore_notification(status: Dict[str, Any]) -> None:
    """Send auto-restore notification."""
    _send_notification(
        event_type="auto_restore",
        title="🔄 긴급 모드 자동 복구",
        message=(
            f"긴급 모드가 만료되어 자동으로 NORMAL 모드로 복귀했습니다.\n"
            f"이전 전환자: {status.get('activated_by', 'N/A')}\n"
            f"경과 시간: {status['hours_elapsed']:.1f}시간\n"
            f"Safe Default가 적용되었습니다."
        ),
        status=status,
    )


def _send_notification(
    event_type: str,
    title: str,
    message: str,
    status: Dict[str, Any],
) -> None:
    """
    Send notification via configured channels.

    Integration with actual notification service (Slack, Email, etc.)
    should be added here.
    """
    try:
        from selfhealing.services.governance import get_emergency_tracker

        tracker = get_emergency_tracker()
        config = tracker._get_governance_config()

        channels = config.get("notify_channels", ["slack", "email"])

        logger.info(
            f"[Governance] Sending notification: "
            f"event={event_type}, title={title}, channels={channels}"
        )

        # TODO: Integrate with actual notification service
        # from selfhealing.services.notification import send_notification
        # send_notification(
        #     title=title,
        #     message=message,
        #     channels=channels,
        #     severity="critical" if event_type == "auto_restore" else "warning",
        # )

    except Exception as e:
        logger.error(f"[Governance] Failed to send notification: {e}")


# =============================================================================
# Celery Beat Schedule Configuration
# =============================================================================


def get_governance_beat_schedule() -> Dict[str, Dict[str, Any]]:
    """
    Get Celery Beat schedule for governance tasks.

    Returns:
        dict: Celery Beat schedule configuration

    Usage:
        # In your celery.py or settings.py:
        from selfhealing.tasks.governance import get_governance_beat_schedule

        CELERY_BEAT_SCHEDULE.update(get_governance_beat_schedule())
    """
    return {
        "check-emergency-mode-expiry": {
            "task": "selfhealing.tasks.governance.check_emergency_mode_expiry",
            "schedule": 900.0,  # 15분 (900초)
            "options": {
                "queue": "governance",
                "priority": 3,  # High priority
            },
        },
    }


# =============================================================================
# Celery Task Registration (if using Celery)
# =============================================================================

try:
    from celery import shared_task

    @shared_task(
        name="selfhealing.tasks.governance.check_emergency_mode_expiry",
        bind=True,
        max_retries=3,
        default_retry_delay=60,
        autoretry_for=(Exception,),
        retry_backoff=True,
    )
    def check_emergency_mode_expiry_task(self) -> Dict[str, Any]:
        """
        Celery task wrapper for check_emergency_mode_expiry.

        This is the actual Celery task that should be scheduled.
        """
        return check_emergency_mode_expiry()

except ImportError:
    # Celery not installed, skip task registration
    logger.debug("[Governance] Celery not installed, skipping task registration")
    pass
