"""
Corruption Shield Configuration (하위 호환 re-export).

실제 정의: selfhealing.settings.corruption_shield

기존 CorruptionShieldConfig(dataclass) → CorruptionShieldSettings(BaseSettings) 으로 통합.
CorruptionShieldConfig 이름은 하위 호환을 위해 alias 로 유지.
"""

from selfhealing.settings.corruption_shield import (  # noqa: F401
    CorruptionShieldSettings as CorruptionShieldConfig,
    get_corruption_shield_settings,
    reset_corruption_shield_settings,
)

__all__ = [
    "CorruptionShieldConfig",
    "get_corruption_shield_settings",
    "reset_corruption_shield_settings",
]
