"""
Correlation Engine Settings — DAG 구축 관련 설정.

트리거 이벤트 기준 시간 윈도우(lookback/lookahead),
Debounce cooldown, DAG 크기 제한 등을 환경변수로 제어한다.

Environment Variables:
    SELFHEALING_CORRELATION_LOOKBACK_SECONDS=300
    SELFHEALING_CORRELATION_LOOKAHEAD_SECONDS=60
    SELFHEALING_CORRELATION_COOLDOWN_SECONDS=60.0
    SELFHEALING_CORRELATION_MAX_EVENTS_PER_DAG=200
    SELFHEALING_CORRELATION_MIN_CONFIDENCE=0.4
    SELFHEALING_CORRELATION_MAX_GRAPH_DEPTH=3
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CorrelationSettings(BaseSettings):
    """Correlation Engine DAG 구축 설정."""

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CORRELATION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    lookback_seconds: int = Field(
        default=300,
        ge=30,
        le=900,
        description="트리거 이벤트 기준 과거 캡처 범위 (초)",
    )

    lookahead_seconds: int = Field(
        default=60,
        ge=10,
        le=300,
        description="트리거 이벤트 기준 미래 캡처 범위 (초)",
    )

    cooldown_seconds: float = Field(
        default=60.0,
        ge=10.0,
        le=600.0,
        description="동일 namespace에서 DAG 재생성 방지 쿨다운 (초)",
    )

    max_events_per_dag: int = Field(
        default=200,
        ge=10,
        le=1000,
        description="단일 DAG에 포함 가능한 최대 이벤트 수",
    )

    min_confidence: float = Field(
        default=0.4,
        ge=0.1,
        le=1.0,
        description="이 값 미만의 엣지는 DAG에서 제거",
    )

    max_graph_depth: int = Field(
        default=3,
        ge=1,
        le=10,
        description="DAG 최대 그래프 깊이 (OOM 방지)",
    )


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: CorrelationSettings | None = None


def get_correlation_settings() -> CorrelationSettings:
    """캐시된 CorrelationSettings 인스턴스 반환."""
    global _settings
    if _settings is None:
        _settings = CorrelationSettings()
    return _settings


def reset_correlation_settings() -> None:
    """테스트용: 캐시된 설정 초기화."""
    global _settings
    _settings = None
