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

    # ==========================================================================
    # Executor Settings (275번 Executor)
    # ==========================================================================
    global_timeout_seconds: int = Field(
        default=1800,
        ge=60,
        le=86400,
        description="런북 전체 실행 타임아웃 (초). 기본 30분",
    )

    lock_extend_seconds: int = Field(
        default=300,
        ge=60,
        le=3600,
        description="Lock TTL 연장 기본값 (초). Heartbeat 연장량",
    )

    lock_heartbeat_interval: int = Field(
        default=60,
        ge=10,
        le=600,
        description="Lock Heartbeat Polling 간격 (초). SagaOrchestrator.HEARTBEAT_INTERVAL과 동일",
    )

    idempotency_ttl_hours: int = Field(
        default=24,
        ge=1,
        le=168,
        description="멱등성 키 TTL (시간)",
    )

    context_ttl_seconds: int = Field(
        default=86400,
        ge=3600,
        le=604800,
        description="실행 컨텍스트 영속화 TTL (초). 기본 24시간",
    )

    resume_stale_threshold_seconds: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="resume 시 stale 거부 임계값 (초). 기본 1시간",
    )

    max_resume_count: int = Field(
        default=10,
        ge=1,
        le=100,
        description="무한 재개 방지 카운터. SagaOrchestrator.MAX_RESUME_COUNT와 동일",
    )

    # ==========================================================================
    # Approval Gate Settings (276번 ApprovalGate)
    # ==========================================================================
    approval_timer_seconds: int = Field(
        default=300,
        ge=30,
        le=7200,
        description="MEDIUM 위험도 런북의 타이머 자동 승인 대기 시간 (초). 기본 5분",
    )

    approval_max_wait_seconds: int = Field(
        default=3600,
        ge=0,
        le=86400,
        description="HIGH 위험도 런북의 최대 대기 시간 (초). 0이면 무기한 대기. 기본 1시간",
    )

    approval_reminder_intervals_minutes: list[int] = Field(
        default=[15, 30],
        description="승인 대기 중 리마인더 발송 간격 (분 단위 목록)",
    )

    approval_check_interval_seconds: int = Field(
        default=30,
        ge=10,
        le=300,
        description="Celery Beat에서 타이머/리마인더/타임아웃 폴링 간격 (초)",
    )

    force_execute_audit_required: bool = Field(
        default=True,
        description="CRITICAL 런북 강제 실행 시 감사 로그 필수 여부",
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
