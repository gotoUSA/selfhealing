"""
Correlation Engine Orchestrator Settings — 엔진 전체 ON/OFF, 분석 주기, 연동 플래그 관리.

Co-occurrence Tracker, DAG Builder 등 서브 모듈별 설정은
기존 ``settings.correlation.CorrelationSettings`` 에서 관리되며,
이 파일에서는 오케스트레이터(서비스 수준) 설정만 정의한다.

Environment Variables:
    SELFHEALING_CORRELATION_ENABLED=true
    SELFHEALING_CORRELATION_ANALYSIS_INTERVAL_SECONDS=60.0
    SELFHEALING_CORRELATION_WEIGHT_TOPOLOGY=0.35
    SELFHEALING_CORRELATION_WEIGHT_TEMPORAL=0.25
    SELFHEALING_CORRELATION_WEIGHT_BLAST_RADIUS=0.25
    SELFHEALING_CORRELATION_WEIGHT_HISTORICAL=0.15
    SELFHEALING_CORRELATION_LEARNING_INTEGRATION_ENABLED=true
    SELFHEALING_CORRELATION_POSTMORTEM_INTEGRATION_ENABLED=true
    SELFHEALING_CORRELATION_STATE_PERSISTENCE_ENABLED=true
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CorrelationEngineSettings(BaseSettings):
    """Metric Correlation Engine 오케스트레이터 설정.

    환경변수 prefix: SELFHEALING_CORRELATION_
    예: SELFHEALING_CORRELATION_ENABLED=true
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CORRELATION_",
        env_file=None,
        extra="ignore",
        validate_default=True,
    )

    # ── 전체 ON/OFF ──
    enabled: bool = Field(
        default=True,
        description="Correlation Engine 활성화 여부",
    )

    # ── 주기적 분석 틱 ──
    analysis_interval_seconds: float = Field(
        default=60.0,
        ge=10.0,
        le=600.0,
        description="주기적 분석 틱 간격 (초)",
    )

    # ── Root Cause Ranker 가중치 ──
    weight_topology: float = Field(default=0.35, ge=0.0, le=1.0)
    weight_temporal: float = Field(default=0.25, ge=0.0, le=1.0)
    weight_blast_radius: float = Field(default=0.25, ge=0.0, le=1.0)
    weight_historical: float = Field(default=0.15, ge=0.0, le=1.0)

    # ── 연동 플래그 ──
    learning_integration_enabled: bool = Field(
        default=True,
        description="LearningService 패턴 축적 연동 활성화",
    )
    postmortem_integration_enabled: bool = Field(
        default=True,
        description="Postmortem 자동 타임라인 주입 활성화",
    )
    state_persistence_enabled: bool = Field(
        default=True,
        description="StateBackend 영속화 활성화 (Cold Start 방지)",
    )


def get_correlation_engine_settings() -> "CorrelationEngineSettings":
    from selfhealing.settings.root import get_config

    return get_config().obs.correlation_engine


def reset_correlation_engine_settings() -> None:
    from selfhealing.settings.root import get_config

    try:
        del get_config().obs.__dict__["correlation_engine"]
    except KeyError:
        pass
