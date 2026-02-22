"""
SafetyBounds Settings - Pydantic v2.

자율 조정 안전 한계 설정.
파라미터별 min/max 범위 및 한 사이클당 최대 변경 비율을 환경변수로 설정 가능.

Environment Variables (각 파라미터별):
    SELFHEALING_BOUNDS_TIMEOUT_MS_MIN=100
    SELFHEALING_BOUNDS_TIMEOUT_MS_MAX=30000
    SELFHEALING_BOUNDS_TIMEOUT_MS_MAX_CHANGE=0.3
    ... (다른 파라미터도 동일 패턴)
"""

import structlog

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class ParameterBoundConfig(BaseModel):
    """개별 파라미터 한계 설정."""

    min_value: float = Field(description="최소 허용 값")
    max_value: float = Field(description="최대 허용 값")
    max_change_per_cycle: float = Field(
        ge=0.01,
        le=1.0,
        description="한 사이클당 최대 변경 비율 (0.3 = 30%)",
    )

    @model_validator(mode="after")
    def validate_bounds(self) -> "ParameterBoundConfig":
        """min < max 검증."""
        if self.min_value > self.max_value:
            raise ValueError(f"min_value ({self.min_value}) cannot be greater than " f"max_value ({self.max_value})")
        return self


class SafetyBoundsSettings(BaseSettings):
    """
    SafetyBounds 전체 설정.

    자율 조정이 위험한 범위로 벗어나지 않도록 보호하는 한계 설정.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_BOUNDS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
        env_nested_delimiter="__",
    )

    # ==========================================================================
    # timeout_ms 한계
    # ==========================================================================
    timeout_ms_min: float = Field(
        default=100,
        ge=10,
        description="타임아웃 최소값 (ms)",
    )
    timeout_ms_max: float = Field(
        default=30000,
        le=120000,
        description="타임아웃 최대값 (ms)",
    )
    timeout_ms_max_change: float = Field(
        default=0.3,
        ge=0.01,
        le=1.0,
        description="타임아웃 한 사이클당 최대 변경 비율",
    )

    # ==========================================================================
    # retry_count 한계
    # ==========================================================================
    retry_count_min: float = Field(
        default=0,
        ge=0,
        description="재시도 횟수 최소값",
    )
    retry_count_max: float = Field(
        default=10,
        le=20,
        description="재시도 횟수 최대값",
    )
    retry_count_max_change: float = Field(
        default=0.5,
        ge=0.01,
        le=1.0,
        description="재시도 횟수 한 사이클당 최대 변경 비율",
    )

    # ==========================================================================
    # circuit_breaker_threshold 한계
    # ==========================================================================
    circuit_breaker_threshold_min: float = Field(
        default=0.1,
        ge=0.01,
        le=0.5,
        description="서킷브레이커 임계값 최소값",
    )
    circuit_breaker_threshold_max: float = Field(
        default=0.9,
        ge=0.5,
        le=0.99,
        description="서킷브레이커 임계값 최대값",
    )
    circuit_breaker_threshold_max_change: float = Field(
        default=0.2,
        ge=0.01,
        le=1.0,
        description="서킷브레이커 임계값 한 사이클당 최대 변경 비율",
    )

    # ==========================================================================
    # jitter_range 한계
    # ==========================================================================
    jitter_range_min: float = Field(
        default=0.01,
        ge=0.001,
        description="지터 범위 최소값 (초)",
    )
    jitter_range_max: float = Field(
        default=1.0,
        le=5.0,
        description="지터 범위 최대값 (초)",
    )
    jitter_range_max_change: float = Field(
        default=0.5,
        ge=0.01,
        le=1.0,
        description="지터 범위 한 사이클당 최대 변경 비율",
    )

    # ==========================================================================
    # rate_limit_rps 한계
    # ==========================================================================
    rate_limit_rps_min: float = Field(
        default=10,
        ge=1,
        description="Rate Limit 최소값 (rps)",
    )
    rate_limit_rps_max: float = Field(
        default=10000,
        le=100000,
        description="Rate Limit 최대값 (rps)",
    )
    rate_limit_rps_max_change: float = Field(
        default=0.2,
        ge=0.01,
        le=1.0,
        description="Rate Limit 한 사이클당 최대 변경 비율",
    )

    # ==========================================================================
    # throttle_sla_warning_ms 한계
    # ==========================================================================
    throttle_sla_warning_ms_min: float = Field(
        default=50,
        ge=10,
        description="SLA Warning 임계값 최소값 (ms)",
    )
    throttle_sla_warning_ms_max: float = Field(
        default=2000,
        le=5000,
        description="SLA Warning 임계값 최대값 (ms)",
    )
    throttle_sla_warning_ms_max_change: float = Field(
        default=0.3,
        ge=0.01,
        le=1.0,
        description="SLA Warning 한 사이클당 최대 변경 비율",
    )

    # ==========================================================================
    # throttle_sla_critical_ms 한계
    # ==========================================================================
    throttle_sla_critical_ms_min: float = Field(
        default=100,
        ge=50,
        description="SLA Critical 임계값 최소값 (ms)",
    )
    throttle_sla_critical_ms_max: float = Field(
        default=5000,
        le=10000,
        description="SLA Critical 임계값 최대값 (ms)",
    )
    throttle_sla_critical_ms_max_change: float = Field(
        default=0.3,
        ge=0.01,
        le=1.0,
        description="SLA Critical 한 사이클당 최대 변경 비율",
    )

    # ==========================================================================
    # backoff_base_ms 한계
    # ==========================================================================
    backoff_base_ms_min: float = Field(
        default=10,
        ge=1,
        description="Backoff 기본값 최소 (ms)",
    )
    backoff_base_ms_max: float = Field(
        default=5000,
        le=30000,
        description="Backoff 기본값 최대 (ms)",
    )
    backoff_base_ms_max_change: float = Field(
        default=0.3,
        ge=0.01,
        le=1.0,
        description="Backoff 기본값 한 사이클당 최대 변경 비율",
    )

    # ==========================================================================
    # backoff_max_ms 한계
    # ==========================================================================
    backoff_max_ms_min: float = Field(
        default=1000,
        ge=100,
        description="Backoff 최대값 최소 (ms)",
    )
    backoff_max_ms_max: float = Field(
        default=60000,
        le=300000,
        description="Backoff 최대값 최대 (ms)",
    )
    backoff_max_ms_max_change: float = Field(
        default=0.3,
        ge=0.01,
        le=1.0,
        description="Backoff 최대값 한 사이클당 최대 변경 비율",
    )

    # ==========================================================================
    # connection_pool_size 한계
    # ==========================================================================
    connection_pool_size_min: float = Field(
        default=1,
        ge=1,
        description="커넥션 풀 크기 최소값",
    )
    connection_pool_size_max: float = Field(
        default=100,
        le=500,
        description="커넥션 풀 크기 최대값",
    )
    connection_pool_size_max_change: float = Field(
        default=0.2,
        ge=0.01,
        le=1.0,
        description="커넥션 풀 크기 한 사이클당 최대 변경 비율",
    )

    def get_bounds(self, parameter: str) -> ParameterBoundConfig | None:
        """
        파라미터명으로 한계 설정 조회.

        Args:
            parameter: 파라미터명 (예: "timeout_ms", "retry_count")

        Returns:
            ParameterBoundConfig 또는 None (알 수 없는 파라미터)
        """
        # 파라미터명 정규화 (하이픈 → 언더스코어)
        normalized = parameter.replace("-", "_")

        min_attr = f"{normalized}_min"
        max_attr = f"{normalized}_max"
        change_attr = f"{normalized}_max_change"

        if not hasattr(self, min_attr):
            return None

        return ParameterBoundConfig(
            min_value=getattr(self, min_attr),
            max_value=getattr(self, max_attr),
            max_change_per_cycle=getattr(self, change_attr),
        )


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: SafetyBoundsSettings | None = None


def get_safety_bounds_settings() -> SafetyBoundsSettings:
    """
    캐시된 SafetyBoundsSettings 인스턴스 반환.

    Returns:
        SafetyBoundsSettings: 싱글톤 인스턴스
    """
    global _settings
    if _settings is None:
        _settings = SafetyBoundsSettings()
        logger.debug(
            "[SafetyBoundsSettings] Loaded: "
            f"timeout_ms={_settings.timeout_ms_min}-{_settings.timeout_ms_max}, "
            f"retry_count={_settings.retry_count_min}-{_settings.retry_count_max}"
        )
    return _settings


def reset_safety_bounds_settings() -> None:
    """
    캐시된 설정 초기화 (테스트용).
    """
    global _settings
    _settings = None
    logger.debug("safety_bounds_settings.reset")
