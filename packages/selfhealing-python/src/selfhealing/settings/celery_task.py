"""
Celery Task Settings - Pydantic v2.

Celery 태스크 기본 설정입니다.

Replaces:
- shopping/tasks/*.py 의 task 데코레이터 설정
- recovery_tasks.py:max_retries, default_retry_delay

Environment Variables:
    SELFHEALING_CELERY_MAX_RETRIES=3
    SELFHEALING_CELERY_DEFAULT_RETRY_DELAY=60
    SELFHEALING_CELERY_TIME_LIMIT=300

Reference:
- docs/self_healing/middleware_system/92_CONFIG_IMPLEMENTATION_GUIDE.md (Week 4 [21])
- docs/self_healing/middleware_system/91_CONFIG_INVENTORY.md §3.7
"""

import logging
from typing import Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class CeleryTaskSettings(BaseSettings):
    """
    Celery 태스크 기본 설정.

    재시도 전략:
    - max_retries: 최대 재시도 횟수 (3회)
    - default_retry_delay: 기본 재시도 지연 (60초)
    - min_retry_delay: 최소 재시도 지연 (30초)
    - max_retry_delay: 최대 재시도 지연 (300초)
    - backoff_multiplier: 지수 백오프 승수 (2)

    시간 제한:
    - time_limit: 하드 타임아웃 (300초)
    - soft_time_limit: 소프트 타임아웃 (240초)

    주의: soft_time_limit < time_limit 보장 필요
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CELERY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Retry Configuration - from tasks/*.py
    # ==========================================================================
    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="최대 재시도 횟수",
    )

    default_retry_delay: int = Field(
        default=60,
        ge=10,
        le=600,
        description="기본 재시도 지연 (초)",
    )

    min_retry_delay: int = Field(
        default=30,
        ge=5,
        le=300,
        description="최소 재시도 지연 (초)",
    )

    max_retry_delay: int = Field(
        default=300,
        ge=60,
        le=3600,
        description="최대 재시도 지연 (초)",
    )

    backoff_multiplier: float = Field(
        default=2.0,
        ge=1.0,
        le=5.0,
        description="지수 백오프 승수",
    )

    # ==========================================================================
    # Time Limits - from tasks/*.py
    # ==========================================================================
    time_limit: int = Field(
        default=300,
        ge=30,
        le=3600,
        description="하드 타임아웃 (초)",
    )

    soft_time_limit: int = Field(
        default=240,
        ge=20,
        le=3500,
        description="소프트 타임아웃 (초). time_limit보다 작아야 함.",
    )

    # ==========================================================================
    # Rate Limiting - from tasks/*.py
    # ==========================================================================
    default_rate_limit: str = Field(
        default="10/s",
        pattern=r"^\d+/(s|m|h)$",
        description="기본 레이트 리밋 (예: '10/s', '60/m')",
    )

    # ==========================================================================
    # Queue Configuration - 기본 큐 이름
    # ==========================================================================
    default_queue: str = Field(
        default="selfhealing.default",
        description="기본 큐 이름",
    )

    # ==========================================================================
    # Recovery Tasks Specific - from recovery_tasks.py
    # ==========================================================================
    trigger_check_interval: int = Field(
        default=60,
        ge=10,
        le=300,
        description="트리거 체크 간격 (초)",
    )

    health_monitor_interval: int = Field(
        default=30,
        ge=10,
        le=120,
        description="헬스 모니터 간격 (초)",
    )

    stale_check_interval: int = Field(
        default=10,
        ge=1,
        le=60,
        description="스테일 체크 간격 (분)",
    )

    # ==========================================================================
    # Celery Inspector Timeout (from adapters/queues/celery_adapter.py)
    # ==========================================================================
    inspect_timeout: int = Field(
        default=2,
        ge=1,
        le=30,
        description="Celery inspect 호출 타임아웃 (초). 워커 상태 확인 시 사용.",
    )

    @model_validator(mode="after")
    def validate_time_limits(self) -> "CeleryTaskSettings":
        """soft_time_limit이 time_limit보다 작은지 검증."""
        if self.soft_time_limit >= self.time_limit:
            raise ValueError(
                f"soft_time_limit ({self.soft_time_limit}) must be less than "
                f"time_limit ({self.time_limit})"
            )
        if self.min_retry_delay > self.max_retry_delay:
            raise ValueError(
                f"min_retry_delay ({self.min_retry_delay}) must be less than or equal to "
                f"max_retry_delay ({self.max_retry_delay})"
            )
        return self


# ==========================================================================
# Singleton 관리
# ==========================================================================
_celery_task_settings: Optional[CeleryTaskSettings] = None


def get_celery_task_settings() -> CeleryTaskSettings:
    """Get cached CeleryTaskSettings instance."""
    global _celery_task_settings
    if _celery_task_settings is None:
        _celery_task_settings = CeleryTaskSettings()
    return _celery_task_settings


def reset_celery_task_settings() -> None:
    """Reset cached settings (for testing)."""
    global _celery_task_settings
    _celery_task_settings = None
