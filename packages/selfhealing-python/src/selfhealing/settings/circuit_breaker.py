"""
Circuit Breaker Settings - Pydantic v2.

Single Source of Truth for circuit breaker configuration.

Replaces:
- core/config.py:CircuitBreakerConfig (lines 13-33)
- core/safe_defaults.py:SAFE_DEFAULTS["circuit_breaker"]
- core/safe_defaults.py:VALIDATION_RULES["circuit_breaker"]

Environment Variables:
    SELFHEALING_CB_ENABLED=true
    SELFHEALING_CB_FAILURE_THRESHOLD=5
    SELFHEALING_CB_RECOVERY_TIMEOUT=60
    ... etc

Reference:
- docs/self_healing/middleware_system/40_PYDANTIC_CONFIG_MIGRATION.md
"""

import structlog

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class CircuitBreakerSettings(BaseSettings):
    """
    Circuit Breaker configuration with validation.

    All defaults match core/config.py:CircuitBreakerConfig
    All validation rules match core/safe_defaults.py:VALIDATION_RULES["circuit_breaker"]
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Core Settings (from core/config.py lines 17-23)
    # ==========================================================================
    enabled: bool = Field(
        default=True,
        description="Enable circuit breaker protection",
    )
    failure_threshold: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Number of failures before opening circuit",
    )
    recovery_timeout: int = Field(
        default=60,
        ge=1,
        le=3600,
        description="Seconds to wait before attempting recovery",
    )
    success_threshold: int = Field(
        default=2,
        ge=1,
        le=100,
        description="Successes required to close circuit",
    )
    half_open_max_calls: int = Field(
        default=3,
        ge=1,
        le=100,
        description="Max calls allowed in half-open state",
    )
    half_open_request_limit: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="Request limit in half-open state",
    )
    excluded_exceptions: list[str] = Field(
        default_factory=lambda: [
            # Bulkhead 거부는 리소스 부족이지 서비스 장애가 아님
            # CB 실패 카운트에서 제외하여 불필요한 서킷 오픈 방지
            "selfhealing.resilience.bulkhead.exceptions.BulkheadFullError",
        ],
        description="Exception types to exclude from failure count",
    )

    # ==========================================================================
    # Rate Limit Cascade Detection (from core/config.py lines 25-27)
    # ==========================================================================
    rate_limit_cascade_threshold: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="429 errors before cascade detection triggers",
    )
    rate_limit_cascade_window_seconds: int = Field(
        default=60,
        ge=1,
        le=3600,
        description="Window for cascade detection",
    )

    # ==========================================================================
    # Self-DDoS Protection (from core/config.py lines 29-33)
    # Validation rules from core/safe_defaults.py lines 233-236
    # ==========================================================================
    self_ddos_protection_enabled: bool = Field(
        default=True,
        description="Enable self-DDoS protection",
    )
    self_ddos_request_threshold: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="Request threshold for self-DDoS detection",
    )
    self_ddos_window_seconds: int = Field(
        default=10,
        ge=1,
        le=300,
        description="Window for self-DDoS detection",
    )
    self_ddos_backoff_multiplier: float = Field(
        default=2.0,
        ge=1.0,
        le=10.0,
        description="Backoff multiplier for self-DDoS",
    )

    @field_validator("failure_threshold")
    @classmethod
    def validate_failure_threshold(cls, v: int) -> int:
        """Safe default fallback warning for extreme values."""
        if v > 50:
            logger.warning(
                "safe_default.high_consider_using_safety",
                v=v,
            )
        return v


# =============================================================================
# Singleton Pattern (cached settings)
# =============================================================================

_settings: CircuitBreakerSettings | None = None


def get_circuit_breaker_settings() -> CircuitBreakerSettings:
    """
    Get cached CircuitBreakerSettings instance.

    Returns:
        CircuitBreakerSettings: Singleton instance
    """
    global _settings
    if _settings is None:
        _settings = CircuitBreakerSettings()
    return _settings


def reset_circuit_breaker_settings() -> None:
    """
    Reset cached settings (for testing).

    Call this after modifying environment variables to reload settings.
    """
    global _settings
    _settings = None
