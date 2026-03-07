"""
SLO Settings - Pydantic v2.

Single Source of Truth for SLO configuration.

Replaces:
- core/config.py:SLOConfigRuntime (lines 112-134)
- core/safe_defaults.py:SAFE_DEFAULTS["slo"]
- core/safe_defaults.py:VALIDATION_RULES["slo"]

Environment Variables:
    SELFHEALING_SLO_DEFAULT_WINDOW_DAYS=30
    SELFHEALING_SLO_DEFAULT_TARGET=0.999

Reference:
- docs/self_healing/middleware_system/40_PYDANTIC_CONFIG_MIGRATION.md
"""

from typing import Any

import structlog
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class SLOSettings(BaseSettings):
    """
    SLO (Service Level Objective) configuration with validation.

    All defaults match core/config.py:SLOConfigRuntime
    All validation rules match core/safe_defaults.py:VALIDATION_RULES["slo"]
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_SLO_",
        env_file=None,
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Default SLO Settings (from core/config.py lines 120-128)
    # Validation rules from core/safe_defaults.py lines 306-310
    # ==========================================================================
    default_window_days: int = Field(
        default=30,
        ge=1,
        le=365,
        description="Default measurement window in days",
    )
    default_target: float = Field(
        default=0.999,
        ge=0.9,
        le=1.0,
        description="Default SLO target (e.g., 0.999 = 99.9%)",
    )
    default_fast_burn_rate: float = Field(
        default=14.4,
        ge=1.0,
        le=100.0,
        description="Default fast burn rate threshold (Google SRE: 14.4)",
    )
    default_slow_burn_rate: float = Field(
        default=3.0,
        ge=0.5,
        le=50.0,
        description="Default slow burn rate threshold (Google SRE: 3.0)",
    )

    # SLO definitions list (runtime dynamically managed)
    # Each item is a dict serialized from SLODefinition
    slos: list[dict[str, Any]] = Field(
        default_factory=list,
        description="List of SLO definitions",
    )

    @field_validator("default_target")
    @classmethod
    def validate_target(cls, v: float) -> float:
        """Warn for extremely high targets."""
        if v > 0.9999:
            logger.warning(
                "safe_default.very_high_slo_consider",
                setting_value=v,
            )
        return v


def get_slo_settings() -> "SLOSettings":
    from selfhealing.settings.root import get_config

    return get_config().slo_group.slo


def reset_slo_settings() -> None:
    from selfhealing.settings.root import get_config

    try:
        del get_config().slo_group.__dict__["slo"]
    except KeyError:
        pass
