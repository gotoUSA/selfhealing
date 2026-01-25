"""
Resilient Recorder Settings - Pydantic v2.

Single Source of Truth for resilient continuous audit recorder configuration.

Replaces:
- audit/resilient_recorder.py:ResilientRecorderConfig

Environment Variables:
    SELFHEALING_RESILIENT_RECORDER_BUFFER_CAPACITY=10000
    SELFHEALING_RESILIENT_RECORDER_FLUSH_INTERVAL=1.0
    SELFHEALING_RESILIENT_RECORDER_FLUSH_BATCH_SIZE=100
    ... etc

Reference:
- docs/self_healing/middleware_system/92_CONFIG_IMPLEMENTATION_GUIDE.md
"""

import logging
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class ResilientRecorderSettings(BaseSettings):
    """
    Resilient Continuous Audit Recorder configuration with validation.

    장애 허용 기능을 가진 연속 감사 기록기 설정입니다.

    Features:
    - RingBuffer: 비침투 Shadow Logging
    - CircuitBreaker: resilience.py 재사용
    - Self-Audit: 자체 상태 기록
    - SyslogFallback: 최후의 수단

    All defaults match:
    - audit/resilient_recorder.py:ResilientRecorderConfig
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_RESILIENT_RECORDER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Buffer Settings
    # ==========================================================================
    buffer_capacity: int = Field(
        default=10000,
        ge=100,
        le=1000000,
        description="RingBuffer 용량 (이벤트 수)",
    )
    backpressure_strategy: str = Field(
        default="DROP_OLDEST",
        description="버퍼 초과 시 전략: DROP_OLDEST, DROP_NEWEST, BLOCK",
    )

    # ==========================================================================
    # Background Worker Settings
    # ==========================================================================
    enable_background_flush: bool = Field(
        default=True,
        description="백그라운드 플러시 활성화",
    )
    flush_interval_seconds: float = Field(
        default=1.0,
        ge=0.1,
        le=60.0,
        description="플러시 간격 (초)",
    )
    flush_batch_size: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="플러시 배치 크기",
    )

    # ==========================================================================
    # Circuit Breaker Settings
    # ==========================================================================
    circuit_failure_threshold: int = Field(
        default=3,
        ge=1,
        le=100,
        description="Circuit Breaker 실패 임계값",
    )
    circuit_success_threshold: int = Field(
        default=2,
        ge=1,
        le=100,
        description="Circuit Breaker 성공 임계값 (반개방→폐쇄)",
    )
    circuit_timeout_seconds: float = Field(
        default=30.0,
        ge=1.0,
        le=600.0,
        description="Circuit Breaker 타임아웃 (초)",
    )

    # ==========================================================================
    # Fallback Settings
    # ==========================================================================
    fallback_file_path: Optional[str] = Field(
        default=None,
        description="폴백 파일 경로 (None이면 기본 위치 사용)",
    )
    enable_syslog_fallback: bool = Field(
        default=True,
        description="Syslog 폴백 활성화",
    )

    @field_validator("backpressure_strategy")
    @classmethod
    def validate_backpressure_strategy(cls, v: str) -> str:
        """Validate backpressure strategy is valid."""
        valid_strategies = {"DROP_OLDEST", "DROP_NEWEST", "BLOCK"}
        if v not in valid_strategies:
            raise ValueError(
                f"Invalid backpressure_strategy: {v}. "
                f"Valid options: {valid_strategies}"
            )
        return v

    @field_validator("circuit_failure_threshold")
    @classmethod
    def validate_circuit_failure_threshold(cls, v: int) -> int:
        """Warn if circuit failure threshold is very low."""
        if v < 2:
            logger.warning(
                f"Very low circuit_failure_threshold={v}. "
                "May cause frequent circuit opens on transient errors"
            )
        return v

    # ==========================================================================
    # In-Memory Audit Buffer - from audit/resilience/buffer.py
    # ==========================================================================
    memory_buffer_max_entries: int = Field(
        default=10000,
        ge=100,
        le=100000,
        description="WAL 실패 시 메모리 버퍼 최대 엔트리 수. 메모리 고갈 방지용.",
    )

    memory_buffer_flush_interval: float = Field(
        default=30.0,
        ge=5.0,
        le=300.0,
        description="메모리 버퍼 플러시 시도 간격 (초). 30초 기본.",
    )


# =============================================================================
# Singleton Pattern (cached settings)
# =============================================================================

_settings: Optional[ResilientRecorderSettings] = None


def get_resilient_recorder_settings() -> ResilientRecorderSettings:
    """
    Get cached ResilientRecorderSettings instance.

    Returns:
        ResilientRecorderSettings: Singleton instance
    """
    global _settings
    if _settings is None:
        _settings = ResilientRecorderSettings()
    return _settings


def reset_resilient_recorder_settings() -> None:
    """
    Reset cached settings (for testing).

    Call this after modifying environment variables to reload settings.
    """
    global _settings
    _settings = None
