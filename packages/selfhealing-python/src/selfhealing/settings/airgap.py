"""
Air-Gap Settings - Pydantic v2.

Air-Gap 저장소 관련 설정입니다.
비즈니스 DB와 Self-Healing 엔진 사이의 중간 저장소 역할을 합니다.

Replaces:
- adapters/airgap/redis_adapter.py:DEFAULT_TTL

Environment Variables:
    SELFHEALING_AIRGAP_REDIS_TTL=3600
    SELFHEALING_AIRGAP_KEY_PREFIX=sh:airgap:

Usage:
    from selfhealing.settings.airgap import get_airgap_settings
    settings = get_airgap_settings()
    ttl = settings.redis_ttl
"""

import logging

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class AirGapSettings(BaseSettings):
    """
    Air-Gap 저장소 설정.

    Air-Gap은 비즈니스 레이어와 Self-Healing 엔진 사이의 분리 계층입니다.
    비즈니스 DB 변경 시 Redis에 요약 상태를 기록하고,
    Self-Healing 엔진은 Redis에서만 상태를 조회합니다.

    Attributes:
        redis_ttl: Redis에 저장되는 Air-Gap 상태의 TTL (초)
        key_prefix: Redis 키 접두사
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_AIRGAP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Redis TTL - from adapters/airgap/redis_adapter.py
    # ==========================================================================
    redis_ttl: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="Redis에 저장되는 Air-Gap 상태의 TTL (초). 기본 1시간.",
    )

    # ==========================================================================
    # Key Prefix
    # ==========================================================================
    key_prefix: str = Field(
        default="sh:airgap:",
        description="Redis 키 접두사",
    )

    @field_validator("key_prefix")
    @classmethod
    def validate_key_prefix(cls, v: str) -> str:
        """키 접두사가 콜론으로 끝나는지 확인."""
        if not v.endswith(":"):
            return f"{v}:"
        return v


# ==========================================================================
# Singleton 관리
# ==========================================================================
_airgap_settings: AirGapSettings | None = None


def get_airgap_settings() -> AirGapSettings:
    """Get cached AirGapSettings instance."""
    global _airgap_settings
    if _airgap_settings is None:
        _airgap_settings = AirGapSettings()
    return _airgap_settings


def reset_airgap_settings() -> None:
    """Reset cached settings (for testing)."""
    global _airgap_settings
    _airgap_settings = None
