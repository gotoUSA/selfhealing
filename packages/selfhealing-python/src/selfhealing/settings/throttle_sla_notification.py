"""
Throttle SLA Notification Settings - Pydantic v2.

Throttle SLA 알림 관련 설정을 관리합니다.
Warning/Critical/Recovery 각각의 활성화, 쿨다운, 채널 설정을 제공합니다.

Environment Variables:
    SELFHEALING_THROTTLE_SLA_NOTIFICATION_ENABLED=true
    SELFHEALING_THROTTLE_SLA_NOTIFICATION_WARNING_COOLDOWN_SECONDS=1800
    SELFHEALING_THROTTLE_SLA_NOTIFICATION_CRITICAL_COOLDOWN_SECONDS=900
"""

import structlog

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class ThrottleSLANotificationSettings(BaseSettings):
    """Throttle SLA 알림 설정."""

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_THROTTLE_SLA_NOTIFICATION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # 알림 활성화 여부
    enabled: bool = Field(default=True, description="SLA 알림 전체 활성화")

    # Warning/Critical 알림 별도 제어
    warning_enabled: bool = Field(default=True, description="Warning 알림 활성화")
    critical_enabled: bool = Field(default=True, description="Critical 알림 활성화")
    recovery_enabled: bool = Field(default=True, description="Recovery 알림 활성화")

    # Cooldown 오버라이드 (초)
    warning_cooldown_seconds: int = Field(
        default=1800,
        ge=60,
        le=7200,
        description="Warning 쿨다운 (초). SLA는 30분",
    )
    critical_cooldown_seconds: int = Field(
        default=900,
        ge=60,
        le=3600,
        description="Critical 쿨다운 (초). Critical은 더 짧음",
    )

    # Redis Cooldown 설정
    redis_cooldown_enabled: bool = Field(
        default=True,
        description="Redis 기반 영속적 쿨다운 사용 (False면 인메모리 전용)",
    )

    # 채널 오버라이드
    warning_channels: list[str] | None = Field(
        default=None,
        description="Warning 채널 (None이면 priority 기본 사용)",
    )
    critical_channels: list[str] | None = Field(
        default=None,
        description="Critical 채널 (None이면 priority 기본 사용)",
    )


# --- 싱글톤 + reset ---
_settings: ThrottleSLANotificationSettings | None = None


def get_throttle_sla_notification_settings() -> ThrottleSLANotificationSettings:
    """Get cached ThrottleSLANotificationSettings instance."""
    global _settings
    if _settings is None:
        _settings = ThrottleSLANotificationSettings()
    return _settings


def reset_throttle_sla_notification_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
