"""
Hash Chain Settings - Pydantic v2.

해시 체인 무결성 관련 분산 락 및 감사 추적 설정입니다.

Source:
- audit/hash_chain_safety.py:AtomicMergeSwap.DEFAULT_TIMEOUT_SECONDS (300초)
- audit/hash_chain_safety.py:ShardedDateLock.DEFAULT_TIMEOUT_SECONDS (120초)
- audit/hash_chain_safety.py:IntegrityAuditTrail.MAX_REDIS_ENTRIES (1000개)

Environment Variables:
    SELFHEALING_HASH_CHAIN_MERGE_SWAP_TIMEOUT_SECONDS=300
    SELFHEALING_HASH_CHAIN_DATE_LOCK_TIMEOUT_SECONDS=120
    SELFHEALING_HASH_CHAIN_INTEGRITY_TRAIL_MAX_REDIS_ENTRIES=1000
    SELFHEALING_HASH_CHAIN_DATE_LOCK_BLOCKING_TIMEOUT_SECONDS=5.0
"""

import structlog
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class HashChainSettings(BaseSettings):
    """
    해시 체인 무결성 관련 설정.

    분산 락:
    - merge_swap_timeout_seconds: 전역 조정 락 타임아웃 (5분)
    - date_lock_timeout_seconds: 날짜별 락 타임아웃 (2분)

    감사 추적:
    - integrity_trail_max_redis_entries: Redis 저장 무결성 이벤트 최대 수
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_HASH_CHAIN_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # AtomicMergeSwap - 전역 조정 락 (from hash_chain_safety.py)
    # ==========================================================================
    merge_swap_timeout_seconds: int = Field(
        default=300,
        ge=60,
        le=600,
        description="AtomicMergeSwap 전역 락 자동 만료 시간 (초). 5분 기본.",
    )

    merge_swap_blocking_timeout_seconds: float = Field(
        default=10.0,
        ge=1.0,
        le=60.0,
        description="AtomicMergeSwap 락 획득 대기 최대 시간 (초).",
    )

    # ==========================================================================
    # ShardedDateLock - 날짜별 분산 락 (from hash_chain_safety.py)
    # ==========================================================================
    date_lock_timeout_seconds: int = Field(
        default=120,
        ge=30,
        le=300,
        description="ShardedDateLock 날짜별 락 자동 만료 시간 (초). 2분 기본.",
    )

    date_lock_blocking_timeout_seconds: float = Field(
        default=5.0,
        ge=0.5,
        le=30.0,
        description="ShardedDateLock 락 획득 대기 최대 시간 (초). 짧게 설정하여 다른 날짜로 빠르게 전환.",
    )

    # ==========================================================================
    # IntegrityAuditTrail - 무결성 감사 추적 (from hash_chain_safety.py)
    # ==========================================================================
    integrity_trail_max_redis_entries: int = Field(
        default=1000,
        ge=100,
        le=10000,
        description="Redis에 저장할 무결성 감사 이벤트 최대 수. FIFO 방식으로 제한.",
    )

    @model_validator(mode="after")
    def validate_lock_timeouts(self) -> "HashChainSettings":
        """락 타임아웃이 블로킹 타임아웃보다 커야 함."""
        if self.merge_swap_timeout_seconds <= self.merge_swap_blocking_timeout_seconds:
            logger.warning(
                "hash_chain_settings.greater_than",
                self=self.merge_swap_timeout_seconds,
                self_1=self.merge_swap_blocking_timeout_seconds,
            )
        if self.date_lock_timeout_seconds <= self.date_lock_blocking_timeout_seconds:
            logger.warning(
                "hash_chain_settings.greater_than",
                self=self.date_lock_timeout_seconds,
                self_1=self.date_lock_blocking_timeout_seconds,
            )
        return self


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: HashChainSettings | None = None


def get_hash_chain_settings() -> HashChainSettings:
    """
    캐시된 HashChainSettings 인스턴스 반환.

    Returns:
        HashChainSettings: 싱글톤 인스턴스
    """
    global _settings
    if _settings is None:
        _settings = HashChainSettings()
    return _settings


def reset_hash_chain_settings() -> None:
    """
    캐시된 Settings 초기화 (테스트용).
    """
    global _settings
    _settings = None
