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
    NotificationResult,
    SecurityNotificationResult,
    SLACK_BLOCK_TEXT_LIMIT,
    DESCRIPTION_MAX_LENGTH,
    ACTION_TAKEN_MAX_LENGTH,
    TITLE_MAX_LENGTH,
    _get_notification_limits,
)

# Handlers (for extension/testing)
from .slack_handler import SlackHandlerMixin
from .email_handler import EmailHandlerMixin
from .sms_handler import SMSHandlerMixin
from .pagerduty_handler import PagerDutyHandlerMixin

# Main service
from .service import SecurityNotificationService


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
    "NotificationResult",
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
