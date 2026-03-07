"""
Correlation Engine Settings — DAG 구축 및 동시발생 분석 관련 설정.

트리거 이벤트 기준 시간 윈도우(lookback/lookahead),
Debounce cooldown, DAG 크기 제한, Co-occurrence Tracker 설정 등을 환경변수로 제어한다.

Environment Variables:
    SELFHEALING_CORRELATION_LOOKBACK_SECONDS=300
    SELFHEALING_CORRELATION_LOOKAHEAD_SECONDS=60
    SELFHEALING_CORRELATION_COOLDOWN_SECONDS=60.0
    SELFHEALING_CORRELATION_MAX_EVENTS_PER_DAG=200
    SELFHEALING_CORRELATION_MIN_CONFIDENCE=0.4
    SELFHEALING_CORRELATION_MAX_GRAPH_DEPTH=3
    SELFHEALING_CORRELATION_WINDOW_SECONDS=300
    SELFHEALING_CORRELATION_ZSCORE_THRESHOLD=2.5
    SELFHEALING_CORRELATION_MIN_CO_OCCURRENCES=3
    SELFHEALING_CORRELATION_MAX_TRACKED_PAIRS=1000
    SELFHEALING_CORRELATION_ANALYSIS_INTERVAL=60
    SELFHEALING_CORRELATION_MAX_EVENT_BUFFER=500
    SELFHEALING_CORRELATION_COUNT_HISTORY_SIZE=100
    SELFHEALING_CORRELATION_SIMULTANEOUS_THRESHOLD_SECONDS=0.001
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CorrelationSettings(BaseSettings):
    """Correlation Engine DAG 구축 및 Co-occurrence 분석 설정."""

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CORRELATION_",
        env_file=None,
        extra="ignore",
        validate_default=True,
    )

    # ── DAG 구축 설정 ──

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

    # ── Co-occurrence Tracker 설정 ──

    window_seconds: float = Field(
        default=300.0,
        ge=30.0,
        le=3600.0,
        description="동시발생 판단 시간 윈도우 (초)",
    )

    zscore_threshold: float = Field(
        default=2.5,
        ge=1.0,
        le=5.0,
        description="ZScore 이상치 임계값",
    )

    min_co_occurrences: int = Field(
        default=3,
        ge=1,
        le=100,
        description="최소 동시발생 횟수 (이하 무시)",
    )

    max_tracked_pairs: int = Field(
        default=1000,
        ge=10,
        le=10000,
        description="추적 가능한 최대 이벤트 쌍 수",
    )

    analysis_interval: float = Field(
        default=60.0,
        ge=10.0,
        le=600.0,
        description="분석 틱 주기 (초)",
    )

    max_event_buffer: int = Field(
        default=500,
        ge=50,
        le=10000,
        description="이벤트 타입별 최대 타임스탬프 버퍼",
    )

    count_history_size: int = Field(
        default=100,
        ge=10,
        le=1000,
        description="쌍별 카운트 히스토리 크기",
    )

    simultaneous_threshold_seconds: float = Field(
        default=0.001,
        ge=0.0,
        le=1.0,
        description="동시 도착 판정 임계값 (초). 이 미만의 시간 간격은 방향성 판단에서 제외",
    )


def get_correlation_settings() -> "CorrelationSettings":
    from selfhealing.settings.root import get_config

    return get_config().obs.correlation

def reset_correlation_settings() -> None:
    from selfhealing.settings.root import get_config

    try:
        del get_config().obs.__dict__["correlation"]
    except KeyError:
        pass
