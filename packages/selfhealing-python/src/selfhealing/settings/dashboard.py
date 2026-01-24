"""
Dashboard Settings - Pydantic v2.

대시보드 캐시 TTL 및 관련 설정입니다.

Replaces:
- services/dashboard_service.py:CACHE_TTL_SECONDS, CACHE_TTL_STATUS, CACHE_TTL_ACTIVITY
- services/namespace_emergency/tracker.py:CACHE_TTL_SECONDS
- services/namespace_emergency/health_penalty.py:_cache_ttl_seconds

Environment Variables:
    SELFHEALING_DASHBOARD_CACHE_TTL_SECONDS=30
    SELFHEALING_DASHBOARD_CACHE_TTL_STATUS=15
    SELFHEALING_DASHBOARD_CACHE_TTL_ACTIVITY=60

Reference:
- docs/self_healing/middleware_system/92_CONFIG_IMPLEMENTATION_GUIDE.md (Week 4 [18])
- docs/self_healing/middleware_system/91_CONFIG_INVENTORY.md §3.1
"""

import logging
from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class DashboardSettings(BaseSettings):
    """
    대시보드 캐시 및 표시 설정.

    캐시 TTL 전략:
    - cache_ttl_seconds: 대시보드 기본 데이터 (30초)
    - cache_ttl_status: 상태 카운트 (15초, 더 자주 갱신)
    - cache_ttl_activity: 활동 통계 (60초, 덜 자주 갱신)
    - tracker_cache_ttl: Emergency Tracker 캐시 (30초)
    - health_penalty_cache_ttl: Health Penalty 캐시 (5초, 빠른 반응)
    - stale_threshold_minutes: 데이터 오래됨 임계치 (30분)
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_DASHBOARD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Dashboard Service Cache TTLs - from dashboard_service.py
    # ==========================================================================
    cache_ttl_seconds: int = Field(
        default=30,
        ge=5,
        le=300,
        description="대시보드 기본 데이터 캐시 TTL (초)",
    )

    cache_ttl_status: int = Field(
        default=15,
        ge=5,
        le=120,
        description="상태 카운트 캐시 TTL (초). 자주 변경되는 데이터용.",
    )

    cache_ttl_activity: int = Field(
        default=60,
        ge=10,
        le=600,
        description="활동 통계 캐시 TTL (초). 덜 변경되는 데이터용.",
    )

    # ==========================================================================
    # Tracker Cache TTL - from namespace_emergency/tracker.py
    # ==========================================================================
    tracker_cache_ttl: float = Field(
        default=30.0,
        ge=5.0,
        le=300.0,
        description="Emergency Tracker 캐시 TTL (초)",
    )

    # ==========================================================================
    # Health Penalty Cache TTL - from namespace_emergency/health_penalty.py
    # ==========================================================================
    health_penalty_cache_ttl: float = Field(
        default=5.0,
        ge=1.0,
        le=60.0,
        description="Health Penalty 캐시 TTL (초). 빠른 반응 필요.",
    )

    # ==========================================================================
    # Recovery Dashboard - from recovery_dashboard.py
    # ==========================================================================
    stale_threshold_minutes: int = Field(
        default=30,
        ge=5,
        le=120,
        description="데이터 오래됨 임계치 (분)",
    )

    max_regional_status: int = Field(
        default=5,
        ge=1,
        le=20,
        description="표시할 최대 리전 상태 수",
    )

    # ==========================================================================
    # Cache Key Prefix
    # ==========================================================================
    cache_prefix: str = Field(
        default="selfhealing:dashboard:",
        description="대시보드 캐시 키 접두사",
    )

    @field_validator("cache_prefix")
    @classmethod
    def validate_cache_prefix(cls, v: str) -> str:
        """캐시 접두사가 콜론으로 끝나는지 확인."""
        if not v.endswith(":"):
            return f"{v}:"
        return v


# ==========================================================================
# Singleton 관리
# ==========================================================================
_dashboard_settings: Optional[DashboardSettings] = None


def get_dashboard_settings() -> DashboardSettings:
    """Get cached DashboardSettings instance."""
    global _dashboard_settings
    if _dashboard_settings is None:
        _dashboard_settings = DashboardSettings()
    return _dashboard_settings


def reset_dashboard_settings() -> None:
    """Reset cached settings (for testing)."""
    global _dashboard_settings
    _dashboard_settings = None
