"""
Security Settings - Pydantic v2.

Single Source of Truth for security-related configuration.

Replaces:
- core/config.py:SecurityConfig (lines 174-185)
- core/safe_defaults.py:SAFE_DEFAULTS["security"]
- core/safe_defaults.py:VALIDATION_RULES["security"]

Environment Variables:
    SELFHEALING_SECURITY_RATE_LIMIT_WINDOW_SECONDS=60
    SELFHEALING_SECURITY_RATE_LIMIT_MAX_REQUESTS=100
    SELFHEALING_SECURITY_INJECTION_BAN_HOURS=24
    ... etc

Reference:
- docs/self_healing/middleware_system/40_PYDANTIC_CONFIG_MIGRATION.md
"""

import structlog

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class SecuritySettings(BaseSettings):
    """
    Security-related thresholds and timeouts with validation.

    All defaults match core/config.py:SecurityConfig
    All validation rules match core/safe_defaults.py:VALIDATION_RULES["security"]

    Note: Some fields are FATAL configs (see core/safe_defaults.py:FATAL_CONFIGS)
    - rate_limit_max_requests: DDoS protection
    - injection_ban_hours: SQL injection response
    - failed_login_threshold: Brute force protection
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_SECURITY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Rate Limiting Settings (from core/config.py lines 176-178)
    # Validation rules from core/safe_defaults.py lines 263-268
    # ==========================================================================
    rate_limit_window_seconds: int = Field(
        default=60,
        ge=1,
        le=3600,
        description="Window for rate limiting in seconds",
    )
    rate_limit_max_requests: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="Maximum requests allowed in window (FATAL config)",
    )

    # ==========================================================================
    # Ban Settings (from core/config.py lines 179-181)
    # ==========================================================================
    temporary_ban_hours: int = Field(
        default=1,
        ge=1,
        le=168,
        description="Hours for temporary ban",
    )
    permanent_ban_threshold: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Number of temp bans before permanent ban",
    )

    # ==========================================================================
    # Cache and Security (from core/config.py lines 182-185)
    # ==========================================================================
    suspicious_ip_cache_timeout: int = Field(
        default=86400,
        ge=3600,
        le=604800,
        description="Suspicious IP cache timeout in seconds (1h-7d)",
    )
    injection_ban_hours: int = Field(
        default=24,
        ge=1,
        le=720,
        description="Hours to ban for SQL injection attempts (FATAL config)",
    )
    failed_login_threshold: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Failed logins before action (FATAL config)",
    )

    # ==========================================================================
    # Cache Prefixes (from core/config.py lines 186-187)
    # ==========================================================================
    suspicious_ip_cache_prefix: str = Field(
        default="security:suspicious_ip:",
        description="Redis key prefix for suspicious IPs",
    )
    banned_ip_cache_prefix: str = Field(
        default="security:banned_ip:",
        description="Redis key prefix for banned IPs",
    )

    @field_validator("rate_limit_max_requests")
    @classmethod
    def validate_rate_limit_max_requests(cls, v: int) -> int:
        """
        FATAL config: rate_limit_max_requests.

        If too high, system is vulnerable to DDoS.
        """
        if v > 1000:
            logger.warning(
                f"[FATAL_CONFIG] Very high rate_limit_max_requests={v}, "
                "system may be vulnerable to DDoS. Consider <= 1000"
            )
        return v

    @field_validator("injection_ban_hours")
    @classmethod
    def validate_injection_ban_hours(cls, v: int) -> int:
        """
        FATAL config: injection_ban_hours.

        SQL injection attempts should result in meaningful bans.
        """
        if v < 12:
            logger.warning(
                f"[FATAL_CONFIG] Short injection_ban_hours={v}, "
                "consider >= 12 hours for SQL injection attempts"
            )
        return v

    @field_validator("failed_login_threshold")
    @classmethod
    def validate_failed_login_threshold(cls, v: int) -> int:
        """
        FATAL config: failed_login_threshold.

        Brute force protection should be strict.
        """
        if v > 20:
            logger.warning(
                f"[FATAL_CONFIG] High failed_login_threshold={v}, "
                "consider <= 20 for brute force protection"
            )
        return v


# =============================================================================
# Singleton Pattern (cached settings)
# =============================================================================

_settings: SecuritySettings | None = None


def get_security_settings() -> SecuritySettings:
    """
    Get cached SecuritySettings instance.

    Returns:
        SecuritySettings: Singleton instance
    """
    global _settings
    if _settings is None:
        _settings = SecuritySettings()
    return _settings


def reset_security_settings() -> None:
    """
    Reset cached settings (for testing).

    Call this after modifying environment variables to reload settings.
    """
    global _settings
    _settings = None
