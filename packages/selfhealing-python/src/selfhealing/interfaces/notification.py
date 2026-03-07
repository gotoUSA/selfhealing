"""
Notification Service Interface

Provides an abstraction for sending notifications to external channels
(Slack, Teams, PagerDuty, Email, Webhook, etc.) without coupling to
any specific implementation.

Design Philosophy:
- ABC-based interface with duck-typed adapter compatibility via ABC.register()
- Default implementations: stdout, file logging
- User provides their own adapter for production
- All adapters are managed through ProviderRegistry

Usage:
    # Register your notification adapter
    from selfhealing.interfaces.notification import register_notification_adapter

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

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger()


# =============================================================================
# Notification Types
# =============================================================================


from selfhealing.interfaces.messaging_common import MessageChannel, MessageSeverity

# Backward-compatible aliases
NotificationSeverity = MessageSeverity
NotificationChannel = MessageChannel


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
# Notification Adapter Interface (ABC)
# =============================================================================


class NotificationAdapter(ABC):
    """
    ABC for notification adapters.

    Implement this class to send notifications to your preferred channel.
    Duck-typed adapters are also accepted via register_notification_adapter(),
    which auto-registers them as virtual subclasses.

    Example:
        class SlackNotificationAdapter(NotificationAdapter):
            def send(self, notification: Notification) -> bool:
                response = requests.post(
                    SLACK_WEBHOOK_URL,
                    json={"text": f"*{notification.title}*\\n{notification.message}"}
                )
                return response.ok

            def send_batch(self, notifications: list[Notification]) -> int:
                return sum(1 for n in notifications if self.send(n))

            @property
            def channel(self) -> NotificationChannel:
                return NotificationChannel.SLACK
    """

    @abstractmethod
    def send(self, notification: Notification) -> bool:
        """
        Send a single notification.

        Returns:
            True if sent successfully, False otherwise
        """
        ...

    @abstractmethod
    def send_batch(self, notifications: list[Notification]) -> int:
        """
        Send multiple notifications.

        Returns:
            Number of successfully sent notifications
        """
        ...

    @property
    @abstractmethod
    def channel(self) -> NotificationChannel:
        """Return the channel this adapter handles."""
        ...


# =============================================================================
# Default Implementations
# =============================================================================


class StdoutNotificationAdapter(NotificationAdapter):
    """Default adapter that prints to stdout."""

    def send(self, notification: Notification) -> bool:
        print(
            f"[{notification.severity.value.upper()}] "
            f"{notification.title}: {notification.message}"
        )
        return True

    def send_batch(self, notifications: list[Notification]) -> int:
        return sum(1 for n in notifications if self.send(n))

    @property
    def channel(self) -> NotificationChannel:
        return NotificationChannel.STDOUT


class LoggingNotificationAdapter(NotificationAdapter):
    """Adapter that logs notifications."""

    # severity -> structlog method mapping
    _SEVERITY_TO_LOG_METHOD = {
        "CRITICAL": "critical",
        "HIGH": "error",
        "WARNING": "warning",
        "MEDIUM": "warning",
        "LOW": "info",
        "INFO": "debug",
    }

    def __init__(self, logger_name: str = "selfhealing.notifications"):
        self._logger = structlog.get_logger().bind(logger_name=logger_name)

    def send(self, notification: Notification) -> bool:
        method_name = self._SEVERITY_TO_LOG_METHOD.get(
            notification.severity.value.upper(), "info"
        )
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
# Notification Service Registry (delegates to ProviderRegistry)
# =============================================================================


_default_adapter: NotificationAdapter = LoggingNotificationAdapter()


def register_notification_adapter(adapter: object) -> None:
    """Register a notification adapter for its channel.

    Delegates to ProviderRegistry. Auto-registers duck-typed adapters
    as virtual subclasses of NotificationAdapter.
    """
    if not isinstance(adapter, NotificationAdapter):
        required = ("send", "send_batch", "channel")
        missing = [m for m in required if not hasattr(adapter, m)]
        if missing:
            raise TypeError(
                f"Adapter {type(adapter).__name__} missing: {missing}. "
                f"Inherit from NotificationAdapter."
            )
        NotificationAdapter.register(type(adapter))
        logger.warning(
            "notification.duck_typed_adapter_registered",
            adapter_type=type(adapter).__name__,
        )

    from selfhealing.factory import ProviderRegistry

    channel_name = adapter.channel.value if hasattr(adapter, "channel") else "default"
    ProviderRegistry.register_notification(channel_name, lambda: adapter)
    logger.info("notification.adapter_registered", channel=channel_name)


def get_notification_adapter(
    channel: NotificationChannel | None = None,
) -> NotificationAdapter:
    """
    Get the notification adapter for a channel.

    Delegates to ProviderRegistry.

    Args:
        channel: Target channel, or None for default

    Returns:
        NotificationAdapter instance
    """
    from selfhealing.factory import ProviderRegistry

    name = channel.value if channel else None
    try:
        return ProviderRegistry.get_notification(name)
    except (ValueError, Exception):
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
    # ABC
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
