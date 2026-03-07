"""
Base Settings Configuration for Pydantic Settings.

Provides common configuration for all settings classes.
"""

from pydantic_settings import SettingsConfigDict

# Common configuration for all settings
COMMON_SETTINGS_CONFIG = SettingsConfigDict(
    env_file=None,
    extra="ignore",
    validate_default=True,
)
