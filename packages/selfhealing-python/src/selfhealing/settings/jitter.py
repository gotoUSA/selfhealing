"""
Jitter Settings - Pydantic v2.

Thundering Herd 방지를 위한 Jitter 설정.
분산 환경에서 동시 시작되는 인스턴스들의 DB 쿼리를 시간적으로 분산시킵니다.

Source:
- utils/jitter.py

Environment Variables:
    SELFHEALING_JITTER_MAX_DELAY_SECONDS=60.0
    SELFHEALING_JITTER_MIN_DELAY_SECONDS=0.0
"""

import logging
from typing import Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class JitterSettings(BaseSettings):
    """
    Jitter 설정.

    Thundering Herd 방지를 위한 무작위 지연 설정을 정의합니다.
    환경별 권장 설정:
    - 단일 서버: 0초 (비활성화)
    - K8s 10 Pods: 30초
    - K8s 100+ Pods: 60초
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_JITTER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Delay Settings (from utils/jitter.py lines 27-28, 75-76, 99-100, 122-123)
    # ==========================================================================
    max_delay_seconds: float = Field(
        default=60.0,
        ge=0.0,
        le=300.0,
        description="최대 지연 시간 (초)",
    )
    min_delay_seconds: float = Field(
        default=0.0,
        ge=0.0,
        le=60.0,
        description="최소 지연 시간 (초)",
    )

    # ==========================================================================
    # Startup Jitter (for AppConfig.ready())
    # ==========================================================================
    startup_max_delay_seconds: float = Field(
        default=30.0,
        ge=0.0,
        le=120.0,
        description="시작 시 최대 지연 시간 (초)",
    )

    # ==========================================================================
    # Feature Toggle
    # ==========================================================================
    enabled: bool = Field(
        default=True,
        description="Jitter 활성화 여부. False면 지연 없음.",
    )

    @model_validator(mode="after")
    def validate_delay_range(self) -> "JitterSettings":
        """min_delay가 max_delay보다 작은지 확인."""
        if self.min_delay_seconds > self.max_delay_seconds:
            raise ValueError(
                f"min_delay_seconds ({self.min_delay_seconds}) cannot be greater than "
                f"max_delay_seconds ({self.max_delay_seconds})"
            )
        return self


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: Optional[JitterSettings] = None


def get_jitter_settings() -> JitterSettings:
    """
    캐시된 JitterSettings 인스턴스 반환.

    Returns:
        JitterSettings: 싱글톤 인스턴스
    """
    global _settings
    if _settings is None:
        _settings = JitterSettings()
    return _settings


def reset_jitter_settings() -> None:
    """
    캐시된 설정 초기화 (테스트용).

    환경 변수 변경 후 설정을 다시 로드하려면 이 함수를 호출하세요.
    """
    global _settings
    _settings = None
