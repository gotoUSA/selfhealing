"""
Slack Channel Settings - Pydantic v2.

Slack 채널 매핑 및 관련 설정입니다.

Replaces:
- services/unified_notification.py 내 채널 매핑
- services/notification_policy.py 설정

Environment Variables:
    SELFHEALING_SLACK_DEFAULT_CHANNEL=#selfhealing-alerts
    SELFHEALING_SLACK_CRITICAL_CHANNEL=#selfhealing-critical

Reference:
- docs/self_healing/middleware_system/92_CONFIG_IMPLEMENTATION_GUIDE.md (Week 4 [24])
- docs/self_healing/middleware_system/91_CONFIG_INVENTORY.md §6.33
"""

import structlog
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class SlackChannelSettings(BaseSettings):
    """
    Slack 채널 설정.

    채널 매핑:
    - default_channel: 기본 알림 채널
    - critical_channel: 크리티컬 알림 채널
    - emergency_channel: 비상 상황 채널
    - recovery_channel: 복구 알림 채널
    - audit_channel: 감사 로그 채널

    메시지 설정:
    - block_text_limit: Slack 블록 텍스트 제한 (3000)
    - max_attachments: 최대 첨부 수 (10)
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_SLACK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Channel Names - from unified_notification.py
    # ==========================================================================
    default_channel: str = Field(
        default="#selfhealing-alerts",
        description="기본 알림 채널",
    )

    critical_channel: str = Field(
        default="#selfhealing-critical",
        description="크리티컬 알림 채널",
    )

    emergency_channel: str = Field(
        default="#selfhealing-emergency",
        description="비상 상황 채널",
    )

    recovery_channel: str = Field(
        default="#selfhealing-recovery",
        description="복구 알림 채널",
    )

    audit_channel: str = Field(
        default="#selfhealing-audit",
        description="감사 로그 채널",
    )

    on_call_channel: str = Field(
        default="#on-call",
        description="당직자 알림 채널",
    )

    # ==========================================================================
    # Message Limits - from notification config
    # ==========================================================================
    block_text_limit: int = Field(
        default=3000,
        ge=1000,
        le=10000,
        description="Slack 블록 텍스트 최대 길이",
    )

    max_attachments: int = Field(
        default=10,
        ge=1,
        le=50,
        description="메시지당 최대 첨부 수",
    )

    # ==========================================================================
    # Text Limits - from config.py
    # ==========================================================================
    title_max_length: int = Field(
        default=150,
        ge=50,
        le=500,
        description="제목 최대 길이",
    )

    description_max_length: int = Field(
        default=500,
        ge=100,
        le=2000,
        description="설명 최대 길이",
    )

    action_taken_max_length: int = Field(
        default=200,
        ge=50,
        le=500,
        description="조치 내용 최대 길이",
    )

    # ==========================================================================
    # Webhook Timeout - from config.py
    # ==========================================================================
    webhook_timeout_seconds: int = Field(
        default=10,
        ge=5,
        le=60,
        description="웹훅 요청 타임아웃 (초)",
    )

    @field_validator(
        "default_channel",
        "critical_channel",
        "emergency_channel",
        "recovery_channel",
        "audit_channel",
        "on_call_channel",
    )
    @classmethod
    def validate_channel_name(cls, v: str) -> str:
        """채널 이름이 # 또는 C로 시작하는지 확인."""
        if not v.startswith("#") and not v.startswith("C"):
            raise ValueError(
                f"Channel name must start with '#' (name) or 'C' (ID): {v}"
            )
        return v

    def get_channel_for_severity(self, severity: str) -> str:
        """심각도에 따른 채널 반환."""
        severity_upper = severity.upper()
        if severity_upper == "CRITICAL":
            return self.critical_channel
        elif severity_upper == "EMERGENCY":
            return self.emergency_channel
        elif severity_upper in ("HIGH", "WARNING"):
            return self.critical_channel
        return self.default_channel


# ==========================================================================
# Singleton 관리
# ==========================================================================
_slack_channel_settings: SlackChannelSettings | None = None


def get_slack_channel_settings() -> SlackChannelSettings:
    """Get cached SlackChannelSettings instance."""
    global _slack_channel_settings
    if _slack_channel_settings is None:
        _slack_channel_settings = SlackChannelSettings()
    return _slack_channel_settings


def reset_slack_channel_settings() -> None:
    """Reset cached settings (for testing)."""
    global _slack_channel_settings
    _slack_channel_settings = None
