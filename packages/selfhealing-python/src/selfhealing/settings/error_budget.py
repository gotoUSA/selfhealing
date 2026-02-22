"""
Error Budget Settings - Pydantic v2.

Single Source of Truth for error budget configuration.

Replaces:
- core/config.py:ErrorBudgetConfig (lines 278-319)
- core/safe_defaults.py:SAFE_DEFAULTS["error_budget"]
- core/safe_defaults.py:VALIDATION_RULES["error_budget"]

Environment Variables:
    SELFHEALING_ERROR_BUDGET_THRESHOLD_HEALTHY=75.0
    SELFHEALING_ERROR_BUDGET_BURN_RATE_FAST_CRITICAL=14.4

Reference:
- docs/self_healing/middleware_system/40_PYDANTIC_CONFIG_MIGRATION.md
"""

import structlog

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class ErrorBudgetSettings(BaseSettings):
    """
    Error Budget thresholds configuration with validation.

    Google SRE recommended values are used as defaults.

    All defaults match core/config.py:ErrorBudgetConfig
    All validation rules match core/safe_defaults.py:VALIDATION_RULES["error_budget"]
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_ERROR_BUDGET_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Error Budget Thresholds (%) (from core/config.py lines 285-289)
    # Validation rules from core/safe_defaults.py lines 287-294
    # ==========================================================================
    threshold_healthy: float = Field(
        default=75.0,
        ge=50.0,
        le=100.0,
        description="Healthy threshold (%) - 75% or above is normal",
    )
    threshold_caution: float = Field(
        default=50.0,
        ge=20.0,
        le=80.0,
        description="Caution threshold (%) - 50-75% requires attention",
    )
    threshold_warning: float = Field(
        default=20.0,
        ge=5.0,
        le=50.0,
        description="Warning threshold (%) - 20-50% is warning level",
    )
    threshold_critical: float = Field(
        default=0.0,
        ge=0.0,
        le=20.0,
        description="Critical threshold (%) - below 20% is critical",
    )

    # ==========================================================================
    # Burn Rate Thresholds (Google SRE) (from core/config.py lines 291-295)
    # ==========================================================================
    burn_rate_fast_critical: float = Field(
        default=14.4,
        ge=10.0,
        le=50.0,
        description="Fast burn rate critical threshold (2% in 1 hour)",
    )
    burn_rate_fast_warning: float = Field(
        default=6.0,
        ge=3.0,
        le=15.0,
        description="Fast burn rate warning threshold",
    )
    burn_rate_slow_warning: float = Field(
        default=3.0,
        ge=1.0,
        le=10.0,
        description="Slow burn rate warning threshold (5% in 6 hours)",
    )
    burn_rate_slow_info: float = Field(
        default=1.0,
        ge=0.5,
        le=3.0,
        description="Slow burn rate info threshold (normal consumption)",
    )

    # ==========================================================================
    # Fail-Safe Settings (from core/config.py lines 297-299)
    # ==========================================================================
    failsafe_alert_enabled: bool = Field(
        default=True,
        description="Enable alerts when fail-safe is triggered",
    )
    failsafe_cooldown_seconds: int = Field(
        default=300,
        ge=60,
        le=3600,
        description="Cooldown between consecutive fail-safe alerts",
    )

    # ==========================================================================
    # Heartbeat (Dead Man's Snitch) Settings (from core/config.py lines 305-308)
    # ==========================================================================
    heartbeat_enabled: bool = Field(
        default=True,
        description="Enable heartbeat monitoring",
    )
    heartbeat_interval_seconds: int = Field(
        default=60,
        ge=10,
        le=300,
        description="Heartbeat interval in seconds",
    )
    heartbeat_timeout_seconds: int = Field(
        default=120,
        ge=30,
        le=600,
        description="Heartbeat timeout - dead judgment if exceeded",
    )

    # ==========================================================================
    # Recovery Notification Settings (from core/config.py lines 314-316)
    # ==========================================================================
    recovery_alert_enabled: bool = Field(
        default=True,
        description="Enable alerts on recovery",
    )
    recovery_alert_include_downtime: bool = Field(
        default=True,
        description="Include downtime information in recovery alerts",
    )

    # ==========================================================================
    # Override Escalation Settings (from core/config.py lines 322-325)
    # ==========================================================================
    escalation_enabled: bool = Field(
        default=True,
        description="Enable override escalation",
    )
    escalation_channel: str = Field(
        default="#governance",
        description="Escalation notification channel",
    )
    escalation_mention: str = Field(
        default="@cto @security",
        description="Mention targets for escalation",
    )

    # ==========================================================================
    # Crisis Multiplier Settings - from services/error_budget/multiplier.py
    # ==========================================================================
    multiplier_cache_ttl: float = Field(
        default=30.0,
        ge=5.0,
        le=300.0,
        description="위기 가중치 캐시 TTL (초). 기본 30초.",
    )
    multiplier_max: float = Field(
        default=10.0,
        ge=1.0,
        le=50.0,
        description="최대 위기 가중치. 과도한 Error Budget 소진 방지.",
    )

    # ==========================================================================
    # Exception Budget Weight Settings - Q12 구현
    # ==========================================================================
    weight_combine_policy: str = Field(
        default="MAX",
        pattern=r"^(MAX|SUM|MULTIPLY)$",
        description=("EmergencyLevel과 ErrorCode 가중치 결합 정책. " "MAX: 최댓값 (권장), SUM: 합산, MULTIPLY: 곱셈 (비권장)"),
    )
    exception_weights_json: str | None = Field(
        default=None,
        description=(
            "ErrorCode별 가중치 JSON 설정. "
            '예: {"category_weights": {"SYSTEM": 1.0}, "code_weights": {"SERVICE_TIMEOUT": 0.5}}'
        ),
    )

    # ==========================================================================
    # Validators
    # ==========================================================================
    @field_validator("weight_combine_policy")
    @classmethod
    def validate_weight_combine_policy(cls, v: str) -> str:
        """가중치 결합 정책 검증."""
        valid_policies = {"MAX", "SUM", "MULTIPLY"}
        v_upper = v.upper()
        if v_upper not in valid_policies:
            raise ValueError(f"Invalid policy: {v}. Must be one of {valid_policies}")
        return v_upper


# Singleton instance (cached)
_settings: ErrorBudgetSettings | None = None


def get_error_budget_settings() -> ErrorBudgetSettings:
    """Get cached ErrorBudgetSettings instance."""
    global _settings
    if _settings is None:
        _settings = ErrorBudgetSettings()
    return _settings


def reset_error_budget_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
