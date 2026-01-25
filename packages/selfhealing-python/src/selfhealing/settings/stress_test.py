"""
Stress Test Settings - Pydantic v2.

Pool 스트레스 테스트 관련 설정.
테스트 환경에서 DB Connection Pool 고갈 시뮬레이션에 사용됩니다.

Source:
- services/stress_test_service.py

Environment Variables:
    SELFHEALING_STRESS_TEST_DEFAULT_LOCK_TIMEOUT_MS=1
    SELFHEALING_STRESS_TEST_MAX_BURST_DURATION_SECONDS=30
    SELFHEALING_STRESS_TEST_MAX_CONCURRENT_LOCKS=100
"""

import logging
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class StressTestSettings(BaseSettings):
    """
    스트레스 테스트 설정.

    Pool 고갈, Lock Contention 등 스트레스 테스트에 사용되는 설정값입니다.
    테스트 전용이며, 프로덕션에서는 사용되지 않습니다.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_STRESS_TEST_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Lock Timeout Settings (from stress_test_service.py line 565)
    # ==========================================================================
    default_lock_timeout_ms: int = Field(
        default=1,
        ge=1,
        le=10000,
        description="Lock 획득 시 기본 타임아웃 (ms). 최소 1ms.",
    )

    # ==========================================================================
    # Burst Failure Settings (from stress_test_service.py lines 566-567)
    # ==========================================================================
    max_burst_duration_seconds: int = Field(
        default=30,
        ge=1,
        le=300,
        description="Controlled Burst Failure 최대 지속 시간 (초)",
    )
    max_concurrent_locks: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="동시 Lock 시도 최대 수",
    )

    # ==========================================================================
    # Connection Leak Simulation (from stress_test_service.py)
    # ==========================================================================
    default_leak_hold_seconds: int = Field(
        default=30,
        ge=1,
        le=300,
        description="Connection Leak 시뮬레이션 시 연결 유지 시간 (초)",
    )

    # ==========================================================================
    # Sleep Intervals
    # ==========================================================================
    inter_request_sleep_ms: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="Burst 테스트 시 요청 간 대기 시간 (ms)",
    )

    @field_validator("max_burst_duration_seconds")
    @classmethod
    def validate_burst_duration(cls, v: int) -> int:
        """Burst duration이 너무 길면 경고."""
        if v > 60:
            logger.warning(
                f"[StressTestSettings] High burst_duration={v}s, "
                "consider using <= 60s for safety"
            )
        return v


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: Optional[StressTestSettings] = None


def get_stress_test_settings() -> StressTestSettings:
    """
    캐시된 StressTestSettings 인스턴스 반환.

    Returns:
        StressTestSettings: 싱글톤 인스턴스
    """
    global _settings
    if _settings is None:
        _settings = StressTestSettings()
    return _settings


def reset_stress_test_settings() -> None:
    """
    캐시된 설정 초기화 (테스트용).

    환경 변수 변경 후 설정을 다시 로드하려면 이 함수를 호출하세요.
    """
    global _settings
    _settings = None
