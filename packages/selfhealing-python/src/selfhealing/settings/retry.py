"""
Retry Settings - Pydantic v2.

Single Source of Truth for retry mechanism configuration.

Replaces:
- core/config.py:RetryConfig (lines 48-56)
- core/safe_defaults.py:SAFE_DEFAULTS["retry"]
- core/safe_defaults.py:VALIDATION_RULES["retry"]

Environment Variables:
    SELFHEALING_RETRY_MAX_ATTEMPTS=3
    SELFHEALING_RETRY_BACKOFF_STRATEGY=exponential
    SELFHEALING_RETRY_BASE_DELAY=1.0
    ... etc

Reference:
- docs/self_healing/middleware_system/40_PYDANTIC_CONFIG_MIGRATION.md
"""

import logging
from typing import Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Valid backoff strategies (from core/safe_defaults.py)
VALID_BACKOFF_STRATEGIES = {"exponential", "linear", "constant", "decorrelated_jitter"}


class RetrySettings(BaseSettings):
    """
    Retry mechanism configuration with validation.

    All defaults match core/config.py:RetryConfig
    All validation rules match core/safe_defaults.py:VALIDATION_RULES["retry"]
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_RETRY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Core Settings (from core/config.py lines 50-56)
    # Validation rules from core/safe_defaults.py lines 246-252
    # ==========================================================================
    max_attempts: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Maximum number of retry attempts",
    )
    backoff_strategy: str = Field(
        default="exponential",
        description="Backoff strategy: exponential, linear, constant, decorrelated_jitter",
    )
    backoff_base: int = Field(
        default=4,
        ge=1,
        le=10,
        description="Base for exponential backoff (4^n seconds)",
    )
    base_delay: float = Field(
        default=1.0,
        ge=0.1,
        le=60.0,
        description="Base delay in seconds",
    )
    max_delay: float = Field(
        default=300.0,
        ge=1.0,
        le=3600.0,
        description="Maximum delay cap in seconds",
    )
    min_delay: int = Field(
        default=1,
        ge=1,
        le=60,
        description="Minimum delay in seconds",
    )
    jitter: bool = Field(
        default=True,
        description="Enable random jitter to prevent thundering herd",
    )
    jitter_percent: int = Field(
        default=25,
        ge=0,
        le=100,
        description="Jitter percentage (±%)",
    )

    @field_validator("backoff_strategy")
    @classmethod
    def validate_backoff_strategy(cls, v: str) -> str:
        """Validate backoff strategy is one of the allowed values."""
        if v not in VALID_BACKOFF_STRATEGIES:
            raise ValueError(
                f"backoff_strategy must be one of {VALID_BACKOFF_STRATEGIES}, got '{v}'"
            )
        return v

    @field_validator("max_delay")
    @classmethod
    def validate_max_delay(cls, v: float) -> float:
        """Warn if max_delay is very high."""
        if v > 600:
            logger.warning(
                f"[SafeDefault] High max_delay={v}s, "
                "consider using <= 600s for responsiveness"
            )
        return v


# =============================================================================
# Singleton Pattern (cached settings)
# =============================================================================

_settings: Optional[RetrySettings] = None


def get_retry_settings() -> RetrySettings:
    """
    Get cached RetrySettings instance.

    Returns:
        RetrySettings: Singleton instance
    """
    global _settings
    if _settings is None:
        _settings = RetrySettings()
    return _settings


def reset_retry_settings() -> None:
    """
    Reset cached settings (for testing).

    Call this after modifying environment variables to reload settings.
    """
    global _settings
    _settings = None
