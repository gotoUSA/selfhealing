"""
Audit Sync Settings - Pydantic v2.

Background Sync Worker (WAL → 중앙 저장소 동기화) 설정.

Source:
- audit/sync_worker.py (SyncWorkerConfig)

Environment Variables:
    SELFHEALING_AUDIT_SYNC_SYNC_INTERVAL_SECONDS=1.0
    SELFHEALING_AUDIT_SYNC_BATCH_SIZE=100
    SELFHEALING_AUDIT_SYNC_MAX_RETRIES=3
    SELFHEALING_AUDIT_SYNC_RETRY_DELAY_SECONDS=1.0
    SELFHEALING_AUDIT_SYNC_RETRY_BACKOFF_MULTIPLIER=2.0
    SELFHEALING_AUDIT_SYNC_MAX_RETRY_DELAY_SECONDS=30.0
    SELFHEALING_AUDIT_SYNC_CLEANUP_AFTER_SECONDS=3600.0
    SELFHEALING_AUDIT_SYNC_METRICS_INTERVAL_SECONDS=60.0
"""

import structlog
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class AuditSyncSettings(BaseSettings):
    """
    Audit Sync Worker 설정.

    WAL에서 중앙 저장소로 감사 로그를 동기화하는 백그라운드 워커 설정.
    ADR-005 (Fail-Open + WAL 기반 누락 0 보장) 구현.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_AUDIT_SYNC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Sync Interval (from sync_worker.py line 42)
    # ==========================================================================
    sync_interval_seconds: float = Field(
        default=1.0,
        ge=0.1,
        le=60.0,
        description="동기화 주기 (초)",
    )

    # ==========================================================================
    # Batch Settings (from sync_worker.py line 45)
    # ==========================================================================
    batch_size: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="배치 크기",
    )

    # ==========================================================================
    # Retry Settings (from sync_worker.py lines 47-50)
    # ==========================================================================
    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="최대 재시도 횟수",
    )
    retry_delay_seconds: float = Field(
        default=1.0,
        ge=0.1,
        le=30.0,
        description="재시도 지연 시간 (초)",
    )
    retry_backoff_multiplier: float = Field(
        default=2.0,
        ge=1.0,
        le=5.0,
        description="재시도 지수 백오프 승수",
    )
    max_retry_delay_seconds: float = Field(
        default=30.0,
        ge=5.0,
        le=300.0,
        description="최대 재시도 지연 시간 (초)",
    )

    # ==========================================================================
    # Cleanup Settings (from sync_worker.py line 53)
    # ==========================================================================
    cleanup_after_seconds: float = Field(
        default=3600.0,
        ge=300.0,
        le=86400.0,
        description="오래된 엔트리 정리 기준 시간 (초)",
    )

    # ==========================================================================
    # Metrics Settings (from sync_worker.py line 56)
    # ==========================================================================
    metrics_interval_seconds: float = Field(
        default=60.0,
        ge=10.0,
        le=300.0,
        description="메트릭 리포팅 주기 (초)",
    )

    @field_validator("sync_interval_seconds")
    @classmethod
    def validate_sync_interval(cls, v: float) -> float:
        """동기화 주기가 너무 짧으면 경고."""
        if v < 0.5:
            logger.warning(
                "audit_sync_settings.very_short_consider_using",
                v=v,
            )
        return v


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: AuditSyncSettings | None = None


def get_audit_sync_settings() -> AuditSyncSettings:
    """
    캐시된 AuditSyncSettings 인스턴스 반환.

    Returns:
        AuditSyncSettings: 싱글톤 인스턴스
    """
    global _settings
    if _settings is None:
        _settings = AuditSyncSettings()
    return _settings


def reset_audit_sync_settings() -> None:
    """
    캐시된 Settings 초기화 (테스트용).
    """
    global _settings
    _settings = None
