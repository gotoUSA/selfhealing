"""
Auto-Configuration Settings for configure_selfhealing() wrapper.

Controls the behavior of the configure_selfhealing() function via
environment variables (SELFHEALING_AUTO_* prefix).
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class AutoConfigSettings(BaseSettings):
    """configure_selfhealing() 래퍼의 동작을 제어하는 설정."""

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_AUTO_",
        env_file=None,
        extra="ignore",
        validate_default=True,
    )

    middleware: bool = True
    exception_handler: bool = True
    otel: bool = True
    celery_signal_warning: bool = True


_cached: AutoConfigSettings | None = None


def get_auto_config_settings() -> AutoConfigSettings:
    global _cached
    if _cached is None:
        _cached = AutoConfigSettings()
    return _cached


def reset_auto_config_settings() -> None:
    global _cached
    _cached = None
