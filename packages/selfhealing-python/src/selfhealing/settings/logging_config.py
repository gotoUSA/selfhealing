"""
Logging Settings - Pydantic v2.

Single Source of Truth for logging configuration.

Replaces:
- core/config.py:LoggingConfig (lines 214-238)
- core/safe_defaults.py:SAFE_DEFAULTS["logging"]

Environment Variables:
    SELFHEALING_LOGGING_DLQ_LOG_LEVEL=INFO
    SELFHEALING_LOGGING_CIRCUIT_BREAKER_LOG_LEVEL=INFO

Reference:
- docs/self_healing/middleware_system/40_PYDANTIC_CONFIG_MIGRATION.md
"""

import structlog
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()

# Valid log levels
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class LoggingSettings(BaseSettings):
    """
    Logging configuration for Self-Healing components.

    All defaults match core/config.py:LoggingConfig
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_LOGGING_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Component Log Levels (from core/config.py lines 224-232)
    # ==========================================================================
    dlq_log_level: str = Field(
        default="INFO",
        description="Log level for DLQ component",
    )
    circuit_breaker_log_level: str = Field(
        default="INFO",
        description="Log level for Circuit Breaker component",
    )
    replay_log_level: str = Field(
        default="INFO",
        description="Log level for Replay component",
    )
    sla_log_level: str = Field(
        default="INFO",
        description="Log level for SLA component",
    )
    forensic_log_level: str = Field(
        default="DEBUG",
        description="Log level for Forensic component",
    )
    emergency_log_level: str = Field(
        default="WARNING",
        description="Log level for Emergency component",
    )
    chaos_log_level: str = Field(
        default="INFO",
        description="Log level for Chaos component",
    )
    l2_storage_log_level: str = Field(
        default="INFO",
        description="Log level for L2 Storage component",
    )

    # ==========================================================================
    # Log Format Settings (from core/config.py lines 234-236)
    # ==========================================================================
    include_timestamps: bool = Field(
        default=True,
        description="Include timestamps in log entries",
    )
    include_request_id: bool = Field(
        default=True,
        description="Include request ID in log entries",
    )
    include_user_info: bool = Field(
        default=False,
        description="Include user info in log entries (security consideration)",
    )

    # ==========================================================================
    # Log Output Settings (from core/config.py lines 238-240)
    # ==========================================================================
    console_output_enabled: bool = Field(
        default=True,
        description="Enable console log output",
    )
    file_output_enabled: bool = Field(
        default=False,
        description="Enable file log output",
    )
    structured_json: bool = Field(
        default=True,
        description="Use structured JSON format for logs",
    )

    # ==========================================================================
    # Event Name Validation (312 Q5, 314 Audit)
    # ==========================================================================
    strict_log_validation: bool = Field(
        default=False,
        description="Strict log event name validation. True = ValueError on violation (dev/test).",
    )

    # ==========================================================================
    # Log Volume Control (281_LOG_RATE_LIMITER, 282_LOG_SAMPLING)
    # ==========================================================================
    log_rate_limit_window: int = Field(
        default=10,
        ge=0,
        description="Rate limit window in seconds. 0 = disabled.",
    )
    log_rate_limit_max: int = Field(
        default=10,
        ge=0,
        description="Max same-event logs per window. 0 = unlimited.",
    )
    log_sampling_rate: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Hot path log sampling rate (0.0-1.0). 1.0 = all logs pass.",
    )
    log_sampling_events: str = Field(
        default="",
        description=(
            "Comma-separated event names to apply sampling. "
            "Empty = apply to all DEBUG/INFO logs."
        ),
    )

    @field_validator(
        "dlq_log_level",
        "circuit_breaker_log_level",
        "replay_log_level",
        "sla_log_level",
        "forensic_log_level",
        "emergency_log_level",
        "chaos_log_level",
        "l2_storage_log_level",
    )
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate log level is one of the valid options."""
        v_upper = v.upper()
        if v_upper not in VALID_LOG_LEVELS:
            raise ValueError(
                f"Invalid log level '{v}'. Must be one of: {VALID_LOG_LEVELS}"
            )
        return v_upper


# Singleton instance (cached)
_settings: LoggingSettings | None = None


def get_logging_settings() -> LoggingSettings:
    """Get cached LoggingSettings instance."""
    global _settings
    if _settings is None:
        _settings = LoggingSettings()
    return _settings


def reset_logging_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
