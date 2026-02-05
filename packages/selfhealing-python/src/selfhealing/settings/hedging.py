"""
Hedging Settings - Pydantic v2 기반 설정.

환경변수로 헷징 전략의 기본 설정을 구성할 수 있습니다.

Environment Variables:
    SELFHEALING_HEDGING_ENABLED=true
    SELFHEALING_HEDGING_DEFAULT_MODE=delayed
    SELFHEALING_HEDGING_DEFAULT_TIMEOUT=5.0
    SELFHEALING_HEDGING_DEFAULT_DELAY=0.1
    SELFHEALING_HEDGING_MAX_CANDIDATES=3
"""

from __future__ import annotations

import logging
import threading

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class HedgingSettings(BaseSettings):
    """
    헷징 설정.

    환경변수로 설정을 구성할 수 있습니다.
    접두사: SELFHEALING_HEDGING_
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_HEDGING_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Global
    # ==========================================================================
    enabled: bool = Field(
        default=True,
        description="헷징 활성화 여부",
    )

    # ==========================================================================
    # 기본 설정
    # ==========================================================================
    default_mode: str = Field(
        default="delayed",
        description="기본 헷징 모드 (immediate, delayed, adaptive)",
    )

    default_timeout: float = Field(
        default=5.0,
        ge=0.1,
        le=60.0,
        description="기본 타임아웃 (초)",
    )

    default_delay: float = Field(
        default=0.1,
        ge=0.0,
        le=10.0,
        description="DELAYED 모드 기본 대기 시간 (초)",
    )

    max_candidates: int = Field(
        default=3,
        ge=1,
        le=10,
        description="최대 동시 실행 후보 수",
    )

    # ==========================================================================
    # 스레드 풀
    # ==========================================================================
    executor_max_workers: int = Field(
        default=10,
        ge=1,
        le=50,
        description="헷징 스레드 풀 최대 워커 수",
    )

    # ==========================================================================
    # Backpressure 연동
    # ==========================================================================
    disable_on_load_level: str = Field(
        default="high",
        description="이 부하 레벨 이상에서 헷징 비활성화 (none, low, medium, high, critical)",
    )

    delay_multiplier_on_medium: float = Field(
        default=2.0,
        ge=1.0,
        le=10.0,
        description="MEDIUM 레벨에서 delay 배율",
    )

    delay_multiplier_on_high: float = Field(
        default=5.0,
        ge=1.0,
        le=20.0,
        description="HIGH 레벨에서 delay 배율",
    )

    # ==========================================================================
    # 결과 정합성 검증
    # ==========================================================================
    validation_enabled: bool = Field(
        default=True,
        description="결과 정합성 검증 활성화 여부",
    )

    validation_sample_rate: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="검증 샘플링 비율 (0.0~1.0)",
    )

    skip_validation_on_high_load: bool = Field(
        default=True,
        description="HIGH/CRITICAL 부하 시 검증 생략",
    )


# =============================================================================
# Singleton
# =============================================================================

_settings: HedgingSettings | None = None
_settings_lock = threading.Lock()


def get_hedging_settings() -> HedgingSettings:
    """HedgingSettings 싱글톤 반환."""
    global _settings
    if _settings is None:
        with _settings_lock:
            if _settings is None:
                _settings = HedgingSettings()
    return _settings


def reset_hedging_settings() -> None:
    """싱글톤 초기화 (테스트용)."""
    global _settings
    with _settings_lock:
        _settings = None
