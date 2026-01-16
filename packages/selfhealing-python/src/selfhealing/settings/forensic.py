"""
Forensic Settings - Pydantic v2.

Single Source of Truth for forensic context configuration.

Replaces:
- core/config.py:ForensicConfig (lines 188-211)
- core/safe_defaults.py:SAFE_DEFAULTS["forensic"]
- core/safe_defaults.py:VALIDATION_RULES["forensic"]

Environment Variables:
    SELFHEALING_FORENSIC_ERROR_MESSAGE_MAX_LENGTH=500
    SELFHEALING_FORENSIC_MAX_STACK_FRAMES=50

Reference:
- docs/self_healing/middleware_system/40_PYDANTIC_CONFIG_MIGRATION.md
"""

import logging
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class ForensicSettings(BaseSettings):
    """
    Forensic context truncation limits with validation.

    All defaults match core/config.py:ForensicConfig
    All validation rules match core/safe_defaults.py:VALIDATION_RULES["forensic"]
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_FORENSIC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Truncation Limits (from core/config.py lines 200-202)
    # Validation rules from core/safe_defaults.py lines 269-273
    # ==========================================================================
    error_message_max_length: int = Field(
        default=500,
        ge=50,
        le=5000,
        description="Maximum length for error messages",
    )
    response_body_max_length: int = Field(
        default=5000,
        ge=100,
        le=100000,
        description="Maximum length for response body capture",
    )
    user_agent_max_length: int = Field(
        default=500,
        ge=50,
        le=2000,
        description="Maximum length for user agent strings",
    )

    # ==========================================================================
    # Phase 5 Settings (from core/config.py lines 205-211)
    # ==========================================================================
    max_stack_frames: int = Field(
        default=50,
        ge=10,
        le=200,
        description="Maximum stack frames to capture",
    )
    max_context_size_bytes: int = Field(
        default=65536,  # 64KB
        ge=1024,
        le=1048576,  # 1MB
        description="Maximum context size in bytes",
    )
    include_local_variables: bool = Field(
        default=False,
        description="Include local variables in stack traces (security risk)",
    )
    sanitize_sensitive_data: bool = Field(
        default=True,
        description="Sanitize sensitive data in forensic context",
    )
    sensitive_key_patterns: List[str] = Field(
        default_factory=lambda: ["password", "secret", "token", "key", "auth"],
        description="Patterns to match sensitive keys for sanitization",
    )


# Singleton instance (cached)
_settings: Optional[ForensicSettings] = None


def get_forensic_settings() -> ForensicSettings:
    """Get cached ForensicSettings instance."""
    global _settings
    if _settings is None:
        _settings = ForensicSettings()
    return _settings


def reset_forensic_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
