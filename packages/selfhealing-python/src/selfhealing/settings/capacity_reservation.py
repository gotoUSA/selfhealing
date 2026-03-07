"""
Capacity Reservation Settings - Pydantic v2.

예정 이벤트 기반 사전 용량 확보 설정.

환경변수 접두사: SELFHEALING_CAPACITY_RESERVATION_
"""

from __future__ import annotations


from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CapacityReservationSettings(BaseSettings):
    """
    Capacity Reservation 설정.

    환경변수:
        SELFHEALING_CAPACITY_RESERVATION_ENABLED=false
        SELFHEALING_CAPACITY_RESERVATION_DEFAULT_WARMUP_MINUTES=5
        SELFHEALING_CAPACITY_RESERVATION_DRY_RUN=true
        ...
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CAPACITY_RESERVATION_",
        env_file=None,
        extra="ignore",
        validate_default=True,
    )

    enabled: bool = Field(
        default=False,
        description="Capacity Reservation 서비스 활성화 여부",
    )

    default_warmup_minutes: int = Field(
        default=5,
        ge=1,
        le=60,
        description="기본 사전 워밍 시간 (분)",
    )

    scheduler_interval_seconds: int = Field(
        default=30,
        ge=5,
        le=300,
        description="스케줄러 확인 주기 (초)",
    )

    max_rate_multiplier: float = Field(
        default=5.0,
        ge=1.0,
        le=20.0,
        description="Rate 확장 최대 배율",
    )

    max_pool_multiplier: float = Field(
        default=3.0,
        ge=1.0,
        le=10.0,
        description="Pool 확장 최대 배율",
    )

    max_bulkhead_extra_permits: int = Field(
        default=100,
        ge=0,
        le=1000,
        description="Bulkhead 추가 permit 최대값",
    )

    cooldown_grace_period_seconds: int = Field(
        default=300,
        ge=60,
        le=3600,
        description="이벤트 종료 후 설정 복원까지 유예 시간 (초)",
    )

    max_concurrent_events: int = Field(
        default=3,
        ge=1,
        le=10,
        description="동시 진행 이벤트 수 상한",
    )

    dry_run: bool = Field(
        default=True,
        description="True이면 로그만 기록, 실제 조정 미수행",
    )

    safety_valve_cpu_threshold: float = Field(
        default=0.95,
        ge=0.5,
        le=1.0,
        description="Safety Valve 발동 CPU 임계치. 이벤트 모드라도 이 값 초과 시 즉시 CRITICAL 전환",
    )

    safety_valve_error_rate_threshold: float = Field(
        default=0.10,
        ge=0.01,
        le=1.0,
        description="Safety Valve 발동 Error Rate 임계치",
    )

    safety_valve_min_hold_seconds: int = Field(
        default=120,
        ge=30,
        le=600,
        description="Safety Valve 발동 후 최소 유지 시간 (Flapping 방지)",
    )


def get_capacity_reservation_settings() -> "CapacityReservationSettings":
    from selfhealing.settings.root import get_config

    return get_config().services_group.capacity_reservation


def reset_capacity_reservation_settings() -> None:
    from selfhealing.settings.root import get_config

    try:
        del get_config().services_group.__dict__["capacity_reservation"]
    except KeyError:
        pass
