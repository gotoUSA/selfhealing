"""
Precomputed Cache Settings - Pydantic v2.

L3 Observability 엔드포인트 성능 최적화를 위한 사전 계산 캐시 설정.

Source:
- services/precomputed_cache.py

Environment Variables:
    SELFHEALING_PRECOMPUTED_CACHE_L1_TTL_SECONDS=2.0
    SELFHEALING_PRECOMPUTED_CACHE_L2_TTL_SECONDS=15.0
    SELFHEALING_PRECOMPUTED_CACHE_REFRESH_INTERVAL_SECONDS=10.0
    SELFHEALING_PRECOMPUTED_CACHE_L1_MAXSIZE=100
"""

import structlog
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class PrecomputedCacheSettings(BaseSettings):
    """
    사전 계산 캐시 설정.

    Multi-tier 캐시 (L1 In-Process, L2 Redis) 설정을 정의합니다.
    V3 최적화: L3 오버헤드 50ms 이하 달성을 위한 설정입니다.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_PRECOMPUTED_CACHE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # L1 In-Process Cache (from precomputed_cache.py line 66-67)
    # ==========================================================================
    l1_ttl_seconds: float = Field(
        default=2.0,
        ge=0.1,
        le=60.0,
        description="L1 In-Process 캐시 TTL (초). 0ms 오버헤드.",
    )
    l1_maxsize: int = Field(
        default=100,
        ge=10,
        le=10000,
        description="L1 캐시 최대 항목 수",
    )

    # ==========================================================================
    # L2 Redis Cache (from precomputed_cache.py line 68)
    # ==========================================================================
    l2_ttl_seconds: float = Field(
        default=15.0,
        ge=1.0,
        le=300.0,
        description="L2 Redis 캐시 TTL (초). 1-5ms 오버헤드.",
    )

    # ==========================================================================
    # Background Refresh (from precomputed_cache.py line 69)
    # ==========================================================================
    refresh_interval_seconds: float = Field(
        default=10.0,
        ge=1.0,
        le=300.0,
        description="백그라운드 갱신 주기 (초). L2 TTL보다 작아야 함.",
    )

    @field_validator("refresh_interval_seconds")
    @classmethod
    def validate_refresh_interval(cls, v: float) -> float:
        """refresh_interval이 L2 TTL보다 작은지 확인."""
        # Note: cross-field validation은 model_validator로 처리해야 하지만
        # 단순 경고만 발생시킴
        if v > 15.0:
            logger.warning(
                "precomputed_cache_settings.cache_expire_before_refresh",
                v=v,
            )
        return v


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: PrecomputedCacheSettings | None = None


def get_precomputed_cache_settings() -> PrecomputedCacheSettings:
    """
    캐시된 PrecomputedCacheSettings 인스턴스 반환.

    Returns:
        PrecomputedCacheSettings: 싱글톤 인스턴스
    """
    global _settings
    if _settings is None:
        _settings = PrecomputedCacheSettings()
    return _settings


def reset_precomputed_cache_settings() -> None:
    """
    캐시된 설정 초기화 (테스트용).

    환경 변수 변경 후 설정을 다시 로드하려면 이 함수를 호출하세요.
    """
    global _settings
    _settings = None
