"""
Chaos Settings - Pydantic v2.

Single Source of Truth for chaos engineering configuration.

Replaces:
- core/config.py:ChaosConfig (lines 575-605)
- core/safe_defaults.py:SAFE_DEFAULTS["chaos"]
- core/safe_defaults.py:VALIDATION_RULES["chaos"]

Environment Variables:
    SELFHEALING_CHAOS_MAX_BLAST_RADIUS=0.10
    SELFHEALING_CHAOS_DRY_RUN_DEFAULT=true

Reference:
- docs/self_healing/middleware_system/40_PYDANTIC_CONFIG_MIGRATION.md
"""

import logging
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class ChaosSettings(BaseSettings):
    """
    Chaos Engineering configuration with validation.

    Safety Guard, Blast Radius, and experiment controls.

    All defaults match core/config.py:ChaosConfig
    All validation rules match core/safe_defaults.py:VALIDATION_RULES["chaos"]
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CHAOS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Safety Guard (from core/config.py lines 582-586)
    # Validation rules from core/safe_defaults.py lines 301-304
    # ==========================================================================
    max_blast_radius: float = Field(
        default=0.10,
        ge=0.0,
        le=0.5,
        description="Maximum blast radius (10% default, 50% max)",
    )
    max_failure_rate: float = Field(
        default=0.20,
        ge=0.0,
        le=0.5,
        description="Maximum failure rate (20% default, 50% max)",
    )
    auto_rollback_enabled: bool = Field(
        default=True,
        description="Enable automatic rollback on threshold breach",
    )
    rollback_threshold: float = Field(
        default=0.05,
        ge=0.0,
        le=0.5,
        description="Error rate threshold for rollback (5%)",
    )

    # ==========================================================================
    # Experiment Controls (from core/config.py lines 588-592)
    # ==========================================================================
    dry_run_default: bool = Field(
        default=True,
        description="Default to dry run mode",
    )
    require_approval: bool = Field(
        default=False,
        description="Require approval before experiments",
    )
    experiment_timeout_seconds: int = Field(
        default=300,
        ge=60,
        le=3600,
        description="Experiment timeout in seconds",
    )

    # ==========================================================================
    # Stop Conditions (from core/config.py lines 594-596)
    # ==========================================================================
    stop_on_error_rate: float = Field(
        default=0.10,
        ge=0.0,
        le=0.5,
        description="Error rate threshold to stop experiment",
    )
    stop_on_latency_increase_pct: float = Field(
        default=50.0,
        ge=0.0,
        le=200.0,
        description="Latency increase percentage threshold to stop",
    )

    # ==========================================================================
    # Additional from safe_defaults
    # ==========================================================================
    enabled: bool = Field(
        default=False,
        description="Enable chaos engineering (default disabled for safety)",
    )
    failure_rate: float = Field(
        default=0.01,
        ge=0.0,
        le=0.5,
        description="Default failure injection rate (1%)",
    )
    latency_max_ms: int = Field(
        default=1000,
        ge=0,
        le=10000,
        description="Maximum latency injection in milliseconds",
    )

    @field_validator("max_blast_radius", "max_failure_rate")
    @classmethod
    def validate_safety_limits(cls, v: float) -> float:
        """Warn if safety limits are set high."""
        if v > 0.3:
            logger.warning(
                f"[SafeDefault] High chaos limit={v}, "
                "consider using <= 0.3 (30%) for safety"
            )
        return v


# Singleton instance (cached)
_settings: Optional[ChaosSettings] = None


def get_chaos_settings() -> ChaosSettings:
    """Get cached ChaosSettings instance."""
    global _settings
    if _settings is None:
        _settings = ChaosSettings()
    return _settings


def reset_chaos_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
