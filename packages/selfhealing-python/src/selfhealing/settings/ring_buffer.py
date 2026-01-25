"""
Ring Buffer Settings - Pydantic v2.

Shadow Logging을 위한 Ring Buffer 설정.
비침투 원칙에 따라 DROP_OLDEST가 기본값이며, 메인 애플리케이션 성능에 영향을 주지 않습니다.

Source:
- audit/ring_buffer.py

Environment Variables:
    SELFHEALING_RING_BUFFER_CAPACITY=10000
    SELFHEALING_RING_BUFFER_BATCH_MAX_SIZE=100
    SELFHEALING_RING_BUFFER_STRATEGY=drop_oldest
"""

import logging
from typing import Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class RingBufferSettings(BaseSettings):
    """
    Ring Buffer 설정.

    Shadow Logging을 위한 비침투 버퍼 설정을 정의합니다.
    메인 애플리케이션을 절대 블로킹하지 않습니다.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_RING_BUFFER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Buffer Settings (from ring_buffer.py line 67)
    # ==========================================================================
    capacity: int = Field(
        default=10000,
        ge=100,
        le=1000000,
        description="Ring Buffer 최대 용량",
    )

    # ==========================================================================
    # Batch Settings (from ring_buffer.py - get_batch default)
    # ==========================================================================
    batch_max_size: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="배치 처리 시 최대 항목 수",
    )

    # ==========================================================================
    # Strategy Settings (from ring_buffer.py BackpressureStrategy)
    # ==========================================================================
    strategy: Literal["drop_oldest", "drop_newest"] = Field(
        default="drop_oldest",
        description="배압 전략. drop_oldest (권장: 비침투) 또는 drop_newest.",
    )

    @field_validator("capacity")
    @classmethod
    def validate_capacity(cls, v: int) -> int:
        """capacity가 너무 크면 경고."""
        if v > 100000:
            logger.warning(
                f"[RingBufferSettings] High capacity={v}, "
                "consider using <= 100000 for memory efficiency"
            )
        return v


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: Optional[RingBufferSettings] = None


def get_ring_buffer_settings() -> RingBufferSettings:
    """
    캐시된 RingBufferSettings 인스턴스 반환.

    Returns:
        RingBufferSettings: 싱글톤 인스턴스
    """
    global _settings
    if _settings is None:
        _settings = RingBufferSettings()
    return _settings


def reset_ring_buffer_settings() -> None:
    """
    캐시된 설정 초기화 (테스트용).

    환경 변수 변경 후 설정을 다시 로드하려면 이 함수를 호출하세요.
    """
    global _settings
    _settings = None
