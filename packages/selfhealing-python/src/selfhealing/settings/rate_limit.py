"""
Rate Limit Settings - Pydantic v2.

Single Source of Truth for rate limit coordination configuration.

Replaces:
- core/config.py:RateLimitConfig (lines 137-163)
- core/safe_defaults.py:SAFE_DEFAULTS["rate_limit"]
- core/safe_defaults.py:VALIDATION_RULES["rate_limit"]

Environment Variables:
    SELFHEALING_RATE_LIMIT_BASE_DELAY=1.0
    SELFHEALING_RATE_LIMIT_MAX_DELAY=60.0
    SELFHEALING_RATE_LIMIT_CONTROL_API_RATE_LIMIT=100
    ... etc

Reference:
- docs/self_healing/middleware_system/40_PYDANTIC_CONFIG_MIGRATION.md
"""

import structlog
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class RateLimitSettings(BaseSettings):
    """
    Rate Limit coordination configuration with validation.

    Includes both retry backoff settings and Control API rate limiting.

    All defaults match core/config.py:RateLimitConfig
    All validation rules match core/safe_defaults.py:VALIDATION_RULES["rate_limit"]
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_RATE_LIMIT_",
        env_file=None,
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Retry Backoff Settings (for 429 handling)
    # From core/config.py lines 144-149
    # Validation rules from core/safe_defaults.py lines 254-261
    # ==========================================================================
    base_delay: float = Field(
        default=1.0,
        ge=0.1,
        le=60.0,
        description="Base delay in seconds",
    )
    max_delay: float = Field(
        default=60.0,
        ge=1.0,
        le=300.0,
        description="Maximum delay cap in seconds",
    )
    jitter_percent: float = Field(
        default=30.0,
        ge=0.0,
        le=100.0,
        description="±% random jitter",
    )
    default_retry_after: float = Field(
        default=5.0,
        ge=0.1,
        le=60.0,
        description="Default delay if no Retry-After header",
    )
    backoff_multiplier: float = Field(
        default=2.0,
        ge=1.0,
        le=10.0,
        description="Cooldown multiplier for consecutive 429s",
    )

    # ==========================================================================
    # Control API Rate Limiting (HybridRateLimitMiddleware)
    # From core/config.py lines 152-156
    # Validation rules from core/safe_defaults.py lines 258-260
    # ==========================================================================
    control_api_rate_limit: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="Requests/minute in normal mode (Redis)",
    )
    control_api_window_seconds: int = Field(
        default=60,
        ge=1,
        le=3600,
        description="Window size for rate limiting",
    )
    emergency_rate_limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Requests/minute when Redis fails",
    )
    emergency_window_seconds: int = Field(
        default=60,
        ge=1,
        le=3600,
        description="Emergency window size",
    )

    # ==========================================================================
    # Redis Storage TTL - from adapters/rate_limit/redis_adapter.py
    # ==========================================================================
    redis_ttl: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="Redis에 저장되는 Rate Limit 상태의 TTL (초). 기본 1시간.",
    )

    @field_validator("emergency_rate_limit")
    @classmethod
    def validate_emergency_rate_limit(cls, v: int) -> int:
        """Emergency rate limit should be conservative."""
        if v > 50:
            logger.warning(
                "safe_default.high_consider_using_safety",
                setting_value=v,
            )
        return v


# =============================================================================
# Singleton Pattern (cached settings)
# =============================================================================

_settings: RateLimitSettings | None = None


def get_rate_limit_settings() -> RateLimitSettings:
    """
    Get cached RateLimitSettings instance.

    Returns:
        RateLimitSettings: Singleton instance
    """
    global _settings
    if _settings is None:
        _settings = RateLimitSettings()
    return _settings


def reset_rate_limit_settings() -> None:
    """
    Reset cached settings (for testing).

    Call this after modifying environment variables to reload settings.
    """
    global _settings
    _settings = None
