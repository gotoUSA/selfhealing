"""
Namespace Emergency Settings - Pydantic v2.

리전별 독립적인 Emergency 상태 관리 및 연쇄 장애 감지 설정.

Source:
- services/namespace_emergency/cascade_detector.py
- services/namespace_emergency/tracker.py
- services/namespace_emergency/escalation_audit.py

Environment Variables:
    SELFHEALING_NAMESPACE_EMERGENCY_ESCALATION_THRESHOLD=2
    SELFHEALING_NAMESPACE_EMERGENCY_CASCADE_WINDOW_MINUTES=30
    SELFHEALING_NAMESPACE_EMERGENCY_EXPIRY_HOURS=8
    SELFHEALING_NAMESPACE_EMERGENCY_CACHE_TTL_SECONDS=30.0
    SELFHEALING_NAMESPACE_EMERGENCY_MAX_BUFFER_SIZE=1000
"""

import structlog
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class NamespaceEmergencySettings(BaseSettings):
    """
    네임스페이스 Emergency 설정.

    다중 리전 연쇄 장애 감지, 상태 추적, 감사 추적 설정을 정의합니다.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_NAMESPACE_EMERGENCY_",
        env_file=None,
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Cascade Detection (from cascade_detector.py lines 44-47)
    # ==========================================================================
    escalation_threshold: int = Field(
        default=2,
        ge=1,
        le=10,
        description="GLOBAL 격상 임계값: N개 이상 리전이 STRICT면 cascade",
    )
    cascade_window_minutes: int = Field(
        default=30,
        ge=5,
        le=120,
        description="Cascade 판단 시간 윈도우 (분)",
    )

    # ==========================================================================
    # Tracker Settings (from tracker.py lines 48-51)
    # ==========================================================================
    expiry_hours: int = Field(
        default=8,
        ge=1,
        le=72,
        description="Emergency 상태 기본 만료 시간",
    )
    cache_ttl_seconds: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        description="로컬 캐시 TTL (초)",
    )

    # ==========================================================================
    # Audit Trail (from escalation_audit.py line 203)
    # ==========================================================================
    max_buffer_size: int = Field(
        default=1000,
        ge=100,
        le=100000,
        description="메모리 버퍼 최대 크기 (감사 이벤트 수)",
    )

    @field_validator("escalation_threshold")
    @classmethod
    def validate_escalation_threshold(cls, v: int) -> int:
        """escalation_threshold가 너무 작으면 경고."""
        if v < 2:
            logger.warning(
                "namespace_emergency_settings.low_consider_using_avoid",
                setting_value=v,
            )
        return v


def get_namespace_emergency_settings() -> "NamespaceEmergencySettings":
    from selfhealing.settings.root import get_config

    return get_config().multi_region.namespace_emergency

def reset_namespace_emergency_settings() -> None:
    from selfhealing.settings.root import get_config

    try:
        del get_config().multi_region.__dict__["namespace_emergency"]
    except KeyError:
        pass
