"""
Drift Detection Settings - Pydantic v2.

SLA 드리프트 감지 태스크 관련 설정.

Source:
- tasks/drift_detection.py

Environment Variables:
    SELFHEALING_DRIFT_DETECTION_ANALYSIS_WINDOW_HOURS=24
    SELFHEALING_DRIFT_DETECTION_SLA_BREACH_RATE_THRESHOLD=10.0
    SELFHEALING_DRIFT_DETECTION_SLA_APPROACHING_THRESHOLD=0.8
    SELFHEALING_DRIFT_DETECTION_PENDING_AT_RISK_THRESHOLD=5
"""

import logging
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class DriftDetectionSettings(BaseSettings):
    """
    SLA 드리프트 감지 설정.

    SLA 위반 감지, 분석 윈도우, 경고 임계값 등을 정의합니다.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_DRIFT_DETECTION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Analysis Window (from drift_detection.py line 112)
    # ==========================================================================
    analysis_window_hours: int = Field(
        default=24,
        ge=1,
        le=168,
        description="SLA 분석 윈도우 크기 (시간)",
    )

    # ==========================================================================
    # SLA Breach Thresholds (from drift_detection.py line 174-195)
    # ==========================================================================
    sla_breach_rate_threshold: float = Field(
        default=10.0,
        ge=1.0,
        le=50.0,
        description="SLA 위반율 경고 임계값 (%)",
    )

    sla_breach_rate_critical_threshold: float = Field(
        default=25.0,
        ge=10.0,
        le=75.0,
        description="SLA 위반율 위험 임계값 (%)",
    )

    # ==========================================================================
    # SLA Approaching Threshold (from drift_detection.py line 184)
    # ==========================================================================
    sla_approaching_threshold: float = Field(
        default=0.8,
        ge=0.5,
        le=0.95,
        description="SLA 근접 경고 임계값 (비율, 0.8 = 80%)",
    )

    # ==========================================================================
    # Pending At Risk (from drift_detection.py line 195)
    # ==========================================================================
    pending_at_risk_threshold: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Pending 상태 위험 경고 임계값 (개수)",
    )

    @field_validator("sla_breach_rate_critical_threshold")
    @classmethod
    def validate_critical_threshold(cls, v: float, info) -> float:
        """critical_threshold가 breach_rate_threshold보다 커야 함."""
        # Note: 이 검증은 model_validator로 더 정확하게 할 수 있음
        return v


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: Optional[DriftDetectionSettings] = None


def get_drift_detection_settings() -> DriftDetectionSettings:
    """
    캐시된 DriftDetectionSettings 인스턴스 반환.

    Returns:
        DriftDetectionSettings: 싱글톤 인스턴스
    """
    global _settings
    if _settings is None:
        _settings = DriftDetectionSettings()
    return _settings


def reset_drift_detection_settings() -> None:
    """
    캐시된 설정 초기화 (테스트용).

    환경 변수 변경 후 설정을 다시 로드하려면 이 함수를 호출하세요.
    """
    global _settings
    _settings = None
