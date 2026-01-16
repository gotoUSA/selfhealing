"""
Pydantic Settings Module for Self-Healing Configuration.

Single Source of Truth for all configuration:
- Default values
- Type definitions
- Validation rules
- Environment variable loading

Replaces:
- core/config.py (dataclass definitions)
- core/safe_defaults.py (SAFE_DEFAULTS, VALIDATION_RULES)

Reference:
- docs/self_healing/middleware_system/40_PYDANTIC_CONFIG_MIGRATION.md
"""

from selfhealing.settings.circuit_breaker import (
    CircuitBreakerSettings,
    get_circuit_breaker_settings,
    reset_circuit_breaker_settings,
)
from selfhealing.settings.dlq import (
    DLQSettings,
    get_dlq_settings,
    reset_dlq_settings,
)
from selfhealing.settings.retry import (
    RetrySettings,
    get_retry_settings,
    reset_retry_settings,
)
from selfhealing.settings.rate_limit import (
    RateLimitSettings,
    get_rate_limit_settings,
    reset_rate_limit_settings,
)
from selfhealing.settings.security import (
    SecuritySettings,
    get_security_settings,
    reset_security_settings,
)

__all__ = [
    # Circuit Breaker
    "CircuitBreakerSettings",
    "get_circuit_breaker_settings",
    "reset_circuit_breaker_settings",
    # DLQ
    "DLQSettings",
    "get_dlq_settings",
    "reset_dlq_settings",
    # Retry
    "RetrySettings",
    "get_retry_settings",
    "reset_retry_settings",
    # Rate Limit
    "RateLimitSettings",
    "get_rate_limit_settings",
    "reset_rate_limit_settings",
    # Security
    "SecuritySettings",
    "get_security_settings",
    "reset_security_settings",
]
