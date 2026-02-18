"""
Pool Monitor Settings - Pydantic v2.

DB Connection Pool 모니터링 및 누수 감지 설정.

Source:
- core/pool_monitor.py
- core/connection_health.py

Environment Variables:
    SELFHEALING_POOL_MONITOR_WARNING_THRESHOLD=70.0
    SELFHEALING_POOL_MONITOR_CRITICAL_THRESHOLD=90.0
    SELFHEALING_POOL_MONITOR_LEAK_THRESHOLD_SECONDS=300.0
    SELFHEALING_POOL_MONITOR_MAX_HISTORY=5000
    SELFHEALING_POOL_MONITOR_CONNECTION_FAILURE_THRESHOLD=3
"""

import logging

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class PoolMonitorSettings(BaseSettings):
    """
    Connection Pool 모니터링 설정.

    Pool 사용률 임계값, 누수 감지 기준 등을 정의합니다.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_POOL_MONITOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Health Thresholds (from core/pool_monitor.py lines 102-104)
    # ==========================================================================
    warning_threshold: float = Field(
        default=70.0,
        ge=10.0,
        le=95.0,
        description="Pool 사용률 경고 임계값 (%)",
    )
    critical_threshold: float = Field(
        default=90.0,
        ge=50.0,
        le=100.0,
        description="Pool 사용률 위험 임계값 (%)",
    )

    # ==========================================================================
    # Leak Detection (from core/pool_monitor.py line 105)
    # ==========================================================================
    leak_threshold_seconds: float = Field(
        default=300.0,
        ge=30.0,
        le=3600.0,
        description="Connection 누수 의심 기준 시간 (초). 기본 5분.",
    )

    # ==========================================================================
    # History Settings (from core/pool_monitor.py line 116)
    # ==========================================================================
    max_history: int = Field(
        default=5000,
        ge=10,
        le=10000,
        description="트렌드 분석용 통계 히스토리 최대 개수. " "72시간 커버를 위해 5,000 기본값 (60초 간격 기준).",
    )

    # ==========================================================================
    # Connection Health Monitor (from core/connection_health.py line 108)
    # ==========================================================================
    connection_failure_threshold: int = Field(
        default=3,
        ge=1,
        le=20,
        description="UNHEALTHY 판정을 위한 연속 실패 횟수",
    )

    @model_validator(mode="after")
    def validate_thresholds(self) -> "PoolMonitorSettings":
        """warning_threshold가 critical_threshold보다 작은지 확인."""
        if self.warning_threshold >= self.critical_threshold:
            raise ValueError(
                f"warning_threshold ({self.warning_threshold}) must be less than "
                f"critical_threshold ({self.critical_threshold})"
            )
        return self


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: PoolMonitorSettings | None = None


def get_pool_monitor_settings() -> PoolMonitorSettings:
    """
    캐시된 PoolMonitorSettings 인스턴스 반환.

    Returns:
        PoolMonitorSettings: 싱글톤 인스턴스
    """
    global _settings
    if _settings is None:
        _settings = PoolMonitorSettings()
    return _settings


def reset_pool_monitor_settings() -> None:
    """
    캐시된 설정 초기화 (테스트용).

    환경 변수 변경 후 설정을 다시 로드하려면 이 함수를 호출하세요.
    """
    global _settings
    _settings = None
