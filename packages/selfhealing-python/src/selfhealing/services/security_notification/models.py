"""
Security Notification Models and Data Classes.

Enums, dataclasses, and configuration for security notifications.

보안 알림 설정 및 결과 데이터 클래스.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import structlog

from selfhealing.settings import get_config

logger = structlog.get_logger()


# =============================================================================
# Constants - loaded from config
# =============================================================================


def _get_notification_limits():
    """Lazy-load notification limits from config."""
    from selfhealing.config import get_notification_limits

    return get_notification_limits()


# =============================================================================
# Deprecated 상수 - 모듈 레벨 __getattr__로 경고 발생
# =============================================================================

# 기본값 (하위 호환성용) - 새 코드는 _get_notification_limits() 사용 권장
_DEPRECATED_CONSTANTS = {
    "SLACK_BLOCK_TEXT_LIMIT": 3000,
    "DESCRIPTION_MAX_LENGTH": 500,
    "ACTION_TAKEN_MAX_LENGTH": 200,
    "TITLE_MAX_LENGTH": 150,
}

_deprecated_constant_warned: set = set()


def __getattr__(name: str):
    """
    Deprecated 상수 접근 시 DeprecationWarning 발생.

    .. deprecated:: 2.0.0
        Use _get_notification_limits() instead.
        These constants will be removed in version 3.0.0.
    """
    import warnings

    if name in _DEPRECATED_CONSTANTS:
        if name not in _deprecated_constant_warned:
            warnings.warn(
                f"'{name}' is deprecated. Use _get_notification_limits() instead. "
                f"This constant will be removed in v3.0.0.",
                DeprecationWarning,
                stacklevel=2,
            )
            _deprecated_constant_warned.add(name)
        return _DEPRECATED_CONSTANTS[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# =============================================================================
# Enums
# =============================================================================

# NotificationChannel: 단일 소스는 interfaces/notification.py (Item 3 중복 제거)
from selfhealing.interfaces.notification import NotificationChannel  # noqa: E402, F401

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
    def from_settings(cls) -> NotificationConfig:
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
class ChannelDeliveryResult:
    """Result of a notification attempt."""

    channel: str
    success: bool
    message: str = ""
    error: str | None = None


@dataclass
class SecurityNotificationResult:
    """Aggregate result of all notification attempts."""

    incident_id: int
    results: list[ChannelDeliveryResult] = field(default_factory=list)

    @property
    def all_success(self) -> bool:
        """Check if all notifications were successful."""
        return all(r.success for r in self.results)

    @property
    def any_success(self) -> bool:
        """Check if any notification was successful."""
        return any(r.success for r in self.results)

    def add_result(self, result: ChannelDeliveryResult) -> None:
        """Add a notification result."""
        self.results.append(result)
