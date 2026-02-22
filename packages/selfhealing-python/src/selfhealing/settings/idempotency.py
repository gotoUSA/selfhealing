"""
Idempotency Settings - Pydantic v2.

Single Source of Truth for idempotency service configuration.

Replaces:
- core/config.py:IdempotencyConfig (lines 166-171)
- core/safe_defaults.py:SAFE_DEFAULTS["idempotency"]
- core/safe_defaults.py:VALIDATION_RULES["idempotency"]

Environment Variables:
    SELFHEALING_IDEMPOTENCY_DEFAULT_CACHE_TTL=60
    SELFHEALING_IDEMPOTENCY_EXTENDED_CACHE_TTL=300

Reference:
- docs/self_healing/middleware_system/40_PYDANTIC_CONFIG_MIGRATION.md
"""

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class IdempotencySettings(BaseSettings):
    """
    Idempotency service configuration with validation.

    All defaults match core/config.py:IdempotencyConfig
    All validation rules match core/safe_defaults.py:VALIDATION_RULES["idempotency"]
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_IDEMPOTENCY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Cache TTL Settings (from core/config.py lines 168-171)
    # Validation rules from core/safe_defaults.py lines 296-300
    # ==========================================================================
    default_cache_ttl: int = Field(
        default=60,
        ge=1,
        le=3600,
        description="Default cache TTL in seconds",
    )
    extended_cache_ttl: int = Field(
        default=300,
        ge=1,
        le=86400,
        description="Extended cache TTL for operations requiring longer TTL",
    )
    short_cache_ttl: int = Field(
        default=60,
        ge=1,
        le=300,
        description="Short cache TTL for short-lived operations",
    )
    clock_skew_tolerance_seconds: float = Field(
        default=5.0,
        ge=0.0,
        le=60.0,
        description="Clock skew tolerance for idempotency checks",
    )


# Singleton instance (cached)
_settings: IdempotencySettings | None = None


def get_idempotency_settings() -> IdempotencySettings:
    """Get cached IdempotencySettings instance."""
    global _settings
    if _settings is None:
        _settings = IdempotencySettings()
    return _settings


def reset_idempotency_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
