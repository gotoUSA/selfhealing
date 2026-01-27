"""
Notification Channel Settings - Pydantic v2.

알림 채널별 Rate Limiting 및 재시도 정책 설정입니다.

Replaces:
- services/unified_notification.py:채널 매핑
- core/safe_defaults.py:notification 관련 설정
- notification_policy.py:cooldown_seconds

Environment Variables:
    SELFHEALING_NOTIFICATION_CHANNEL_RATE_LIMIT_PER_MINUTE=60
    SELFHEALING_NOTIFICATION_CHANNEL_MAX_RETRY=3
    SELFHEALING_NOTIFICATION_CHANNEL_COOLDOWN_SECONDS=300

Reference:
- docs/self_healing/middleware_system/92_CONFIG_IMPLEMENTATION_GUIDE.md (Week 3 [15])
- docs/self_healing/middleware_system/91_CONFIG_INVENTORY.md §6.33, §8.6
"""

import logging

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class NotificationChannelSettings(BaseSettings):
    """
    알림 채널 설정.

    심각도별 채널 매핑 및 Rate Limiting을 관리합니다.

    Features:
    - 심각도별 채널 라우팅 (CRITICAL → slack,email,pagerduty)
    - Rate Limiting으로 알림 폭주 방지
    - 재시도 정책
    - 쿨다운으로 중복 알림 방지
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_NOTIFICATION_CHANNEL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Rate Limiting (from safe_defaults.py, notification_config.py)
    # ==========================================================================
    rate_limit_per_minute: int = Field(
        default=60,
        ge=1,
        le=1000,
        description="분당 최대 알림 수",
    )

    rate_limit_per_hour: int = Field(
        default=300,
        ge=10,
        le=5000,
        description="시간당 최대 알림 수",
    )

    # ==========================================================================
    # Retry Settings (from notification_config.py)
    # ==========================================================================
    max_retry: int = Field(
        default=3,
        ge=0,
        le=10,
        description="알림 전송 최대 재시도 횟수",
    )

    retry_delay_seconds: int = Field(
        default=30,
        ge=5,
        le=300,
        description="재시도 간격 (초)",
    )

    # ==========================================================================
    # Cooldown Settings (from notification_policy.py#L100)
    # ==========================================================================
    cooldown_seconds: int = Field(
        default=300,
        ge=60,
        le=3600,
        description="동일 알림 재발송 대기 시간 (초)",
    )

    # ==========================================================================
    # Channel Defaults (from unified_notification.py#L150-152)
    # ==========================================================================
    default_channels: list[str] = Field(
        default_factory=lambda: ["slack"],
        description="기본 알림 채널 목록",
    )

    critical_channels: list[str] = Field(
        default_factory=lambda: ["slack", "email", "sms", "pagerduty"],
        description="CRITICAL 심각도 알림 채널",
    )

    high_channels: list[str] = Field(
        default_factory=lambda: ["slack", "email"],
        description="HIGH 심각도 알림 채널",
    )

    medium_channels: list[str] = Field(
        default_factory=lambda: ["slack"],
        description="MEDIUM 심각도 알림 채널",
    )

    low_channels: list[str] = Field(
        default_factory=lambda: ["slack"],
        description="LOW 심각도 알림 채널",
    )

    # ==========================================================================
    # Escalation Settings (from models.py#L351)
    # ==========================================================================
    escalation_channels: list[str] = Field(
        default_factory=lambda: ["slack", "pagerduty"],
        description="에스컬레이션 알림 채널",
    )

    escalate_on_emergency: bool = Field(
        default=True,
        description="비상 상황 시 자동 에스컬레이션",
    )

    # ==========================================================================
    # Timeout Settings (from safe_defaults.py)
    # ==========================================================================
    timeout_seconds: int = Field(
        default=10,
        ge=1,
        le=60,
        description="알림 전송 타임아웃 (초)",
    )

    @field_validator("rate_limit_per_minute")
    @classmethod
    def validate_rate_limit(cls, v: int) -> int:
        """Rate limit이 너무 높으면 경고."""
        if v > 100:
            logger.warning(
                f"[NotificationChannel] rate_limit_per_minute={v}는 높은 값입니다. "
                "알림 폭주에 주의하세요."
            )
        return v


# Singleton instance (cached)
_settings: NotificationChannelSettings | None = None


def get_notification_channel_settings() -> NotificationChannelSettings:
    """Get cached NotificationChannelSettings instance."""
    global _settings
    if _settings is None:
        _settings = NotificationChannelSettings()
    return _settings


def reset_notification_channel_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
