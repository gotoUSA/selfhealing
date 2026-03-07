"""
Thread Management Settings — Pydantic v2.

Thread join 및 background worker timeout 설정.
하드코딩된 thread.join(timeout=N) 값을 환경변수로 제어 가능하게 한다.

Environment Variables:
    SELFHEALING_THREAD_JOIN_TIMEOUT=5.0
    SELFHEALING_THREAD_JOIN_TIMEOUT_LONG=10.0
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ThreadManagementSettings(BaseSettings):
    """Thread join 및 background worker timeout 설정."""

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_THREAD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    join_timeout: float = Field(
        default=5.0,
        ge=1.0,
        le=60.0,
        description="Default timeout for thread.join() calls",
    )

    join_timeout_long: float = Field(
        default=10.0,
        ge=5.0,
        le=120.0,
        description="Timeout for long-running thread joins (event bus, capacity reservation)",
    )


def get_thread_management_settings() -> "ThreadManagementSettings":
    """Root settings 경유 단일 진입점 (SSOT)."""
    from selfhealing.settings.root import get_config

    return get_config().thread


def reset_thread_management_settings() -> None:
    """Root reset으로 위임 (테스트용)."""
    from selfhealing.settings.root import reset_config

    reset_config()
