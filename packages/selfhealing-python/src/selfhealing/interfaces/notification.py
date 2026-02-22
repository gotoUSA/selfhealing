"""
Notification Service Interface

Provides an abstraction for sending notifications to external channels
(Slack, Teams, PagerDuty, Email, Webhook, etc.) without coupling to
any specific implementation.

Design Philosophy:
- Protocol-based interface for maximum flexibility
- Default implementations: stdout, file logging
- User provides their own adapter for production

Usage:
    # Register your notification adapter
    from selfhealing.services.notification import register_notification_adapter

    class SlackNotificationAdapter(NotificationAdapter):
        def send(self, notification: Notification) -> bool:
            # Your Slack webhook implementation
            return True

    register_notification_adapter(SlackNotificationAdapter())

Environment Variables:
    SELFHEALING_NOTIFICATION_WEBHOOK: Webhook URL for HTTP-based notifications
    SELFHEALING_NOTIFICATION_CHANNEL: Default channel/room name
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol, runtime_checkable

import structlog

logger = structlog.get_logger()


# =============================================================================
# Notification Types
# =============================================================================


class NotificationSeverity(str, Enum):
    """Notification urgency levels."""

    CRITICAL = "critical"  # Immediate action required (pages on-call)
    HIGH = "high"  # Urgent but not page-worthy
    MEDIUM = "medium"  # Should be addressed soon
    LOW = "low"  # Informational
    INFO = "info"  # FYI only


class NotificationChannel(str, Enum):
    """Notification delivery channels."""

    SLACK = "slack"
    TEAMS = "teams"
    PAGERDUTY = "pagerduty"
    EMAIL = "email"
    WEBHOOK = "webhook"
    SMS = "sms"
    STDOUT = "stdout"  # Default: print to console
    FILE = "file"  # Log to file


@dataclass
class Notification:
    """
    Notification payload.

    Attributes:
        title: Short summary (for subject/title)
        message: Full message body
        severity: Urgency level
        channel: Delivery channel (optional, uses default if not set)
        source: Component that generated the notification
        metadata: Additional context (e.g., service_name, incident_id)
    """

    title: str
    message: str
    severity: NotificationSeverity = NotificationSeverity.MEDIUM
    channel: NotificationChannel | None = None
    source: str = "selfhealing"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "title": self.title,
            "message": self.message,
            "severity": self.severity.value,
            "channel": self.channel.value if self.channel else None,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }


# =============================================================================
# Notification Adapter Interface
# =============================================================================


@runtime_checkable
class NotificationAdapter(Protocol):
    """
    Protocol for notification adapters.

    Implement this protocol to send notifications to your preferred channel.

    Example:
        class SlackNotificationAdapter:
            def send(self, notification: Notification) -> bool:
                response = requests.post(
                    SLACK_WEBHOOK_URL,
                    json={"text": f"*{notification.title}*\n{notification.message}"}
                )
                return response.ok

            def send_batch(self, notifications: List[Notification]) -> int:
                return sum(1 for n in notifications if self.send(n))

            @property
            def channel(self) -> NotificationChannel:
                return NotificationChannel.SLACK
    """

    def send(self, notification: Notification) -> bool:
        """
        Send a single notification.

        Returns:
            True if sent successfully, False otherwise
        """
        ...

    def send_batch(self, notifications: list[Notification]) -> int:
        """
        Send multiple notifications.

        Returns:
            Number of successfully sent notifications
        """
        ...

    @property
    def channel(self) -> NotificationChannel:
        """Return the channel this adapter handles."""
        ...


# =============================================================================
# Default Implementations
# =============================================================================


class StdoutNotificationAdapter:
    """Default adapter that prints to stdout."""

    def send(self, notification: Notification) -> bool:
        print(f"[{notification.severity.value.upper()}] " f"{notification.title}: {notification.message}")
        return True

    def send_batch(self, notifications: list[Notification]) -> int:
        return sum(1 for n in notifications if self.send(n))

    @property
    def channel(self) -> NotificationChannel:
        return NotificationChannel.STDOUT


class LoggingNotificationAdapter:
    """Adapter that logs notifications."""

    # severity → structlog 메서드 매핑
    _SEVERITY_TO_LOG_METHOD = {
        "CRITICAL": "critical",
        "HIGH": "error",
        "MEDIUM": "warning",
        "LOW": "info",
        "INFO": "debug",
    }

    def __init__(self, logger_name: str = "selfhealing.notifications"):
        self._logger = structlog.get_logger().bind(logger_name=logger_name)

    def send(self, notification: Notification) -> bool:
        method_name = self._SEVERITY_TO_LOG_METHOD.get(notification.severity.value.upper(), "info")
        log_method = getattr(self._logger, method_name)
        log_method(
            "notification.sent",
            source=notification.source,
            title=notification.title,
            message=notification.message,
            notification=notification.to_dict(),
        )
        return True

    def send_batch(self, notifications: list[Notification]) -> int:
        return sum(1 for n in notifications if self.send(n))

    @property
    def channel(self) -> NotificationChannel:
        return NotificationChannel.FILE


# =============================================================================
# Notification Service Registry
# =============================================================================


_notification_adapters: dict[NotificationChannel, NotificationAdapter] = {}
_default_adapter: NotificationAdapter = LoggingNotificationAdapter()


def register_notification_adapter(adapter: NotificationAdapter) -> None:
    """Register a notification adapter for its channel."""
    _notification_adapters[adapter.channel] = adapter
    logger.info("notification.adapter_registered", channel=adapter.channel.value)


def get_notification_adapter(
    channel: NotificationChannel | None = None,
) -> NotificationAdapter:
    """
    Get the notification adapter for a channel.

    Args:
        channel: Target channel, or None for default

    Returns:
        NotificationAdapter instance
    """
    if channel and channel in _notification_adapters:
        return _notification_adapters[channel]
    return _default_adapter


def send_notification(
    title: str,
    message: str,
    severity: NotificationSeverity = NotificationSeverity.MEDIUM,
    channel: NotificationChannel | None = None,
    **metadata,
) -> bool:
    """
    Convenience function to send a notification.

    Args:
        title: Notification title
        message: Notification body
        severity: Urgency level
        channel: Target channel (uses default if None)
        **metadata: Additional context

    Returns:
        True if sent successfully
    """
    notification = Notification(
        title=title,
        message=message,
        severity=severity,
        channel=channel,
        metadata=metadata,
    )
    adapter = get_notification_adapter(channel)
    return adapter.send(notification)


# =============================================================================
# Convenience Functions for Chaos Scheduler Integration
# =============================================================================


def send_pending_approval_alert(
    pending_count: int,
    schedules: list[Any] | None = None,
    blast_radius: list[Any] | None = None,
) -> bool:
    """
    Send alert for pending chaos experiment approvals.

    Called by chaos_scheduler.check_and_alert_pending_approvals().
    """
    message = f"{pending_count} chaos experiments are pending approval."
    if schedules:
        message += f"\n- Scheduled: {len(schedules)}"
    if blast_radius:
        message += f"\n- Blast radius: {len(blast_radius)}"

    return send_notification(
        title="Chaos Experiments Pending Approval",
        message=message,
        severity=NotificationSeverity.MEDIUM,
        pending_count=pending_count,
    )


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Types
    "Notification",
    "NotificationSeverity",
    "NotificationChannel",
    # Protocol
    "NotificationAdapter",
    # Default adapters
    "StdoutNotificationAdapter",
    "LoggingNotificationAdapter",
    # Registry
    "register_notification_adapter",
    "get_notification_adapter",
    "send_notification",
    # Convenience
    "send_pending_approval_alert",
]
