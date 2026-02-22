"""
Governance Settings - Pydantic v2.

Single Source of Truth for governance configuration.

Replaces:
- core/config.py:GovernanceConfig (lines 322-361)
- core/safe_defaults.py:SAFE_DEFAULTS["governance"]
- core/safe_defaults.py:VALIDATION_RULES["governance"]

Environment Variables:
    SELFHEALING_GOVERNANCE_THRESHOLD_OPERATOR=0.15
    SELFHEALING_GOVERNANCE_EMERGENCY_EXPIRY_HOURS=8

    # Celery Task 재시도 설정
    SELFHEALING_GOVERNANCE_EXPIRY_CHECK_MAX_RETRIES=3
    SELFHEALING_GOVERNANCE_EXPIRY_CHECK_RETRY_DELAY=60
"""

import structlog
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class GovernanceSettings(BaseSettings):
    """
    Governance configuration with validation.

    RBAC thresholds, emergency mode auto-recovery, and notification settings.

    All defaults match core/config.py:GovernanceConfig
    All validation rules match core/safe_defaults.py:VALIDATION_RULES["governance"]
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_GOVERNANCE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Risk-Based Access Control (from core/config.py lines 332-334)
    # Validation rules from core/safe_defaults.py lines 319-325
    # ==========================================================================
    threshold_operator: float = Field(
        default=0.15,
        ge=0.01,
        le=1.0,
        description="Operator approval threshold (15%)",
    )
    threshold_admin: float = Field(
        default=0.30,
        ge=0.01,
        le=1.0,
        description="Admin approval threshold (30%)",
    )

    # ==========================================================================
    # Emergency Escalation (Break Glass) (from core/config.py lines 340-343)
    # ==========================================================================
    emergency_expiry_hours: int = Field(
        default=8,
        ge=1,
        le=48,
        description="Hours until automatic emergency mode expiry",
    )
    emergency_warning_hours: int = Field(
        default=4,
        ge=1,
        le=24,
        description="Hours when warning starts",
    )
    emergency_final_warning_hours: int = Field(
        default=6,
        ge=1,
        le=24,
        description="Hours for final warning",
    )

    emergency_min_level: int = Field(
        default=2,
        ge=1,
        le=3,
        description="거버넌스 체크에서 비상 모드 차단 최소 레벨 (1=LEVEL_1, 2=LEVEL_2, 3=LEVEL_3)",
    )

    # ==========================================================================
    # Operating Mode (from core/config.py lines 349)
    # ==========================================================================
    default_mode: str = Field(
        default="NORMAL",
        description="Default operating mode (NORMAL or STRICT)",
    )

    # ==========================================================================
    # Notification Settings (from core/config.py lines 355-360)
    # ==========================================================================
    notify_on_emergency: bool = Field(
        default=True,
        description="Send notification on emergency activation",
    )
    notify_channels: list[str] = Field(
        default_factory=lambda: ["slack", "email"],
        description="Notification channels for governance events",
    )
    emergency_slack_channel: str = Field(
        default="#emergency-alerts",
        description="Slack channel for emergency alerts",
    )
    emergency_email_recipients: list[str] = Field(
        default_factory=list,
        description="Email recipients for emergency alerts",
    )

    # ==========================================================================
    # 4-Eyes Principle (from core/config.py lines 366-368)
    # ==========================================================================
    four_eyes_enabled: bool = Field(
        default=False,
        description="Enable dual approval workflow",
    )
    four_eyes_expiry_hours: int = Field(
        default=24,
        ge=1,
        le=168,
        description="Approval request expiry hours",
    )

    # ==========================================================================
    # Audit Settings (from safe_defaults.py governance)
    # ==========================================================================
    approval_timeout_hours: int = Field(
        default=24,
        ge=1,
        le=168,
        description="Approval wait timeout hours",
    )
    max_approval_retries: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum approval retry attempts",
    )
    audit_log_retention_days: int = Field(
        default=90,
        ge=7,
        le=365,
        description="Audit log retention days",
    )
    require_reason_for_changes: bool = Field(
        default=True,
        description="Require reason for configuration changes",
    )

    # ==========================================================================
    # Governance Check Cache TTL (from governance_checks.py line 296)
    # ==========================================================================
    cache_ttl: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        description="거버넌스 체크 캐시 TTL (초). 시스템 상태 캐싱에 사용.",
    )

    # ==========================================================================
    # Break Glass (비상 탈출구) - 172_CANARY_ERROR_BUDGET_GATE.md §13.2
    # ==========================================================================
    break_glass_enabled: bool = Field(
        default=False,
        description="긴급 상황 시 모든 거버넌스 체크 우회 (PIR 필수). 환경변수: SELFHEALING_GOVERNANCE_BREAK_GLASS_ENABLED=true",
    )

    break_glass_audit_required: bool = Field(
        default=True,
        description="Break Glass 사용 시 Audit 로그 필수",
    )

    # ==========================================================================
    # Celery Task 재시도 설정 (check_emergency_mode_expiry_task)
    # ==========================================================================
    expiry_check_max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="긴급 모드 만료 체크 태스크 최대 재시도 횟수",
    )
    expiry_check_retry_delay: int = Field(
        default=60,
        ge=10,
        le=600,
        description="긴급 모드 만료 체크 태스크 재시도 지연 (초)",
    )

    @field_validator("default_mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        """Validate operating mode."""
        v_upper = v.upper()
        if v_upper not in {"NORMAL", "STRICT"}:
            raise ValueError("default_mode must be 'NORMAL' or 'STRICT'")
        return v_upper


# Singleton instance (cached)
_settings: GovernanceSettings | None = None


def get_governance_settings() -> GovernanceSettings:
    """Get cached GovernanceSettings instance."""
    global _settings
    if _settings is None:
        _settings = GovernanceSettings()
    return _settings


def reset_governance_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
