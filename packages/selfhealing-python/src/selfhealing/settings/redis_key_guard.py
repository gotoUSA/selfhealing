"""
Redis Key Guard Settings - Pydantic v2.

Single Source of Truth for Redis key priority eviction configuration.

Replaces:
- services/coordination/redis_key_guard.py:RedisKeyPriorityEviction memory thresholds

Environment Variables:
    SELFHEALING_REDIS_GUARD_MEMORY_WARNING_THRESHOLD=80.0
    SELFHEALING_REDIS_GUARD_MEMORY_CRITICAL_THRESHOLD=90.0
    ... etc

Reference:
- docs/self_healing/middleware_system/92_CONFIG_IMPLEMENTATION_GUIDE.md
- docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#11.1
"""

import structlog

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class RedisKeyGuardSettings(BaseSettings):
    """
    Redis Key Priority Eviction configuration with validation.

    Redis maxmemory 상황에서 핵심 거버넌스 키(P0)를 보호합니다.

    All defaults match:
    - services/coordination/redis_key_guard.py:RedisKeyPriorityEviction
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_REDIS_GUARD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Memory Threshold Settings
    # ==========================================================================
    memory_warning_threshold: float = Field(
        default=80.0,
        ge=50.0,
        le=95.0,
        description="메모리 경고 임계값 (%). 이 값 이상이면 경고",
    )
    memory_critical_threshold: float = Field(
        default=90.0,
        ge=60.0,
        le=99.0,
        description="메모리 위험 임계값 (%). 이 값 이상이면 긴급 정리",
    )

    # ==========================================================================
    # Eviction Settings
    # ==========================================================================
    target_free_percent: float = Field(
        default=20.0,
        ge=5.0,
        le=50.0,
        description="긴급 정리 시 목표 여유 메모리 비율 (%)",
    )

    # ==========================================================================
    # TTL Settings for Volatile Keys
    # ==========================================================================
    cache_ttl_seconds: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="캐시 키 기본 TTL (초). 1시간",
    )
    metrics_realtime_ttl_seconds: int = Field(
        default=600,
        ge=60,
        le=3600,
        description="실시간 메트릭 키 TTL (초). 10분",
    )
    metrics_aggregate_ttl_seconds: int = Field(
        default=7200,
        ge=600,
        le=86400,
        description="집계 메트릭 키 TTL (초). 2시간",
    )
    audit_event_ttl_seconds: int = Field(
        default=604800,
        ge=86400,
        le=2592000,
        description="감사 이벤트 키 TTL (초). 7일",
    )
    temp_key_ttl_seconds: int = Field(
        default=300,
        ge=60,
        le=3600,
        description="임시 키 TTL (초). 5분",
    )

    @field_validator("memory_critical_threshold")
    @classmethod
    def validate_critical_gt_warning(cls, v: float, info) -> float:
        """Ensure critical threshold > warning threshold."""
        # info.data는 이미 검증된 필드들을 포함
        warning = info.data.get("memory_warning_threshold", 80.0)
        if v <= warning:
            raise ValueError(
                f"memory_critical_threshold ({v}) must be > "
                f"memory_warning_threshold ({warning})"
            )
        return v


# =============================================================================
# Singleton Pattern (cached settings)
# =============================================================================

_settings: RedisKeyGuardSettings | None = None


def get_redis_key_guard_settings() -> RedisKeyGuardSettings:
    """
    Get cached RedisKeyGuardSettings instance.

    Returns:
        RedisKeyGuardSettings: Singleton instance
    """
    global _settings
    if _settings is None:
        _settings = RedisKeyGuardSettings()
    return _settings


def reset_redis_key_guard_settings() -> None:
    """
    Reset cached settings (for testing).

    Call this after modifying environment variables to reload settings.
    """
    global _settings
    _settings = None
