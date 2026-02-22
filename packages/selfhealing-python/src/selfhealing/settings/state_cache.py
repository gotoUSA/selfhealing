"""
CBStateCache Settings - Pydantic v2.

Circuit Breaker 상태 캐시 설정.
TTL 및 Jitter 범위를 환경변수로 설정 가능.

Environment Variables:
    SELFHEALING_STATE_CACHE_BASE_TTL=5.0
    SELFHEALING_STATE_CACHE_JITTER_RANGE=0.5
"""

import structlog
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class StateCacheSettings(BaseSettings):
    """
    CBStateCache 설정.

    TTL 기반 로컬 캐싱으로 네트워크 호출 최소화.
    Polling Jitter로 Thundering Herd 방지.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_STATE_CACHE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # TTL 설정
    # ==========================================================================
    base_ttl: float = Field(
        default=5.0,
        ge=0.1,
        le=300.0,
        description="기본 캐시 TTL (초). 이 시간이 지나면 캐시 무효화.",
    )

    # ==========================================================================
    # Jitter 설정
    # ==========================================================================
    jitter_range: float = Field(
        default=0.5,
        ge=0.0,
        le=10.0,
        description="랜덤 지터 범위 (초). TTL에 ±jitter_range 만큼 랜덤 적용.",
    )

    @model_validator(mode="after")
    def validate_jitter(self) -> "StateCacheSettings":
        """jitter_range가 base_ttl보다 크면 안됨."""
        if self.jitter_range > self.base_ttl:
            raise ValueError(
                f"jitter_range ({self.jitter_range}) should not exceed "
                f"base_ttl ({self.base_ttl})"
            )
        return self


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: StateCacheSettings | None = None


def get_state_cache_settings() -> StateCacheSettings:
    """
    캐시된 StateCacheSettings 인스턴스 반환.

    Returns:
        StateCacheSettings: 싱글톤 인스턴스
    """
    global _settings
    if _settings is None:
        _settings = StateCacheSettings()
        logger.debug(
            "[StateCacheSettings] Loaded: "  # noqa: G004
            f"base_ttl={_settings.base_ttl}s, "
            f"jitter_range=±{_settings.jitter_range}s"
        )
    return _settings


def reset_state_cache_settings() -> None:
    """
    캐시된 설정 초기화 (테스트용).
    """
    global _settings
    _settings = None
    logger.debug("state_cache_settings.reset")
