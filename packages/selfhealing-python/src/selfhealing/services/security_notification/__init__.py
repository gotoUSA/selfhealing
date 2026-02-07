"""
Security Notification Service Package.

Handles security-related notifications across multiple channels:
- Slack alerts
- Email notifications
- SMS alerts (critical only)
- PagerDuty integration (critical only)

Notifications are routed based on incident severity:
- CRITICAL: Slack + Email + SMS + PagerDuty
- HIGH: Slack + Email
- MEDIUM: Slack only

보안 사고 심각도에 따른 다중 채널 알림을 제공합니다.
"""

from __future__ import annotations

from typing import Any

# Models and data classes
from .models import (
    NotificationChannel,
    NotificationConfig,
    ChannelDeliveryResult,
    SecurityNotificationResult,
    _get_notification_limits,
)

# =============================================================================
# Deprecated Constants - Lazy import with DeprecationWarning
# =============================================================================

# Deprecated 상수는 __getattr__을 통해 접근 시 경고 발생
# 새 코드에서는 _get_notification_limits() 사용 권장
_DEPRECATED_CONSTANT_NAMES = {
    "SLACK_BLOCK_TEXT_LIMIT",
    "DESCRIPTION_MAX_LENGTH",
    "ACTION_TAKEN_MAX_LENGTH",
    "TITLE_MAX_LENGTH",
}

_init_deprecated_warned: set = set()


def __getattr__(name: str):
    """
    Deprecated 상수 접근 시 DeprecationWarning 발생.

    .. deprecated:: 2.0.0
        Use _get_notification_limits() instead.
        These constants will be removed in version 3.0.0.
    """
    import warnings

    from .models import _DEPRECATED_CONSTANTS

    if name in _DEPRECATED_CONSTANT_NAMES:
        if name not in _init_deprecated_warned:
            warnings.warn(
                f"'{name}' is deprecated. Use _get_notification_limits() instead. "
                f"This constant will be removed in v3.0.0.",
                DeprecationWarning,
                stacklevel=2,
            )
            _init_deprecated_warned.add(name)
        # models 모듈의 _DEPRECATED_CONSTANTS에서 직접 값 반환 (중복 경고 방지)
        return _DEPRECATED_CONSTANTS[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


from .email_handler import EmailHandlerMixin
from .pagerduty_handler import PagerDutyHandlerMixin

# Main service
from .service import SecurityNotificationService

# Handlers (for extension/testing)
from .slack_handler import SlackHandlerMixin
from .sms_handler import SMSHandlerMixin

# =============================================================================
# Module-level Helper Functions
# =============================================================================


_notification_service: SecurityNotificationService | None = None


def get_security_notification_service() -> SecurityNotificationService:
    """Get or create the singleton security notification service."""
    global _notification_service
    if _notification_service is None:
        _notification_service = SecurityNotificationService()
    return _notification_service


def send_alert(
    title: str,
    message: str,
    severity: str = "info",
    channels: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> SecurityNotificationResult:
    """
    Send a general-purpose alert notification.

    This is a convenience function for sending alerts that are not
    tied to a specific security incident (e.g., SLA drift, system events).

    Args:
        title: Alert title (short summary)
        message: Alert message (detailed description)
        severity: Severity level ('info', 'warning', 'critical')
        channels: List of channels to send to (default: ['slack'])
        metadata: Additional context data

    Returns:
        SecurityNotificationResult with results from all channels
    """
    service = get_security_notification_service()
    return service.send_alert(
        title=title,
        message=message,
        severity=severity,
        channels=channels,
        metadata=metadata,
    )


def notify_security_incident_by_id(
    incident_id: int,
    incident_type: str,
    severity: str,
    **kwargs: Any,
) -> SecurityNotificationResult:
    """
    Convenience function to notify about a security incident by ID.

    Args:
        incident_id: The security incident ID
        incident_type: Type of incident
        severity: Severity level
        **kwargs: Additional arguments passed to notify_security_incident_by_id

    Returns:
        SecurityNotificationResult
    """
    service = get_security_notification_service()
    return service.notify_security_incident_by_id(
        incident_id=incident_id,
        incident_type=incident_type,
        severity=severity,
        **kwargs,
    )


def notify_security_incident(incident: Any) -> SecurityNotificationResult:
    """
    Convenience function to notify about a security incident object (legacy API).

    Args:
        incident: Incident object with id, incident_type, severity, etc.

    Returns:
        SecurityNotificationResult
    """
    service = get_security_notification_service()
    return service.notify_security_incident(incident)


__all__ = [
    # Models
    "NotificationChannel",
    "NotificationConfig",
    "ChannelDeliveryResult",
    "SecurityNotificationResult",
    # Constants
    "SLACK_BLOCK_TEXT_LIMIT",
    "DESCRIPTION_MAX_LENGTH",
    "ACTION_TAKEN_MAX_LENGTH",
    "TITLE_MAX_LENGTH",
    "_get_notification_limits",
    # Handlers
    "SlackHandlerMixin",
    "EmailHandlerMixin",
    "SMSHandlerMixin",
    "PagerDutyHandlerMixin",
    # Service
    "SecurityNotificationService",
    # Convenience functions
    "get_security_notification_service",
    "send_alert",
    "notify_security_incident_by_id",
    "notify_security_incident",
]
