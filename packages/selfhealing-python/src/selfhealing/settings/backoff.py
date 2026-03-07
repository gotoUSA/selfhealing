"""
Backoff Settings - Pydantic v2.

재시도 메커니즘의 Backoff 전략 설정.

Source:
- core/backoff.py

Environment Variables:
    SELFHEALING_BACKOFF_EXPONENTIAL_BASE_DELAY=1.0
    SELFHEALING_BACKOFF_EXPONENTIAL_MAX_DELAY=300.0
    SELFHEALING_BACKOFF_EXPONENTIAL_MULTIPLIER=2.0
    SELFHEALING_BACKOFF_EXPONENTIAL_JITTER_FACTOR=0.2
    ...
"""

import structlog
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class BackoffSettings(BaseSettings):
    """
    Backoff 전략 설정.

    Exponential, Linear, Constant, Decorrelated Jitter 전략의
    기본값을 정의합니다.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_BACKOFF_",
        env_file=None,
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Exponential Backoff (from core/backoff.py lines 44-49)
    # ==========================================================================
    exponential_base_delay: float = Field(
        default=1.0,
        ge=0.1,
        le=60.0,
        description="Exponential Backoff 기본 지연 시간 (초)",
    )
    exponential_max_delay: float = Field(
        default=300.0,
        ge=1.0,
        le=3600.0,
        description="Exponential Backoff 최대 지연 시간 (초)",
    )
    exponential_multiplier: float = Field(
        default=2.0,
        ge=1.1,
        le=10.0,
        description="Exponential Backoff 배수",
    )
    exponential_jitter_factor: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Exponential Backoff Jitter 비율 (0.0-1.0)",
    )

    # ==========================================================================
    # Linear Backoff (from core/backoff.py lines 74-80)
    # ==========================================================================
    linear_base_delay: float = Field(
        default=1.0,
        ge=0.1,
        le=60.0,
        description="Linear Backoff 기본 지연 시간 (초)",
    )
    linear_increment: float = Field(
        default=1.0,
        ge=0.1,
        le=60.0,
        description="Linear Backoff 증가분 (초)",
    )
    linear_max_delay: float = Field(
        default=60.0,
        ge=1.0,
        le=600.0,
        description="Linear Backoff 최대 지연 시간 (초)",
    )
    linear_jitter_factor: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Linear Backoff Jitter 비율",
    )

    # ==========================================================================
    # Constant Backoff (from core/backoff.py lines 106-109)
    # ==========================================================================
    constant_delay: float = Field(
        default=5.0,
        ge=0.1,
        le=300.0,
        description="Constant Backoff 고정 지연 시간 (초)",
    )
    constant_jitter_factor: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Constant Backoff Jitter 비율",
    )

    # ==========================================================================
    # Decorrelated Jitter Backoff (from core/backoff.py lines 126-128)
    # ==========================================================================
    decorrelated_base_delay: float = Field(
        default=1.0,
        ge=0.1,
        le=60.0,
        description="Decorrelated Jitter Backoff 기본 지연 시간 (초)",
    )
    decorrelated_max_delay: float = Field(
        default=300.0,
        ge=1.0,
        le=3600.0,
        description="Decorrelated Jitter Backoff 최대 지연 시간 (초)",
    )

    # ==========================================================================
    # Legacy Backoff (from core/backoff.py LegacyBackoffConfig)
    # ==========================================================================
    legacy_base: int = Field(
        default=4,
        ge=1,
        le=10,
        description="Legacy Backoff 지수 기반 (4^n)",
    )
    legacy_max_delay: int = Field(
        default=180,
        ge=1,
        le=3600,
        description="Legacy Backoff 최대 지연 시간 (초)",
    )
    legacy_jitter_percent: int = Field(
        default=25,
        ge=0,
        le=100,
        description="Legacy Backoff Jitter 퍼센트 (±%)",
    )
    legacy_min_delay: int = Field(
        default=1,
        ge=1,
        le=60,
        description="Legacy Backoff 최소 지연 시간 (초)",
    )

    @field_validator("exponential_max_delay")
    @classmethod
    def validate_exponential_max_delay(cls, v: float) -> float:
        """max_delay가 너무 크면 경고."""
        if v > 600:
            logger.warning(
                "backoff_settings.high_consider_using_responsiveness",
                setting_value=v,
            )
        return v


def get_backoff_settings() -> "BackoffSettings":
    from selfhealing.settings.root import get_config

    return get_config().core.backoff


def reset_backoff_settings() -> None:
    from selfhealing.settings.root import get_config

    try:
        del get_config().core.__dict__["backoff"]
    except KeyError:
        pass
