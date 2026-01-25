"""
Chaos Safety Caps Settings - Pydantic v2.

카오스 실험 안전 메커니즘의 Hard Caps 설정.
실험 구성에서 상한선으로 적용되며, 사용자가 더 큰 값을 지정해도 제한됩니다.

Source:
- services/chaos/constants.py (ExperimentHardCaps)

Environment Variables:
    SELFHEALING_CHAOS_SAFETY_CAPS_DISK_IO_MAX_LATENCY_MS=2000
    SELFHEALING_CHAOS_SAFETY_CAPS_DISK_IO_MAX_FAILURE_RATE=0.30
    SELFHEALING_CHAOS_SAFETY_CAPS_REPLAY_FLOOD_MAX_ENTRIES=5000
    ...
"""

import logging
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class ChaosSafetyCapsSettings(BaseSettings):
    """
    카오스 실험 안전 Hard Caps 설정.

    각 카오스 실험 유형별 최대 허용 범위를 정의합니다.
    Blast Radius 제한을 위해 사용됩니다.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CHAOS_SAFETY_CAPS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # SimulatedDiskIOExperiment (from constants.py lines 31-34)
    # ==========================================================================
    disk_io_max_latency_ms: int = Field(
        default=2000,
        ge=100,
        le=10000,
        description="디스크 I/O 최대 지연 시간 (ms)",
    )
    disk_io_max_failure_rate: float = Field(
        default=0.30,
        ge=0.01,
        le=1.0,
        description="디스크 I/O 최대 실패율 (0.0-1.0)",
    )

    # ==========================================================================
    # ReplayFloodExperiment (from constants.py lines 37-40)
    # ==========================================================================
    replay_flood_max_entries: int = Field(
        default=5000,
        ge=100,
        le=100000,
        description="Replay Flood 최대 생성 엔트리 수",
    )
    replay_flood_max_rate: int = Field(
        default=500,
        ge=10,
        le=10000,
        description="Replay Flood 최대 초당 생성 속도",
    )

    # ==========================================================================
    # ClockSkewExperiment (from constants.py line 43)
    # ==========================================================================
    clock_skew_max_seconds: int = Field(
        default=86400,
        ge=1,
        le=604800,
        description="Clock Skew 최대 오차 (초). 기본 1일.",
    )

    # ==========================================================================
    # NetworkBlackholeExperiment (from constants.py line 46)
    # ==========================================================================
    blackhole_max_duration_seconds: int = Field(
        default=300,
        ge=10,
        le=1800,
        description="Network Blackhole 최대 지속 시간 (초)",
    )

    # ==========================================================================
    # PoolExhaustionExperiment (from constants.py lines 52-55)
    # ==========================================================================
    pool_exhaustion_max_duration_seconds: int = Field(
        default=120,
        ge=10,
        le=600,
        description="Connection Pool 고갈 최대 지속 시간 (초)",
    )
    pool_exhaustion_max_percentage: float = Field(
        default=0.50,
        ge=0.1,
        le=1.0,
        description="Connection Pool 고갈 최대 비율 (0.0-1.0)",
    )

    # ==========================================================================
    # SimulatedTLSFailureExperiment (from constants.py lines 58-61)
    # ==========================================================================
    tls_failure_max_duration_seconds: int = Field(
        default=180,
        ge=10,
        le=600,
        description="TLS 실패 최대 지속 시간 (초)",
    )
    tls_failure_max_rate: float = Field(
        default=0.25,
        ge=0.01,
        le=1.0,
        description="TLS 실패 최대 발생률 (0.0-1.0)",
    )

    @field_validator("disk_io_max_failure_rate", "pool_exhaustion_max_percentage", "tls_failure_max_rate")
    @classmethod
    def validate_rate(cls, v: float) -> float:
        """비율이 합리적인지 확인."""
        if v > 0.5:
            logger.warning(
                f"[ChaosSafetyCapsSettings] High rate={v}, "
                "consider using <= 0.5 for safety"
            )
        return v


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: Optional[ChaosSafetyCapsSettings] = None


def get_chaos_safety_caps_settings() -> ChaosSafetyCapsSettings:
    """
    캐시된 ChaosSafetyCapsSettings 인스턴스 반환.

    Returns:
        ChaosSafetyCapsSettings: 싱글톤 인스턴스
    """
    global _settings
    if _settings is None:
        _settings = ChaosSafetyCapsSettings()
    return _settings


def reset_chaos_safety_caps_settings() -> None:
    """
    캐시된 설정 초기화 (테스트용).

    환경 변수 변경 후 설정을 다시 로드하려면 이 함수를 호출하세요.
    """
    global _settings
    _settings = None
