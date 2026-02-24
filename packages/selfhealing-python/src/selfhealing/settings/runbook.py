"""
Runbook Executor Settings - Pydantic v2.

자동화된 런북 실행기의 설정입니다.

설정 항목:
- 런북 시스템 활성화 여부
- 승인 대기 시간 (MEDIUM 위험도)
- 동시 실행 런북 수 제한
- step 기본 타임아웃
- 분산 락 TTL

Environment Variables:
    SELFHEALING_RUNBOOK_ENABLED=true
    SELFHEALING_RUNBOOK_APPROVAL_TIMEOUT_SECONDS=300
    SELFHEALING_RUNBOOK_MAX_CONCURRENT_RUNBOOKS=3
    SELFHEALING_RUNBOOK_STEP_DEFAULT_TIMEOUT_SECONDS=120
    SELFHEALING_RUNBOOK_LOCK_TTL_SECONDS=600

Reference:
- docs/self_healing/middleware_system/272_RUNBOOK_ARCHITECTURE_OVERVIEW.md §7
"""

import structlog
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class RunbookSettings(BaseSettings):
    """
    자동화된 런북 실행기 설정.

    장애 패턴에 대한 복구 절차를 선언적으로 매칭하고
    자동 실행하는 오케스트레이션 계층의 설정을 관리합니다.

    Features:
    - 런북 시스템 전역 활성화/비활성화
    - MEDIUM 위험도 런북의 자동 승인 대기 시간
    - 동시 실행 런북 수 제한으로 리소스 보호
    - step별 기본 타임아웃으로 무한 대기 방지
    - 분산 락 TTL로 데드락 방지
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_RUNBOOK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Global Toggle
    # ==========================================================================
    enabled: bool = Field(
        default=True,
        description="런북 시스템 활성화 여부",
    )

    # ==========================================================================
    # Approval Settings
    # ==========================================================================
    approval_timeout_seconds: int = Field(
        default=300,
        ge=30,
        le=3600,
        description="MEDIUM 위험도 런북의 자동 승인 대기 시간 (초)",
    )

    # ==========================================================================
    # Concurrency Settings
    # ==========================================================================
    max_concurrent_runbooks: int = Field(
        default=3,
        ge=1,
        le=20,
        description="동시 실행 가능한 런북 수 제한",
    )

    # ==========================================================================
    # Timeout Settings
    # ==========================================================================
    step_default_timeout_seconds: int = Field(
        default=120,
        ge=10,
        le=1800,
        description="런북 step의 기본 타임아웃 (초)",
    )

    # ==========================================================================
    # Distributed Lock Settings
    # ==========================================================================
    lock_ttl_seconds: int = Field(
        default=600,
        ge=60,
        le=7200,
        description="분산 락 TTL (초). 런북 실행 중 동시 복구 방지",
    )

    @field_validator("approval_timeout_seconds")
    @classmethod
    def validate_approval_timeout(cls, v: int) -> int:
        """승인 대기 시간 경고."""
        if v < 60:
            logger.warning(
                "runbook.approval_timeout_too_short",
                seconds=v,
                msg="승인 대기 시간이 짧으면 검토 없이 자동 승인될 수 있음",
            )
        if v > 1800:
            logger.warning(
                "runbook.approval_timeout_too_long",
                seconds=v,
                msg="승인 대기 시간이 길면 장애 복구가 지연될 수 있음",
            )
        return v

    @field_validator("lock_ttl_seconds")
    @classmethod
    def validate_lock_ttl(cls, v: int) -> int:
        """락 TTL이 step 타임아웃보다 충분히 커야 함을 경고."""
        if v < 120:
            logger.warning(
                "runbook.lock_ttl_too_short",
                seconds=v,
                msg="락 TTL이 짧으면 실행 중 락이 만료될 수 있음",
            )
        return v


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: RunbookSettings | None = None


def get_runbook_settings() -> RunbookSettings:
    """Get cached RunbookSettings instance."""
    global _settings
    if _settings is None:
        _settings = RunbookSettings()
    return _settings


def reset_runbook_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
