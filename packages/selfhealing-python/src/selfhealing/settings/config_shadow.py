"""
Config Shadow Evaluation Settings.

Shadow Evaluation 및 Shadow Gate 관련 설정.

Environment Variables:
    SELFHEALING_SHADOW_GATE_ENABLED=true
    SELFHEALING_SHADOW_REQUIRE_EVALUATION=false
    SELFHEALING_SHADOW_DEFAULT_TIME_WINDOW_HOURS=336
    SELFHEALING_SHADOW_MIN_CONFIDENCE=0.3
    SELFHEALING_SHADOW_BYPASS_MIN_REASON_LENGTH=10
    SELFHEALING_SHADOW_EVALUATION_TTL_HOURS=1.0
    SELFHEALING_SHADOW_BLOCK_ON_LOW_CONFIDENCE=false
    SELFHEALING_SHADOW_LIVE_EVALUATION_ENABLED=false
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigShadowSettings(BaseSettings):
    """Config Shadow Evaluation 설정."""

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_SHADOW_",
        env_file=None,
        extra="ignore",
    )

    gate_enabled: bool = Field(
        default=True,
        description="Shadow Gate 활성화 여부",
    )
    require_evaluation: bool = Field(
        default=False,
        description=(
            "True: Shadow Evaluation 없이 start_rollout() 불가. "
            "False: 평가 없으면 경고만 (기본값)"
        ),
    )
    default_time_window_hours: int = Field(
        default=336,
        ge=24,
        le=720,
        description="기본 분석 시간 범위 (336 = 14일)",
    )
    min_confidence: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="이 미만의 confidence에서는 경고 추가",
    )
    bypass_min_reason_length: int = Field(
        default=10,
        ge=5,
        le=500,
        description="bypass_shadow_reason 최소 길이",
    )
    evaluation_ttl_hours: float = Field(
        default=1.0,
        ge=0.25,
        le=24.0,
        description="평가 결과 유효 시간 (기본 1시간). 초과 시 재평가 필요.",
    )
    block_on_low_confidence: bool = Field(
        default=False,
        description=(
            "True: confidence < min_confidence 시 start_rollout() 차단. "
            "False: 경고만 발행 (기본값). 시스템 성숙 후 True로 전환."
        ),
    )
    confidence_graduation_enabled: bool = Field(
        default=False,
        description="Low confidence 시 자동 데이터 수집 및 재평가 활성화",
    )
    confidence_graduation_target_events: int = Field(
        default=50,
        ge=20,
        le=500,
        description="재평가 트리거를 위한 최소 이벤트 수",
    )
    live_evaluation_enabled: bool = Field(
        default=False,
        description=(
            "promote() 시 Live Canary Evaluation 활성화 여부. "
            "TimeSeriesMetricsProvider 구현체가 등록된 후 True로 전환."
        ),
    )


def get_config_shadow_settings() -> "ConfigShadowSettings":
    from selfhealing.settings.root import get_config

    return get_config().adapters.config_shadow

def reset_config_shadow_settings() -> None:
    from selfhealing.settings.root import get_config

    try:
        del get_config().adapters.__dict__["config_shadow"]
    except KeyError:
        pass
