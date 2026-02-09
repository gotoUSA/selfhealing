"""
Auto-Scaling & Backpressure 설정 (하위 호환 re-export).

실제 정의: selfhealing.settings.backpressure
"""

from selfhealing.settings.backpressure import (  # noqa: F401
    LEVEL_RATE_MULTIPLIERS,
    BackpressureLevel,
    BackpressureSettings,
    BackpressureStrategy,
    get_backpressure_settings,
    reset_backpressure_settings,
)

__all__ = [
    "BackpressureLevel",
    "BackpressureStrategy",
    "LEVEL_RATE_MULTIPLIERS",
    "BackpressureSettings",
    "get_backpressure_settings",
    "reset_backpressure_settings",
]
