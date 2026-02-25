"""
Audit Watchdog Settings - Pydantic v2.

Audit Watchdog (Dead Man's Switch Pattern) 설정.

Source:
- audit/audit_watchdog.py (WatchdogConfig, HeartbeatTarget)

Environment Variables:
    SELFHEALING_AUDIT_WATCHDOG_HEARTBEAT_INTERVAL_SECONDS=30.0
    SELFHEALING_AUDIT_WATCHDOG_MISSED_THRESHOLD=3
    SELFHEALING_AUDIT_WATCHDOG_TIMEOUT_SECONDS=5.0
    SELFHEALING_AUDIT_WATCHDOG_LOCAL_HEARTBEAT_FILE=
    SELFHEALING_AUDIT_WATCHDOG_HEARTBEAT_URL=
"""

import structlog
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class AuditWatchdogSettings(BaseSettings):
    """
    Audit Watchdog 설정.

    Dead Man's Switch 패턴으로 감사 시스템 생존 확인.
    주기적으로 heartbeat를 전송하고 외부 모니터링 시스템이 감시.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_AUDIT_WATCHDOG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Heartbeat Settings (from audit_watchdog.py lines 86-89)
    # ==========================================================================
    heartbeat_interval_seconds: float = Field(
        default=30.0,
        ge=10.0,
        le=300.0,
        description="Heartbeat 전송 주기 (초)",
    )

    missed_threshold: int = Field(
        default=3,
        ge=1,
        le=10,
        description="연속 실패 허용 횟수",
    )

    # ==========================================================================
    # Target Settings (from audit_watchdog.py HeartbeatTarget line 77)
    # ==========================================================================
    timeout_seconds: float = Field(
        default=5.0,
        ge=1.0,
        le=30.0,
        description="Heartbeat 요청 타임아웃 (초)",
    )

    # ==========================================================================
    # Local File Heartbeat
    # ==========================================================================
    local_heartbeat_file: str | None = Field(
        default=None,
        description="로컬 파일 heartbeat 경로 (외부 서비스 없이 작동)",
    )

    # ==========================================================================
    # Heartbeat URL
    # ==========================================================================
    heartbeat_url: str | None = Field(
        default=None,
        description="Heartbeat 전송 URL",
    )

    # ==========================================================================
    # Max Age (from audit_watchdog.py line 418)
    # ==========================================================================
    max_age_seconds: float = Field(
        default=60.0,
        ge=30.0,
        le=300.0,
        description="Heartbeat 최대 유효 시간 (초)",
    )

    @field_validator("heartbeat_interval_seconds")
    @classmethod
    def validate_heartbeat_interval(cls, v: float) -> float:
        """heartbeat 주기가 너무 짧으면 경고."""
        if v < 15.0:
            logger.warning(
                "audit_watchdog_settings.low_consider_using_reduce",
                setting_value=v,
            )
        return v


# =============================================================================
# Singleton Pattern
# =============================================================================

_settings: AuditWatchdogSettings | None = None


def get_audit_watchdog_settings() -> AuditWatchdogSettings:
    """
    캐시된 AuditWatchdogSettings 인스턴스 반환.

    Returns:
        AuditWatchdogSettings: 싱글톤 인스턴스
    """
    global _settings
    if _settings is None:
        _settings = AuditWatchdogSettings()
    return _settings


def reset_audit_watchdog_settings() -> None:
    """
    캐시된 Settings 초기화 (테스트용).
    """
    global _settings
    _settings = None
