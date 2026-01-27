"""
Recovery Circuit Breaker Settings - Pydantic v2.

Single Source of Truth for recovery circuit breaker configuration.

Replaces:
- services/coordination/recovery_circuit_breaker.py:RecoveryCircuitBreakerConfig

Environment Variables:
    SELFHEALING_RECOVERY_CB_ERROR_RATE_THRESHOLD=0.15
    SELFHEALING_RECOVERY_CB_SAMPLING_WINDOW_SECONDS=60
    SELFHEALING_RECOVERY_CB_MIN_SAMPLES=10
    ... etc

Reference:
- docs/self_healing/middleware_system/92_CONFIG_IMPLEMENTATION_GUIDE.md
- docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#8.1
"""

import logging

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class RecoveryCircuitBreakerSettings(BaseSettings):
    """
    Recovery Circuit Breaker configuration with validation.

    복구 진행 중 지표가 다시 악화되면 즉시 복구를 중단하고
    Emergency 상태로 재-에스컬레이션합니다.

    All defaults match:
    - services/coordination/recovery_circuit_breaker.py:RecoveryCircuitBreakerConfig
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_RECOVERY_CB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Error Rate Settings
    # ==========================================================================
    error_rate_threshold: float = Field(
        default=0.15,
        ge=0.01,
        le=1.0,
        description="에러율 임계값 (이 값 초과 시 트립). 15% = 0.15",
    )

    # ==========================================================================
    # Sampling Settings
    # ==========================================================================
    sampling_window_seconds: int = Field(
        default=60,
        ge=10,
        le=600,
        description="샘플링 윈도우 (초). 최근 N초 데이터로 판단",
    )
    min_samples: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="최소 샘플 수. 이 값 이상 수집 후 판단",
    )

    # ==========================================================================
    # Circuit State Settings
    # ==========================================================================
    open_duration_seconds: int = Field(
        default=300,
        ge=30,
        le=3600,
        description="차단 유지 시간 (초). 차단 후 N초간 유지",
    )
    half_open_max_requests: int = Field(
        default=5,
        ge=1,
        le=100,
        description="반개방 상태에서 허용 요청 수",
    )
    max_consecutive_trips: int = Field(
        default=3,
        ge=1,
        le=10,
        description="연속 트립 횟수. 이 값 초과 시 영구 중단",
    )

    # ==========================================================================
    # Re-escalation Settings
    # ==========================================================================
    re_escalation_enabled: bool = Field(
        default=True,
        description="트립 시 Emergency 레벨로 재-에스컬레이션 활성화",
    )
    re_escalation_level: str = Field(
        default="LEVEL_3",
        description="재-에스컬레이션 시 전환할 레벨",
    )

    @field_validator("error_rate_threshold")
    @classmethod
    def validate_error_rate_threshold(cls, v: float) -> float:
        """Validate error rate threshold is reasonable."""
        if v > 0.5:
            logger.warning(
                f"High error_rate_threshold={v}. "
                "Consider using <= 0.5 for effective protection"
            )
        return v

    @field_validator("re_escalation_level")
    @classmethod
    def validate_re_escalation_level(cls, v: str) -> str:
        """Validate re-escalation level is valid."""
        valid_levels = {"NORMAL", "LEVEL_1", "LEVEL_2", "LEVEL_3"}
        if v not in valid_levels:
            logger.warning(
                f"Unknown re_escalation_level={v}. " f"Valid levels: {valid_levels}"
            )
        return v


# =============================================================================
# Singleton Pattern (cached settings)
# =============================================================================

_settings: RecoveryCircuitBreakerSettings | None = None


def get_recovery_circuit_breaker_settings() -> RecoveryCircuitBreakerSettings:
    """
    Get cached RecoveryCircuitBreakerSettings instance.

    Returns:
        RecoveryCircuitBreakerSettings: Singleton instance
    """
    global _settings
    if _settings is None:
        _settings = RecoveryCircuitBreakerSettings()
    return _settings


def reset_recovery_circuit_breaker_settings() -> None:
    """
    Reset cached settings (for testing).

    Call this after modifying environment variables to reload settings.
    """
    global _settings
    _settings = None
