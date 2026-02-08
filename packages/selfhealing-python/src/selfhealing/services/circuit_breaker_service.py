"""
Circuit Breaker Service - Compatibility Module

This module re-exports all symbols from the circuit_breaker package
for backward compatibility with existing imports.

.. deprecated:: 2.0.0
    Import from ``selfhealing.services.circuit_breaker`` instead.
    This shim will be removed in v3.0.0.

The actual implementation has been split into:
- circuit_breaker/config.py: Configuration and types
- circuit_breaker/rate_limit_tracker.py: Rate limit tracking
- circuit_breaker/service.py: Main service class
- circuit_breaker/convenience.py: Module-level functions

Usage (unchanged):
    from selfhealing.services.circuit_breaker_service import (
        CircuitBreakerService,
        CircuitBreakerConfig,
        CircuitBreakerResult,
        CircuitState,
        RateLimitTracker,
        get_rate_limit_tracker,
        get_circuit_breaker_service,
        should_allow_request,
        force_open_circuit,
        force_close_circuit,
        record_rate_limit,
        should_allow_with_protection,
        get_protection_status,
    )
"""

import warnings

warnings.warn(
    "Importing from 'selfhealing.services.circuit_breaker_service' is deprecated. "
    "Use 'selfhealing.services.circuit_breaker' instead. "
    "This module will be removed in v3.0.0.",
    DeprecationWarning,
    stacklevel=2,
)

# Config and types
from selfhealing.services.circuit_breaker.config import (
    CircuitBreakerConfig,
    CircuitBreakerResult,
    CircuitState,
)

# Convenience functions
from selfhealing.services.circuit_breaker.convenience import (
    force_close_circuit,
    force_open_circuit,
    get_circuit_breaker_service,
    get_protection_status,
    record_rate_limit,
    should_allow_request,
    should_allow_with_protection,
)

# Rate limit tracking
from selfhealing.services.circuit_breaker.rate_limit_tracker import (
    RateLimitTracker,
    get_rate_limit_tracker,
)

# Main service
from selfhealing.services.circuit_breaker.service import CircuitBreakerService

__all__ = [
    # Config and types
    "CircuitBreakerConfig",
    "CircuitBreakerResult",
    "CircuitState",
    # Rate limit tracking
    "RateLimitTracker",
    "get_rate_limit_tracker",
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
