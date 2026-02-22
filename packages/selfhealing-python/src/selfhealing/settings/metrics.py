"""
Metrics Settings - Pydantic v2.

Single Source of Truth for metrics collection configuration.

Replaces:
- core/config.py:MetricsConfig (lines 241-250)
- core/safe_defaults.py:SAFE_DEFAULTS["metrics"]
- core/safe_defaults.py:VALIDATION_RULES["metrics"]

Environment Variables:
    SELFHEALING_METRICS_ENABLED=true
    SELFHEALING_METRICS_COLLECTION_INTERVAL=60

Reference:
- docs/self_healing/middleware_system/40_PYDANTIC_CONFIG_MIGRATION.md
"""

import structlog

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class MetricsSettings(BaseSettings):
    """
    Metrics collection configuration with validation.

    All defaults match core/config.py:MetricsConfig
    All validation rules match core/safe_defaults.py:VALIDATION_RULES["metrics"]
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_METRICS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Core Settings (from core/config.py lines 243-247)
    # Validation rules from core/safe_defaults.py lines 284-286
    # ==========================================================================
    enabled: bool = Field(
        default=True,
        description="Enable metrics collection",
    )
    prefix: str = Field(
        default="selfhealing",
        description="Prefix for all metrics names",
    )
    collection_interval: int = Field(
        default=60,
        ge=1,
        le=3600,
        description="Metrics collection interval in seconds",
    )
    export_prometheus: bool = Field(
        default=True,
        description="Export metrics to Prometheus",
    )

    # ==========================================================================
    # Jitter Settings (Thundering Herd prevention)
    # From core/config.py lines 249-250
    # ==========================================================================
    jitter_enabled: bool = Field(
        default=True,
        description="Enable jitter for collection intervals",
    )
    jitter_max_delay_seconds: float = Field(
        default=60.0,
        ge=0.0,
        le=300.0,
        description="Maximum jitter delay in seconds",
    )

    # ==========================================================================
    # Snapshot Storage - from metrics/snapshot_storage.py
    # ==========================================================================
    snapshot_max_age: int = Field(
        default=3600,
        ge=300,
        le=86400,
        description="메트릭 스냅샷 최대 유효 기간 (초). 기본 1시간.",
    )


# Singleton instance (cached)
_settings: MetricsSettings | None = None


def get_metrics_settings() -> MetricsSettings:
    """Get cached MetricsSettings instance."""
    global _settings
    if _settings is None:
        _settings = MetricsSettings()
    return _settings


def reset_metrics_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
