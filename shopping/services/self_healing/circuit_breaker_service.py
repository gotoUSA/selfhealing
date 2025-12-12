"""
Circuit Breaker Service - Compatibility Module

.. deprecated:: 0.1.0
    This module is deprecated. Please use the standalone `selfhealing` package.

    Before:
        from shopping.services.self_healing.circuit_breaker_service import (
            CircuitBreakerService,
            CircuitBreakerConfig,
        )

    After:
        from selfhealing.services.circuit_breaker import (
            CircuitBreakerService,
            CircuitBreakerConfig,
        )

This module re-exports all symbols from the selfhealing package
for backward compatibility with existing imports.
"""

import warnings

warnings.warn(
    "Importing from 'shopping.services.self_healing.circuit_breaker_service' is "
    "deprecated. Please use 'selfhealing.services.circuit_breaker' instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from selfhealing package
from selfhealing.services.circuit_breaker import (
    # Config and types
    CircuitBreakerConfig,
    CircuitBreakerResult,
    CircuitState,
    # Rate limit tracking
    RateLimitTracker,
    get_rate_limit_tracker,
    # Mixins
    ProtectionMixin,
    ManualControlMixin,
    # Main service
    CircuitBreakerService,
    # Convenience functions
    get_circuit_breaker_service,
    should_allow_request,
    force_open_circuit,
    force_close_circuit,
    record_rate_limit,
    should_allow_with_protection,
    get_protection_status,
)

__all__ = [
    # Config and types
    "CircuitBreakerConfig",
    "CircuitBreakerResult",
    "CircuitState",
    # Rate limit tracking
    "RateLimitTracker",
    "get_rate_limit_tracker",
    # Mixins
    "ProtectionMixin",
    "ManualControlMixin",
    # Main service
    "CircuitBreakerService",
    # Convenience functions
    "get_circuit_breaker_service",
    "should_allow_request",
    "force_open_circuit",
    "force_close_circuit",
    "record_rate_limit",
    "should_allow_with_protection",
    "get_protection_status",
]
