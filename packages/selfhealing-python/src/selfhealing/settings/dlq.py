"""
DLQ (Dead Letter Queue) Settings - Pydantic v2.

Single Source of Truth for DLQ configuration.

Replaces:
- core/config.py:DLQConfig (lines 36-45)
- core/safe_defaults.py:SAFE_DEFAULTS["dlq"]
- core/safe_defaults.py:VALIDATION_RULES["dlq"]

Environment Variables:
    SELFHEALING_DLQ_ENABLED=true
    SELFHEALING_DLQ_MAX_RETRIES=3
    SELFHEALING_DLQ_RETRY_DELAY=60
    ... etc

Reference:
- docs/self_healing/middleware_system/40_PYDANTIC_CONFIG_MIGRATION.md
"""

import logging
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class DLQSettings(BaseSettings):
    """
    Dead Letter Queue configuration with validation.

    All defaults match core/config.py:DLQConfig
    All validation rules match core/safe_defaults.py:VALIDATION_RULES["dlq"]
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_DLQ_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Core Settings (from core/config.py lines 38-45)
    # Validation rules from core/safe_defaults.py lines 238-244
    # ==========================================================================
    enabled: bool = Field(
        default=True,
        description="Enable Dead Letter Queue",
    )
    max_retries: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Maximum retry attempts before giving up",
    )
    retry_delay: int = Field(
        default=60,
        ge=1,
        le=3600,
        description="Delay between retries in seconds",
    )
    expiry_hours: int = Field(
        default=72,
        ge=1,
        le=720,
        description="Hours until DLQ entry expires",
    )
    retention_days: int = Field(
        default=30,
        ge=1,
        le=365,
        description="Days to retain DLQ entries",
    )
    batch_size: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="Number of entries to process in a batch",
    )
    max_replay_attempts: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Maximum replay attempts for DLQ entries",
    )

    @field_validator("retention_days")
    @classmethod
    def validate_retention_days(cls, v: int) -> int:
        """Warn if retention is very short (< 7 days)."""
        if v < 7:
            logger.warning(
                f"[SafeDefault] Short retention_days={v}, "
                "consider using >= 7 for data safety"
            )
        return v


# =============================================================================
# Singleton Pattern (cached settings)
# =============================================================================

_settings: Optional[DLQSettings] = None


def get_dlq_settings() -> DLQSettings:
    """
    Get cached DLQSettings instance.

    Returns:
        DLQSettings: Singleton instance
    """
    global _settings
    if _settings is None:
        _settings = DLQSettings()
    return _settings


def reset_dlq_settings() -> None:
    """
    Reset cached settings (for testing).

    Call this after modifying environment variables to reload settings.
    """
    global _settings
    _settings = None
