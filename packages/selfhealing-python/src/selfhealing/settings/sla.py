"""
SLA Settings - Pydantic v2.

Single Source of Truth for SLA configuration.

Replaces:
- core/config.py:SLAConfig (lines 59-86)
- core/safe_defaults.py:SAFE_DEFAULTS["sla"]
- core/safe_defaults.py:VALIDATION_RULES["sla"]

Environment Variables:
    SELFHEALING_SLA_DEFAULT_HOURS=24

SLA 일괄 설정 및 도메인별 임계값 관리.
"""

from datetime import timedelta

import structlog
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class SLASettings(BaseSettings):
    """
    SLA thresholds configuration with validation.

    Uses a dictionary-based approach for domain-specific thresholds.

    All defaults match core/config.py:SLAConfig
    All validation rules match core/safe_defaults.py:VALIDATION_RULES["sla"]
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_SLA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Core Settings (from core/config.py lines 70-71)
    # Validation rules from core/safe_defaults.py lines 305
    # ==========================================================================
    default_hours: int = Field(
        default=24,
        ge=1,
        le=720,
        description="Default SLA threshold in hours for unregistered domains",
    )

    # Domain-specific thresholds (configured by adapters)
    # Example: {"payment": 1, "order": 2, "notification": 24}
    thresholds_by_domain: dict[str, int] = Field(
        default_factory=dict,
        description="Domain-specific SLA thresholds in hours",
    )

    @field_validator("default_hours")
    @classmethod
    def validate_default_hours(cls, v: int) -> int:
        """Warn for very long SLA thresholds."""
        if v > 168:  # More than a week
            logger.warning(
                "safe_default.very_long_sla_consider",
                setting_value=v,
            )
        return v

    def get_threshold(self, domain: str) -> timedelta:
        """Get the SLA threshold for a domain."""
        hours = self.thresholds_by_domain.get(domain.lower(), self.default_hours)
        return timedelta(hours=hours)

    def get_all_thresholds(self) -> dict[str, timedelta]:
        """Get all configured SLA thresholds as a dictionary."""
        result = {
            domain: timedelta(hours=hours)
            for domain, hours in self.thresholds_by_domain.items()
        }
        if not result:
            result["default"] = timedelta(hours=self.default_hours)
        return result


# Singleton instance (cached)
_settings: SLASettings | None = None


def get_sla_settings() -> SLASettings:
    """Get cached SLASettings instance."""
    global _settings
    if _settings is None:
        _settings = SLASettings()
    return _settings


def reset_sla_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
