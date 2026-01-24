"""
Distributed Lock Settings - Pydantic v2.

분산 환경에서의 락(Lock) 관련 설정입니다.

Replaces:
- services/coordination/distributed_recovery_lock.py:DEFAULT_LOCK_TIMEOUT
- adapters/cache/redis_adapter.py:RedisDistributedLock 설정

Environment Variables:
    SELFHEALING_DISTRIBUTED_LOCK_TIMEOUT_MINUTES=30
    SELFHEALING_DISTRIBUTED_LOCK_RETRY_INTERVAL_SECONDS=0.1

Reference:
- docs/self_healing/middleware_system/92_CONFIG_IMPLEMENTATION_GUIDE.md (Week 3 [17])
- docs/self_healing/middleware_system/91_CONFIG_INVENTORY.md §6.31, §13.4
- docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#8.3
"""

import logging
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class DistributedLockSettings(BaseSettings):
    """
    분산 락 설정.

    Redis 기반 분산 락의 타임아웃 및 재시도 정책을 관리합니다.

    Features:
    - 락 자동 만료로 좀비 락 방지
    - 재시도 간격 및 최대 횟수 설정
    - 연장(Extend) 설정
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_DISTRIBUTED_LOCK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Lock Timeout (from distributed_recovery_lock.py#L92)
    # ==========================================================================
    timeout_minutes: int = Field(
        default=30,
        ge=1,
        le=120,
        description="락 자동 만료 시간 (분). 복구 최대 예상 시간 기준.",
    )

    # ==========================================================================
    # Retry Settings
    # ==========================================================================
    retry_interval_seconds: float = Field(
        default=0.1,
        ge=0.01,
        le=5.0,
        description="락 획득 재시도 간격 (초)",
    )

    max_retry_attempts: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="락 획득 최대 재시도 횟수",
    )

    # ==========================================================================
    # Extend Settings
    # ==========================================================================
    extend_interval_seconds: int = Field(
        default=60,
        ge=10,
        le=300,
        description="락 연장 확인 간격 (초)",
    )

    auto_extend_enabled: bool = Field(
        default=True,
        description="자동 락 연장 활성화",
    )

    # ==========================================================================
    # Key Prefix (IMMUTABLE - 참조용으로만 포함)
    # ==========================================================================
    key_prefix: str = Field(
        default="selfhealing:",
        description="Redis 락 키 접두사 (변경 비권장)",
    )

    @field_validator("timeout_minutes")
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        """타임아웃이 너무 길면 경고."""
        if v > 60:
            logger.warning(
                f"[DistributedLock] timeout_minutes={v}분은 긴 시간입니다. "
                "복구가 실패할 경우 오랜 시간 락이 유지됩니다."
            )
        return v

    @field_validator("retry_interval_seconds")
    @classmethod
    def validate_retry_interval(cls, v: float) -> float:
        """재시도 간격이 너무 짧으면 경고."""
        if v < 0.05:
            logger.warning(
                f"[DistributedLock] retry_interval_seconds={v}초는 매우 짧습니다. "
                "Redis 부하가 증가할 수 있습니다."
            )
        return v

    def get_timeout_seconds(self) -> int:
        """타임아웃을 초 단위로 반환."""
        return self.timeout_minutes * 60

    def get_timeout_ms(self) -> int:
        """타임아웃을 밀리초 단위로 반환."""
        return self.timeout_minutes * 60 * 1000


# Singleton instance (cached)
_settings: Optional[DistributedLockSettings] = None


def get_distributed_lock_settings() -> DistributedLockSettings:
    """Get cached DistributedLockSettings instance."""
    global _settings
    if _settings is None:
        _settings = DistributedLockSettings()
    return _settings


def reset_distributed_lock_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
