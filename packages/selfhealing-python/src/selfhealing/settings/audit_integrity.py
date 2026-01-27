"""
Audit Integrity Settings - Pydantic v2.

감사 무결성 및 Cold Storage 관련 설정입니다.

Replaces:
- audit/integrity/sequence.py:DEFAULT_PENDING_TTL_SECONDS, DEFAULT_ORPHAN_TTL_SECONDS
- audit/integrity/cold_storage.py:ARCHIVE_THRESHOLD_DAYS, DEFAULT_COLD_RETENTION_YEARS
- audit/config.py:integrity_check_interval, hash_chain_lock_timeout

Environment Variables:
    SELFHEALING_AUDIT_INTEGRITY_PENDING_TTL_SECONDS=30
    SELFHEALING_AUDIT_INTEGRITY_ORPHAN_TTL_SECONDS=86400
    SELFHEALING_AUDIT_INTEGRITY_ARCHIVE_THRESHOLD_DAYS=7

Reference:
- docs/self_healing/middleware_system/92_CONFIG_IMPLEMENTATION_GUIDE.md (Week 4 [25])
- docs/self_healing/middleware_system/91_CONFIG_INVENTORY.md §9.7, §9.8, §9.9
"""

import logging

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class AuditIntegritySettings(BaseSettings):
    """
    감사 무결성 및 Cold Storage 설정.

    시퀀스 TTL:
    - pending_ttl_seconds: 보류 중인 항목 TTL (30초)
    - orphan_ttl_seconds: 고아 항목 TTL (24시간)

    Cold Storage:
    - archive_threshold_days: 아카이브 임계치 (7일)
    - cold_retention_years: 콜드 보관 기간 (7년, 법적 요구사항)

    무결성 검사:
    - integrity_check_interval: 검사 간격 (1시간)
    - hash_chain_lock_timeout: 해시 체인 락 타임아웃 (5초)
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_AUDIT_INTEGRITY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Sequence TTL - from audit/integrity/sequence.py
    # ==========================================================================
    pending_ttl_seconds: int = Field(
        default=30,
        ge=10,
        le=300,
        description="보류 중인 항목 TTL (초)",
    )

    orphan_ttl_seconds: int = Field(
        default=86400,
        ge=3600,
        le=604800,  # 7일
        description="고아 항목 TTL (초). 24시간 기본.",
    )

    # ==========================================================================
    # Cold Storage - from audit/integrity/cold_storage.py
    # ==========================================================================
    archive_threshold_days: int = Field(
        default=7,
        ge=1,
        le=30,
        description="Hot → Cold 아카이브 임계치 (일)",
    )

    cold_retention_years: int = Field(
        default=7,
        ge=1,
        le=10,
        description="Cold Storage 보관 기간 (년). 법적 요구사항.",
    )

    # ==========================================================================
    # Integrity Check - from audit/config.py
    # ==========================================================================
    integrity_check_interval: int = Field(
        default=3600,
        ge=300,
        le=86400,
        description="무결성 검사 간격 (초). 1시간 기본.",
    )

    hash_chain_lock_timeout: float = Field(
        default=5.0,
        ge=1.0,
        le=30.0,
        description="해시 체인 락 타임아웃 (초)",
    )

    # ==========================================================================
    # Verification - from audit/integrity
    # ==========================================================================
    verification_batch_size: int = Field(
        default=100,
        ge=10,
        le=1000,
        description="검증 배치 크기",
    )

    max_verification_retries: int = Field(
        default=3,
        ge=1,
        le=10,
        description="검증 최대 재시도 횟수",
    )

    # ==========================================================================
    # Retention - additional
    # ==========================================================================
    retention_days: int = Field(
        default=365,
        ge=90,
        le=3650,
        description="일반 감사 로그 보관 기간 (일). 1년 기본.",
    )

    # ==========================================================================
    # Anchor - from audit/integrity/anchor.py
    # ==========================================================================
    anchor_retention_days: int = Field(
        default=90,
        ge=30,
        le=365,
        description="일일 해시 앵커 보관 기간 (일). 90일 기본.",
    )

    # ==========================================================================
    # Cross Cluster - from audit/integrity/cross_cluster_linker.py
    # ==========================================================================
    cross_cluster_local_ttl_days: int = Field(
        default=90,
        ge=30,
        le=365,
        description="로컬 클러스터 앵커 TTL (일). 90일 기본.",
    )

    cross_cluster_global_ttl_days: int = Field(
        default=365,
        ge=90,
        le=730,
        description="글로벌 클러스터 앵커 TTL (일). 1년 기본.",
    )

    # ==========================================================================
    # Health Score - from audit/integrity/health_score.py
    # ==========================================================================
    health_healthy_threshold: float = Field(
        default=95.0,
        ge=80.0,
        le=100.0,
        description="무결성 건강 상태 임계값 (%). 95% 이상이면 정상.",
    )

    health_warning_threshold: float = Field(
        default=80.0,
        ge=50.0,
        le=95.0,
        description="무결성 경고 상태 임계값 (%). 80% 이상이면 경고.",
    )

    health_critical_threshold: float = Field(
        default=50.0,
        ge=0.0,
        le=80.0,
        description="무결성 위험 상태 임계값 (%). 50% 미만이면 위험.",
    )

    # ==========================================================================
    # S3 WORM - from audit/backends/s3_worm.py
    # ==========================================================================
    s3_worm_retention_days: int = Field(
        default=365,
        ge=90,
        le=2555,
        description="S3 WORM 객체 보관 기간 (일). 1년 기본, 법적 요구사항에 따라 설정.",
    )

    @model_validator(mode="after")
    def validate_retention(self) -> "AuditIntegritySettings":
        """아카이브 임계치가 보관 기간보다 작은지 검증."""
        if self.archive_threshold_days > self.retention_days:
            raise ValueError(
                f"archive_threshold_days ({self.archive_threshold_days}) must be less than "
                f"retention_days ({self.retention_days})"
            )
        return self

    @model_validator(mode="after")
    def validate_health_thresholds(self) -> "AuditIntegritySettings":
        """Health score 임계값 순서 검증: healthy > warning > critical."""
        if self.health_healthy_threshold <= self.health_warning_threshold:
            raise ValueError(
                f"health_healthy_threshold ({self.health_healthy_threshold}) must be greater than "
                f"health_warning_threshold ({self.health_warning_threshold})"
            )
        if self.health_warning_threshold <= self.health_critical_threshold:
            raise ValueError(
                f"health_warning_threshold ({self.health_warning_threshold}) must be greater than "
                f"health_critical_threshold ({self.health_critical_threshold})"
            )
        return self

    @model_validator(mode="after")
    def validate_cross_cluster_ttl(self) -> "AuditIntegritySettings":
        """Cross cluster TTL 순서 검증: global >= local."""
        if self.cross_cluster_global_ttl_days < self.cross_cluster_local_ttl_days:
            raise ValueError(
                f"cross_cluster_global_ttl_days ({self.cross_cluster_global_ttl_days}) must be >= "
                f"cross_cluster_local_ttl_days ({self.cross_cluster_local_ttl_days})"
            )
        return self


# ==========================================================================
# Singleton 관리
# ==========================================================================
_audit_integrity_settings: AuditIntegritySettings | None = None


def get_audit_integrity_settings() -> AuditIntegritySettings:
    """Get cached AuditIntegritySettings instance."""
    global _audit_integrity_settings
    if _audit_integrity_settings is None:
        _audit_integrity_settings = AuditIntegritySettings()
    return _audit_integrity_settings


def reset_audit_integrity_settings() -> None:
    """Reset cached settings (for testing)."""
    global _audit_integrity_settings
    _audit_integrity_settings = None
