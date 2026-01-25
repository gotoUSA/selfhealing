"""
Canary Settings - Pydantic v2.

Canary Rollout 관련 설정.
설정 변경의 점진적 배포, 동시성 제어, 크로스 클러스터 알림 설정.

Source:
- services/canary/service.py
- services/canary/cross_cluster.py
- services/canary/locking.py

Environment Variables:
    SELFHEALING_CANARY_ROLLOUT_TTL_DAYS=7
    SELFHEALING_CANARY_LOCK_TIMEOUT_MINUTES=30
    SELFHEALING_CANARY_CROSS_CLUSTER_TIMEOUT_SECONDS=10
    SELFHEALING_CANARY_DEFAULT_EXPIRY_HOURS=24
"""

import logging
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class CanarySettings(BaseSettings):
    """
    Canary Rollout 설정.

    점진적 배포, 락 타임아웃, 크로스 클러스터 통신 설정을 정의합니다.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CANARY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Rollout Settings (from service.py line 103)
    # ==========================================================================
    rollout_ttl_days: int = Field(
        default=7,
        ge=1,
        le=90,
        description="롤아웃 데이터 보관 기간 (일)",
    )

    # ==========================================================================
    # Locking Settings (from locking.py line 81)
    # ==========================================================================
    lock_timeout_minutes: int = Field(
        default=30,
        ge=5,
        le=120,
        description="Config Lock 자동 만료 시간 (분)",
    )

    # ==========================================================================
    # Cross-Cluster Settings (from cross_cluster.py line 388)
    # ==========================================================================
    cross_cluster_timeout_seconds: int = Field(
        default=10,
        ge=1,
        le=60,
        description="크로스 클러스터 API 호출 타임아웃 (초)",
    )

    # ==========================================================================
    # Propagation Request Settings (from cross_cluster.py line 599)
    # ==========================================================================
    default_expiry_hours: int = Field(
        default=24,
        ge=1,
        le=168,
        description="전파 요청 기본 만료 시간 (시간)",
    )

    # ==========================================================================
    # API View Settings (from views/canary.py - Phase 3 리팩토링)
    # ==========================================================================
    default_completed_rollouts_limit: int = Field(
        default=20,
        ge=5,
        le=100,
        description="완료된 롤아웃 목록 조회 기본 limit",
    )

    default_history_limit: int = Field(
        default=20,
        ge=5,
        le=100,
        description="롤아웃 히스토리 조회 기본 limit",
    )

    @field_validator("lock_timeout_minutes")
    @classmethod
    def validate_lock_timeout(cls, v: int) -> int:
        """lock_timeout이 너무 길면 경고."""
        if v > 60:
            logger.warning(
                f"[CanarySettings] High lock_timeout_minutes={v}, "
                "consider using <= 60 for responsiveness"
            )
        return v


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: Optional[CanarySettings] = None


def get_canary_settings() -> CanarySettings:
    """
    캐시된 CanarySettings 인스턴스 반환.

    Returns:
        CanarySettings: 싱글톤 인스턴스
    """
    global _settings
    if _settings is None:
        _settings = CanarySettings()
    return _settings


def reset_canary_settings() -> None:
    """
    캐시된 설정 초기화 (테스트용).

    환경 변수 변경 후 설정을 다시 로드하려면 이 함수를 호출하세요.
    """
    global _settings
    _settings = None
