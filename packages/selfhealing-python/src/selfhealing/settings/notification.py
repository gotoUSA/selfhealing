"""
Notification Settings - Pydantic v2.

Single Source of Truth for notification configuration.

Replaces:
- core/config.py:NotificationConfig (lines 253-275)
- core/safe_defaults.py:SAFE_DEFAULTS["notification"]
- core/safe_defaults.py:VALIDATION_RULES["notification"]

Environment Variables:
    SELFHEALING_NOTIFICATION_ENABLED=true
    SELFHEALING_NOTIFICATION_CRITICAL_THRESHOLD=10

Reference:
- docs/self_healing/middleware_system/40_PYDANTIC_CONFIG_MIGRATION.md
"""

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class NotificationSettings(BaseSettings):
    """
    Notification and alerts configuration with validation.

    All defaults match core/config.py:NotificationConfig
    All validation rules match core/safe_defaults.py:VALIDATION_RULES["notification"]
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_NOTIFICATION_",
        env_file=None,
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Core Settings (from core/config.py lines 255-259)
    # Validation rules from core/safe_defaults.py lines 274-280
    # ==========================================================================
    enabled: bool = Field(
        default=True,
        description="Enable notification system",
    )
    channels: list[str] = Field(
        default_factory=lambda: ["email"],
        description="Notification channels (email, slack, etc.)",
    )
    critical_threshold: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Threshold for critical notifications",
    )
    warning_threshold: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Threshold for warning notifications",
    )

    # ==========================================================================
    # Message Limits (from core/config.py lines 261-266)
    # ==========================================================================
    slack_block_text_limit: int = Field(
        default=3000,
        ge=100,
        le=10000,
        description="Slack block text limit",
    )
    description_max_length: int = Field(
        default=500,
        ge=50,
        le=5000,
        description="Maximum description length",
    )
    action_taken_max_length: int = Field(
        default=200,
        ge=50,
        le=1000,
        description="Maximum action taken text length",
    )
    title_max_length: int = Field(
        default=150,
        ge=20,
        le=500,
        description="Maximum title length",
    )
    notification_timeout_seconds: int = Field(
        default=10,
        ge=1,
        le=60,
        description="Notification timeout in seconds",
    )

    # ==========================================================================
    # Slack Channels (from core/config.py lines 268-270)
    # ==========================================================================
    critical_channel: str = Field(
        default="#critical-alerts",
        description="Slack channel for critical alerts",
    )
    high_channel: str = Field(
        default="#ops-alerts",
        description="Slack channel for high priority alerts",
    )
    medium_channel: str = Field(
        default="#dev-alerts",
        description="Slack channel for medium priority alerts",
    )


# Singleton instance (cached)
_settings: NotificationSettings | None = None


def get_notification_settings() -> NotificationSettings:
    """Get cached NotificationSettings instance."""
    global _settings
    if _settings is None:
        _settings = NotificationSettings()
    return _settings


def reset_notification_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
