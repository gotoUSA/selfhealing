"""
Security Notification Models and Data Classes.

Enums, dataclasses, and configuration for security notifications.

Reference: docs/L3_SELF_HEALING_OPERATIONS.md §4 (Escalation & Notifications)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

from selfhealing.core.config import get_config

logger = logging.getLogger(__name__)


# =============================================================================
# Constants - loaded from config
# =============================================================================


def _get_notification_limits():
    """Lazy-load notification limits from config."""
    from selfhealing.config import get_notification_limits

    return get_notification_limits()


# For backward compatibility, expose as module-level but load lazily
# Use _get_notification_limits() for actual values
SLACK_BLOCK_TEXT_LIMIT = 3000  # Deprecated: use _get_notification_limits()
DESCRIPTION_MAX_LENGTH = 500  # Deprecated: use _get_notification_limits()
ACTION_TAKEN_MAX_LENGTH = 200  # Deprecated: use _get_notification_limits()
TITLE_MAX_LENGTH = 150  # Deprecated: use _get_notification_limits()


# =============================================================================
# Enums
# =============================================================================


class NotificationChannel(str, Enum):
    """Available notification channels."""

    SLACK = "slack"
    EMAIL = "email"
    SMS = "sms"
    PAGERDUTY = "pagerduty"


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class NotificationConfig:
    """Configuration for security notifications."""

    # Slack configuration
    slack_webhook_url: str = ""
    slack_critical_channel: str = "#critical-alerts"
    slack_high_channel: str = "#ops-alerts"
    slack_medium_channel: str = "#dev-alerts"

    # Email configuration
    email_critical_recipients: list[str] = field(default_factory=list)
    email_high_recipients: list[str] = field(default_factory=list)

    # SMS configuration
    sms_critical_recipients: list[str] = field(default_factory=list)

    # PagerDuty configuration
    pagerduty_service_key: str = ""
    pagerduty_enabled: bool = False

    # General settings
    enabled: bool = True
    dry_run: bool = False  # For testing - log instead of send

    @classmethod
    def from_settings(cls) -> "NotificationConfig":
        """Load configuration from settings."""
        config = get_config()
        notification = config.notification  # Use singular form

        return cls(
            slack_webhook_url=getattr(notification, "slack_webhook_url", ""),
            slack_critical_channel=getattr(notification, "critical_channel", "#critical-alerts"),
            slack_high_channel=getattr(notification, "high_channel", "#ops-alerts"),
            slack_medium_channel=getattr(notification, "medium_channel", "#dev-alerts"),
            email_critical_recipients=getattr(notification, "email_critical_recipients", []),
            email_high_recipients=getattr(notification, "email_high_recipients", []),
            sms_critical_recipients=getattr(notification, "sms_critical_recipients", []),
            pagerduty_service_key=getattr(notification, "pagerduty_service_key", ""),
            pagerduty_enabled=getattr(notification, "pagerduty_enabled", False),
            enabled=notification.enabled,
            dry_run=getattr(notification, "dry_run", False),
        )


# =============================================================================
# Result Data Classes
# =============================================================================


@dataclass
class NotificationResult:
    """Result of a notification attempt."""

    channel: str
    success: bool
    message: str = ""
    error: str | None = None


@dataclass
class SecurityNotificationResult:
    """Aggregate result of all notification attempts."""

    incident_id: int
    results: list[NotificationResult] = field(default_factory=list)

    @property
    def all_success(self) -> bool:
        """Check if all notifications were successful."""
        return all(r.success for r in self.results)

    @property
    def any_success(self) -> bool:
        """Check if any notification was successful."""
        return any(r.success for r in self.results)

    def add_result(self, result: NotificationResult) -> None:
        """Add a notification result."""
        self.results.append(result)
