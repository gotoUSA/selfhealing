"""
CgroupResourceMonitor Settings - Pydantic v2.

컨테이너/VM 리소스 모니터링 설정.
메모리/CPU 안전 마진을 환경변수로 설정 가능.

Environment Variables:
    SELFHEALING_RESOURCE_SAFETY_MARGIN=0.15
    SELFHEALING_RESOURCE_CPU_MARGIN=0.10
"""

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class ResourceMonitorSettings(BaseSettings):
    """
    CgroupResourceMonitor 설정.

    Chaos Experiment의 Resource Exhaustion이 안전 한계 내에서 동작하도록
    리소스 사용량 마진을 설정합니다.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_RESOURCE_",
        env_file=None,
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # 메모리 안전 마진
    # ==========================================================================
    safety_margin: float = Field(
        default=0.15,
        ge=0.05,
        le=0.5,
        description="메모리 사용량 안전 마진 (0.15 = 15%). OOM Killer 방지용 여유분.",
    )

    # ==========================================================================
    # CPU 안전 마진 (향후 확장용)
    # ==========================================================================
    cpu_margin: float = Field(
        default=0.10,
        ge=0.05,
        le=0.5,
        description="CPU 사용량 안전 마진 (0.10 = 10%). 향후 CPU 제한 모니터링용.",
    )


def get_resource_monitor_settings() -> "ResourceMonitorSettings":
    from selfhealing.settings.root import get_config

    return get_config().resilience.resource_monitor


def reset_resource_monitor_settings() -> None:
    from selfhealing.settings.root import get_config

    try:
        del get_config().resilience.__dict__["resource_monitor"]
    except KeyError:
        pass
