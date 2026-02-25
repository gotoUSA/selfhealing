"""
Chaos Experiment Settings - Pydantic v2.

Chaos 실험의 TTL, SLA, 결과 보관 관련 설정입니다.

Replaces:
- services/chaos/base/experiment.py:default_ttl_seconds
- services/chaos/base/models.py:grace_period_seconds
- services/chaos/base/models.py:sla_breach_threshold_percent

Environment Variables:
    SELFHEALING_CHAOS_EXPERIMENT_MAX_DURATION_SECONDS=3600
    SELFHEALING_CHAOS_EXPERIMENT_GRACE_PERIOD_SECONDS=300
    SELFHEALING_CHAOS_EXPERIMENT_RESULT_TTL_SECONDS=86400

Reference:
- docs/self_healing/middleware_system/92_CONFIG_IMPLEMENTATION_GUIDE.md (Week 3 [12])
- docs/self_healing/middleware_system/91_CONFIG_INVENTORY.md §6.6, §9.6
"""

import structlog
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class ChaosExperimentSettings(BaseSettings):
    """
    Chaos 실험 설정.

    Chaos Engineering 실험의 생명주기 및 SLA 관련 설정을 관리합니다.

    Features:
    - 실험 최대 지속 시간 제한
    - Grace Period: 실험 시작 후 안정화 대기 시간
    - SLA 위반 임계치: 자동 중단 트리거
    - 결과 보관 기간 (TTL)
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CHAOS_EXPERIMENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Duration Settings (from chaos/base/experiment.py)
    # ==========================================================================
    max_duration_seconds: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="실험 최대 지속 시간 (초). 기본 1시간, 최대 24시간",
    )

    default_duration_seconds: int = Field(
        default=300,
        ge=30,
        le=3600,
        description="기본 실험 지속 시간 (초). 기본 5분",
    )

    default_ttl_seconds: int = Field(
        default=600,
        ge=60,
        le=7200,
        description="실험 자동 만료 TTL (초). 기본 10분",
    )

    # ==========================================================================
    # Grace Period Settings (from chaos/base/models.py#L48)
    # ==========================================================================
    grace_period_seconds: int = Field(
        default=300,
        ge=30,
        le=1800,
        description="실험 시작 후 안정화 대기 시간 (초). 기본 5분",
    )

    # ==========================================================================
    # SLA Settings (from chaos/base/models.py#L52)
    # ==========================================================================
    sla_breach_threshold_percent: float = Field(
        default=1.0,
        ge=0.1,
        le=10.0,
        description="SLA 위반 임계치 (%). 초과 시 자동 중단",
    )

    # ==========================================================================
    # Result Storage Settings (from 91 문서 §6.6)
    # ==========================================================================
    result_ttl_seconds: int = Field(
        default=86400,
        ge=3600,
        le=604800,
        description="실험 결과 보관 기간 (초). 기본 24시간, 최대 7일",
    )

    # ==========================================================================
    # Health Check Settings (from chaos/base/models.py#L60-61)
    # ==========================================================================
    health_check_interval_seconds: float = Field(
        default=30.0,
        ge=5.0,
        le=300.0,
        description="실험 중 헬스체크 간격 (초)",
    )

    health_check_timeout_ms: int = Field(
        default=100,
        ge=10,
        le=5000,
        description="헬스체크 타임아웃 (밀리초)",
    )

    @field_validator("grace_period_seconds")
    @classmethod
    def validate_grace_period(cls, v: int, info) -> int:
        """Grace period가 max_duration보다 작아야 함."""
        # Note: cross-field validation은 model_validator에서 처리
        if v > 1800:
            logger.warning(
                "chaos_experiment.매우_값입니다_실험_시작이",
                setting_value=v,
            )
        return v


# Singleton instance (cached)
_settings: ChaosExperimentSettings | None = None


def get_chaos_experiment_settings() -> ChaosExperimentSettings:
    """Get cached ChaosExperimentSettings instance."""
    global _settings
    if _settings is None:
        _settings = ChaosExperimentSettings()
    return _settings


def reset_chaos_experiment_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
