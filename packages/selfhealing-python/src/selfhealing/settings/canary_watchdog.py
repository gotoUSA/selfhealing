"""
Canary Watchdog Settings - Pydantic v2.

Canary Rollout Watchdog 태스크 설정.
Zombie 롤아웃 감지, 자동 롤백, 자동 프로모션 설정.

Source:
- tasks/canary_watchdog.py

Environment Variables:
    SELFHEALING_CANARY_WATCHDOG_ZOMBIE_THRESHOLD_MINUTES=30
    SELFHEALING_CANARY_WATCHDOG_AUTO_ROLLBACK_MINUTES=60
    SELFHEALING_CANARY_WATCHDOG_MAX_STAGE_DURATION_MINUTES=15
    SELFHEALING_CANARY_WATCHDOG_ENABLE_AUTO_PROMOTE=true
    SELFHEALING_CANARY_WATCHDOG_ENABLE_AUTO_ROLLBACK=true
    SELFHEALING_CANARY_WATCHDOG_SLACK_CHANNEL=#selfhealing-alerts
"""

import logging

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class CanaryWatchdogSettings(BaseSettings):
    """
    Canary Watchdog 설정.

    Zombie 롤아웃 감지 임계값, 자동 롤백/프로모션, 알림 설정을 정의합니다.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CANARY_WATCHDOG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Zombie Detection (from canary_watchdog.py line 64)
    # ==========================================================================
    zombie_threshold_minutes: int = Field(
        default=30,
        ge=5,
        le=240,
        description="정체로 간주하는 시간 (분)",
    )

    # ==========================================================================
    # Auto Rollback (from canary_watchdog.py line 65)
    # ==========================================================================
    auto_rollback_after_minutes: int = Field(
        default=60,
        ge=10,
        le=480,
        description="자동 롤백까지 대기 시간 (분)",
    )

    # ==========================================================================
    # Stage Duration (from canary_watchdog.py line 66)
    # ==========================================================================
    max_stage_duration_minutes: int = Field(
        default=15,
        ge=1,
        le=120,
        description="단계별 최대 체류 시간 (분)",
    )

    # ==========================================================================
    # Feature Toggles
    # ==========================================================================
    enable_auto_promote: bool = Field(
        default=True,
        description="자동 프로모션 활성화",
    )
    enable_auto_rollback: bool = Field(
        default=True,
        description="Zombie 자동 롤백 활성화",
    )
    notification_enabled: bool = Field(
        default=True,
        description="Slack 알림 활성화",
    )

    # ==========================================================================
    # Notification (from canary_watchdog.py line 70)
    # ==========================================================================
    slack_channel: str = Field(
        default="#selfhealing-alerts",
        min_length=1,
        max_length=100,
        description="알림 Slack 채널",
    )

    @model_validator(mode="after")
    def validate_timing(self) -> "CanaryWatchdogSettings":
        """auto_rollback이 zombie_threshold보다 큰지 확인."""
        if self.auto_rollback_after_minutes <= self.zombie_threshold_minutes:
            raise ValueError(
                f"auto_rollback_after_minutes ({self.auto_rollback_after_minutes}) "
                f"must be greater than zombie_threshold_minutes ({self.zombie_threshold_minutes})"
            )
        return self


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: CanaryWatchdogSettings | None = None


def get_canary_watchdog_settings() -> CanaryWatchdogSettings:
    """
    캐시된 CanaryWatchdogSettings 인스턴스 반환.

    Returns:
        CanaryWatchdogSettings: 싱글톤 인스턴스
    """
    global _settings
    if _settings is None:
        _settings = CanaryWatchdogSettings()
    return _settings


def reset_canary_watchdog_settings() -> None:
    """
    캐시된 설정 초기화 (테스트용).

    환경 변수 변경 후 설정을 다시 로드하려면 이 함수를 호출하세요.
    """
    global _settings
    _settings = None
