"""
Throttle Settings - Pydantic v2.

Netflix Gradient 기반 적응형 스로틀 설정입니다.

Replaces:
- services/throttle/config.py:ThrottleConfig

Environment Variables:
    SELFHEALING_THROTTLE_INITIAL_LIMIT=100
    SELFHEALING_THROTTLE_MIN_LIMIT=10
    SELFHEALING_THROTTLE_MAX_LIMIT=500

Reference:
- docs/self_healing/middleware_system/92_CONFIG_IMPLEMENTATION_GUIDE.md (Week 2 [10])
- docs/self_healing/middleware_system/91_CONFIG_INVENTORY.md §7.1
"""

import logging
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class ThrottleSettings(BaseSettings):
    """
    Netflix Gradient 기반 적응형 스로틀 설정.

    RTT 그래디언트를 기반으로 동적으로 요청 제한을 조절합니다.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_THROTTLE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Basic Rate Limiting (from throttle/config.py)
    # ==========================================================================
    initial_limit: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="초기 요청 제한 (윈도우당 요청 수)",
    )

    window_seconds: int = Field(
        default=60,
        ge=1,
        le=3600,
        description="윈도우 크기 (초)",
    )

    # ==========================================================================
    # Adaptive Throttling Limits
    # ==========================================================================
    min_limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="최소 제한 (이 값 아래로 내려가지 않음)",
    )

    max_limit: int = Field(
        default=500,
        ge=100,
        le=100000,
        description="최대 제한 (이 값 위로 올라가지 않음)",
    )

    # ==========================================================================
    # Gradient Calculation Settings
    # ==========================================================================
    sample_interval_ms: int = Field(
        default=500,
        ge=50,
        le=5000,
        description="RTT 샘플링 간격 (ms)",
    )

    smoothing_factor: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="지수 평활화 계수 (0-1, 높을수록 반응적)",
    )

    # ==========================================================================
    # Adjustment Rates
    # ==========================================================================
    decrease_ratio: float = Field(
        default=0.9,
        ge=0.5,
        le=0.99,
        description="RTT 증가 시 곱할 비율 (감소)",
    )

    increase_step: int = Field(
        default=1,
        ge=1,
        le=100,
        description="RTT 감소 시 더할 값 (증가)",
    )

    # ==========================================================================
    # SLA Thresholds (ms)
    # ==========================================================================
    sla_warning_ms: int = Field(
        default=200,
        ge=10,
        le=5000,
        description="스로틀링 시작 RTT 임계값 (ms)",
    )

    sla_critical_ms: int = Field(
        default=500,
        ge=50,
        le=10000,
        description="공격적 스로틀링 시작 RTT 임계값 (ms)",
    )

    # ==========================================================================
    # Emergency Mode
    # ==========================================================================
    emergency_limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="비상 모드 시 윈도우당 요청 제한",
    )

    # ==========================================================================
    # Redis Key Prefix
    # ==========================================================================
    key_prefix: str = Field(
        default="selfhealing:throttle",
        description="Redis 키 접두사",
    )

    @field_validator("max_limit")
    @classmethod
    def validate_max_limit(cls, v: int, info) -> int:
        """max_limit이 min_limit보다 커야 함."""
        # 다른 필드 접근이 어려우므로 기본값과 비교
        if v < 10:  # min_limit 기본값
            logger.warning(
                f"[SafeDefault] max_limit={v} is very low, may cause issues"
            )
        return v

    @field_validator("sla_critical_ms")
    @classmethod
    def validate_sla_critical(cls, v: int, info) -> int:
        """sla_critical_ms가 sla_warning_ms보다 커야 함."""
        # 기본값 200과 비교
        if v < 200:
            logger.warning(
                f"[SafeDefault] sla_critical_ms={v} is lower than typical warning threshold"
            )
        return v


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: Optional[ThrottleSettings] = None


def get_throttle_settings() -> ThrottleSettings:
    """Get cached ThrottleSettings instance."""
    global _settings
    if _settings is None:
        _settings = ThrottleSettings()
    return _settings


def reset_throttle_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
