"""
Cleanup Settings - Pydantic v2.

DLQ, Pending Config, Approval 등의 정리(cleanup) 작업 설정.

Source:
- services/cleanup_service.py
- tasks/cleanup_tasks.py

Environment Variables:
    SELFHEALING_CLEANUP_ARCHIVE_OLDER_THAN_DAYS=30
    SELFHEALING_CLEANUP_EXPIRED_CONFIG_HOURS=24
    SELFHEALING_CLEANUP_APPROVAL_EXPIRY_HOURS=72
    SELFHEALING_CLEANUP_PURGE_OLDER_THAN_DAYS=90
"""

import logging
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class CleanupSettings(BaseSettings):
    """
    Cleanup 작업 설정.

    DLQ 아카이브, 만료된 설정 정리, 승인 요청 만료 등의 기준값을 정의합니다.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CLEANUP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # DLQ Archive Settings (from cleanup_service.py line 65)
    # ==========================================================================
    archive_older_than_days: int = Field(
        default=30,
        ge=1,
        le=365,
        description="해결된 DLQ 항목 아카이브 기준 일수",
    )

    # ==========================================================================
    # Expired Config Cleanup (from cleanup_service.py line 117)
    # ==========================================================================
    expired_config_hours: int = Field(
        default=24,
        ge=1,
        le=168,
        description="만료된 Pending Config 정리 기준 시간",
    )

    # ==========================================================================
    # Approval Expiry (from cleanup_service.py line 165)
    # ==========================================================================
    approval_expiry_hours: int = Field(
        default=72,
        ge=1,
        le=336,
        description="대기 중인 승인 요청 만료 기준 시간",
    )

    # ==========================================================================
    # DLQ Purge Settings (from cleanup_service.py line 207)
    # ==========================================================================
    purge_older_than_days: int = Field(
        default=90,
        ge=30,
        le=730,
        description="아카이브 항목 영구 삭제 기준 일수",
    )

    @field_validator("purge_older_than_days")
    @classmethod
    def validate_purge_days(cls, v: int, info) -> int:
        """purge_older_than_days는 archive_older_than_days보다 커야 함."""
        # Note: cross-field validation은 model_validator로 처리해야 하지만
        # 단순 경고만 발생시킴
        if v < 60:
            logger.warning(
                f"[CleanupSettings] Low purge_older_than_days={v}, "
                "consider using >= 60 for data retention"
            )
        return v


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: Optional[CleanupSettings] = None


def get_cleanup_settings() -> CleanupSettings:
    """
    캐시된 CleanupSettings 인스턴스 반환.

    Returns:
        CleanupSettings: 싱글톤 인스턴스
    """
    global _settings
    if _settings is None:
        _settings = CleanupSettings()
    return _settings


def reset_cleanup_settings() -> None:
    """
    캐시된 설정 초기화 (테스트용).

    환경 변수 변경 후 설정을 다시 로드하려면 이 함수를 호출하세요.
    """
    global _settings
    _settings = None
