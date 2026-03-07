"""
API Rate Limit Settings - Pydantic v2.

Django API Rate Limiting 미들웨어를 위한 설정 클래스.
api/django/rate_limit.py의 하드코딩된 상수들을 환경변수 기반으로 관리.

Environment Variables:
    SELFHEALING_API_RATE_DEFAULT_LIMIT=100
    SELFHEALING_API_RATE_DEFAULT_WINDOW_SECONDS=60
    SELFHEALING_API_RATE_EMERGENCY_LIMIT=10
    SELFHEALING_API_RATE_EMERGENCY_WINDOW_SECONDS=60
    SELFHEALING_API_RATE_CONTROL_API_PATH_PREFIX=/api/self-healing/
    SELFHEALING_API_RATE_REDIS_PING_INTERVAL=5
    SELFHEALING_API_RATE_REDIS_FAILURE_THRESHOLD=3
    SELFHEALING_API_RATE_REDIS_RECOVERY_JITTER_MAX=10
"""

import structlog
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class ApiRateLimitSettings(BaseSettings):
    """
    API Rate Limiting 설정 (Django 미들웨어용).

    Normal Mode (Redis 가용):
        - default_limit: 분당 최대 요청 수
        - default_window_seconds: 윈도우 크기 (초)

    Emergency Mode (Redis 불가):
        - emergency_limit: 분당 최대 요청 수 (분산 미동기화로 10배 엄격)
        - emergency_window_seconds: 비상 윈도우 크기 (초)

    Redis Health Checker:
        - redis_ping_interval: 헬스체크 간격 (초)
        - redis_failure_threshold: UNHEALTHY 판정을 위한 연속 실패 횟수
        - redis_recovery_jitter_max: Thundering Herd 방지용 최대 지터 (초)
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_API_RATE_",
        env_file=None,
        extra="ignore",
        validate_default=True,
    )

    # =========================================================================
    # Normal Mode Settings (Redis 가용 시)
    # =========================================================================
    default_limit: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="분당 최대 요청 수 (Redis 정상 시)",
    )
    default_window_seconds: int = Field(
        default=60,
        ge=1,
        le=3600,
        description="Rate Limit 윈도우 크기 (초)",
    )

    # =========================================================================
    # Emergency Mode Settings (Redis 장애 시)
    # =========================================================================
    emergency_limit: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="분당 최대 요청 수 (Redis 장애 시, 로컬 메모리 기반)",
    )
    emergency_window_seconds: int = Field(
        default=60,
        ge=1,
        le=3600,
        description="비상 모드 윈도우 크기 (초)",
    )

    # =========================================================================
    # Control API Path Configuration
    # =========================================================================
    control_api_path_prefix: str = Field(
        default="/api/self-healing/",
        description="Rate Limit이 적용되는 API 경로 prefix",
    )

    # =========================================================================
    # Redis Health Checker Settings
    # =========================================================================
    redis_ping_interval: int = Field(
        default=5,
        ge=1,
        le=60,
        description="Redis 헬스체크 간격 (초)",
    )
    redis_failure_threshold: int = Field(
        default=3,
        ge=1,
        le=20,
        description="UNHEALTHY 상태 전환을 위한 연속 실패 횟수",
    )
    redis_recovery_jitter_max: int = Field(
        default=10,
        ge=1,
        le=60,
        description="복구 시 Thundering Herd 방지를 위한 최대 지터 (초)",
    )

    # =========================================================================
    # Local Memory Limiter Settings
    # =========================================================================
    local_cleanup_interval: int = Field(
        default=60,
        ge=10,
        le=300,
        description="로컬 메모리 레이트 리미터 정리 간격 (초)",
    )

    @field_validator("emergency_limit")
    @classmethod
    def validate_emergency_limit(cls, v: int, info) -> int:
        """
        Emergency limit은 default_limit보다 낮아야 합니다.
        분산 환경에서 동기화되지 않으므로 보수적인 값을 권장.
        """
        if v > 50:
            logger.warning(
                "api_rate_limit.high_consider_using_safety",
                setting_value=v,
            )
        return v


def get_api_rate_limit_settings() -> "ApiRateLimitSettings":
    from selfhealing.settings.root import get_config

    return get_config().services_group.api_rate_limit


def reset_api_rate_limit_settings() -> None:
    from selfhealing.settings.root import get_config

    try:
        del get_config().services_group.__dict__["api_rate_limit"]
    except KeyError:
        pass
