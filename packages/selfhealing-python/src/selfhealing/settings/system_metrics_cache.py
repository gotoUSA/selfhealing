"""
System Metrics Cache Settings - Pydantic v2.

psutil CPU/Memory 백그라운드 캐시의 동작을 제어하는 설정.
캐시는 1초 주기로 psutil.cpu_percent(interval=0.1)을 호출하여 캐시하며,
소비자(collect_system_snapshot, ResourceGuard 등)가 ~0ms에 읽을 수 있도록 한다.

Environment Variables:
    SELFHEALING_SYSTEM_METRICS_CACHE_ENABLED=true
    SELFHEALING_SYSTEM_METRICS_CACHE_REFRESH_INTERVAL=1.0
    SELFHEALING_SYSTEM_METRICS_CACHE_SAMPLE_INTERVAL=0.1
    SELFHEALING_SYSTEM_METRICS_CACHE_MAX_AGE_SECONDS=5.0
"""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SystemMetricsCacheSettings(BaseSettings):
    """psutil CPU/Memory 백그라운드 캐시 설정."""

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_SYSTEM_METRICS_CACHE_",
        env_file=None,
        extra="ignore",
        validate_default=True,
    )

    enabled: bool = Field(
        default=True,
        description="시스템 메트릭 캐시 활성화 여부",
    )
    refresh_interval: float = Field(
        default=1.0,
        ge=0.5,
        le=10.0,
        description="캐시 갱신 주기 (초). 1.0 = 1초마다 psutil 측정.",
    )
    sample_interval: float = Field(
        default=0.1,
        ge=0.05,
        le=1.0,
        description="psutil.cpu_percent(interval=?) 값. 0.1 = 100ms 샘플링.",
    )
    max_age_seconds: float = Field(
        default=5.0,
        ge=1.0,
        le=60.0,
        description="캐시 유효 최대 시간. 초과 시 source='stale'로 표시.",
    )

    @field_validator("refresh_interval")
    @classmethod
    def refresh_must_be_greater_than_sample(cls, v, info):
        sample = info.data.get("sample_interval", 0.1)
        if v <= sample:
            raise ValueError(f"refresh_interval ({v}) must be > sample_interval ({sample})")
        return v


def get_system_metrics_cache_settings() -> "SystemMetricsCacheSettings":
    from selfhealing.settings.root import get_config

    return get_config().metrics_group.system_metrics_cache

def reset_system_metrics_cache_settings() -> None:
    from selfhealing.settings.root import get_config

    try:
        del get_config().metrics_group.__dict__["system_metrics_cache"]
    except KeyError:
        pass
