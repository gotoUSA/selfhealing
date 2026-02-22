"""
Daily Report Settings - Pydantic v2.

일일 자율 운영 리포트 생성 태스크 설정.

Source:
- tasks/daily_report.py

Environment Variables:
    SELFHEALING_DAILY_REPORT_MAX_RETRIES=2
    SELFHEALING_DAILY_REPORT_RETRY_DELAY=300
    SELFHEALING_DAILY_REPORT_DEFAULT_CHANNELS=slack
"""

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class DailyReportSettings(BaseSettings):
    """
    일일 자율 운영 리포트 태스크 설정.

    generate_daily_autonomous_report_task의 재시도 전략 및
    기본 알림 채널 설정을 정의합니다.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_DAILY_REPORT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Celery Task 재시도 설정 (generate_daily_autonomous_report_task)
    # ==========================================================================
    max_retries: int = Field(
        default=2,
        ge=0,
        le=10,
        description="일일 리포트 생성 태스크 최대 재시도 횟수",
    )
    retry_delay: int = Field(
        default=300,
        ge=30,
        le=1800,
        description="일일 리포트 생성 태스크 재시도 지연 (초)",
    )

    # ==========================================================================
    # 리포트 기본 설정
    # ==========================================================================
    default_channels: list[str] = Field(
        default_factory=lambda: ["slack"],
        description="기본 알림 채널 목록",
    )
    default_hour: int = Field(
        default=9,
        ge=0,
        le=23,
        description="일일 리포트 기본 생성 시간 (시)",
    )
    default_minute: int = Field(
        default=0,
        ge=0,
        le=59,
        description="일일 리포트 기본 생성 시간 (분)",
    )

    # ==========================================================================
    # Cache TTL (from aggregator.py line 62)
    # ==========================================================================
    cache_ttl: int = Field(
        default=172800,
        ge=3600,
        le=604800,
        description="일일 리포트 캐시 TTL (초). 기본값 172800 = 2일",
    )


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: DailyReportSettings | None = None


def get_daily_report_settings() -> DailyReportSettings:
    """
    캐시된 DailyReportSettings 인스턴스 반환.

    Returns:
        DailyReportSettings: 싱글톤 인스턴스
    """
    global _settings
    if _settings is None:
        _settings = DailyReportSettings()
    return _settings


def reset_daily_report_settings() -> None:
    """
    캐시된 설정 초기화 (테스트용).

    환경 변수 변경 후 설정을 다시 로드하려면 이 함수를 호출하세요.
    """
    global _settings
    _settings = None
