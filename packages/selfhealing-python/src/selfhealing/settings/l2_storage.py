"""
L2 Storage Settings - Pydantic v2.

Single Source of Truth for L2 storage configuration.

Replaces:
- core/config.py:L2StorageConfig (lines 470-512)
- core/safe_defaults.py:SAFE_DEFAULTS["l2_storage"]
- core/safe_defaults.py:VALIDATION_RULES["l2_storage"]

Environment Variables:
    SELFHEALING_L2_STORAGE_REDIS_TIMEOUT_MS=1000
    SELFHEALING_L2_STORAGE_RECONCILIATION_INTERVAL_SECONDS=300

Reference:
- docs/self_healing/middleware_system/40_PYDANTIC_CONFIG_MIGRATION.md
"""

import structlog

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class L2StorageSettings(BaseSettings):
    """
    L2 Storage runtime configuration with validation.

    Timeouts, shadow logging, and health check settings.

    All defaults match core/config.py:L2StorageConfig
    All validation rules match core/safe_defaults.py:VALIDATION_RULES["l2_storage"]
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_L2_STORAGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Enable/Disable (from safe_defaults l2_storage)
    # ==========================================================================
    enabled: bool = Field(
        default=False,
        description="Enable L2 storage (disabled by default, requires explicit activation)",
    )

    # ==========================================================================
    # Timeouts (ms) (from core/config.py lines 492-494)
    # Validation rules from core/safe_defaults.py lines 333-337
    # ==========================================================================
    redis_timeout_ms: int = Field(
        default=1000,
        ge=100,
        le=10000,
        description="Redis connection timeout in milliseconds",
    )
    database_timeout_ms: int = Field(
        default=200,
        ge=50,
        le=5000,
        description="Database connection timeout in milliseconds",
    )
    fallback_timeout_ms: int = Field(
        default=100,
        ge=50,
        le=2000,
        description="Fallback timeout in milliseconds",
    )

    # ==========================================================================
    # Shadow Logging (from core/config.py lines 497-498)
    # ==========================================================================
    shadow_log_enabled: bool = Field(
        default=True,
        description="Enable shadow logging for debugging",
    )
    shadow_log_max_entries: int = Field(
        default=1000,
        ge=100,
        le=10000,
        description="Maximum shadow log entries",
    )

    # ==========================================================================
    # Reconciliation (from core/config.py lines 501-502)
    # ==========================================================================
    reconciliation_enabled: bool = Field(
        default=True,
        description="Enable data reconciliation",
    )
    reconciliation_interval_seconds: int = Field(
        default=300,
        ge=60,
        le=3600,
        description="Reconciliation check interval in seconds",
    )
    reconciliation_jitter_percent: int = Field(
        default=20,
        ge=0,
        le=50,
        description="Jitter percentage for reconciliation",
    )
    reconciliation_jitter_min_seconds: float = Field(
        default=0.0,
        ge=0.0,
        le=60.0,
        description="Minimum jitter in seconds",
    )
    reconciliation_jitter_max_seconds: float = Field(
        default=5.0,
        ge=0.0,
        le=60.0,
        description="Maximum jitter in seconds",
    )

    # ==========================================================================
    # Health Check (from core/config.py lines 508-510)
    # ==========================================================================
    health_check_interval_seconds: float = Field(
        default=30.0,
        ge=5.0,
        le=300.0,
        description="Health check interval in seconds",
    )
    health_check_timeout_ms: int = Field(
        default=100,
        ge=50,
        le=5000,
        description="Health check timeout in milliseconds",
    )

    # ==========================================================================
    # Connection Pool (from safe_defaults l2_storage)
    # ==========================================================================
    file_fallback_enabled: bool = Field(
        default=True,
        description="Enable file fallback when Redis fails",
    )
    max_retry_on_failure: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum retries on failure",
    )
    connection_pool_size: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Connection pool size",
    )


# Singleton instance (cached)
_settings: L2StorageSettings | None = None


def get_l2_storage_settings() -> L2StorageSettings:
    """Get cached L2StorageSettings instance."""
    global _settings
    if _settings is None:
        _settings = L2StorageSettings()
    return _settings


def reset_l2_storage_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
