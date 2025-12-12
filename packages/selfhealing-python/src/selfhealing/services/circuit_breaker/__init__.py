"""
Circuit Breaker Module

Provides circuit breaker functionality for external service protection.

Features:
- Toggle-based circuit breaker (not automatic failure counting)
- Manual force open/close by operators
- Conditional replay trigger when circuit breaker closes
- Rate limit cascade detection (auto-open CB on 429 storm)
- Self-DDoS protection (prevent retry amplification)

Structure:
- config.py: Configuration and types (~125 lines)
- rate_limit_tracker.py: Rate limit tracking (~95 lines)
- protection.py: Protection mixin (~250 lines)
- manual_control.py: Manual control mixin (~350 lines)
- service.py: Main service class (~285 lines)
- convenience.py: Module-level functions (~135 lines)

Usage:
    from selfhealing.services.circuit_breaker import (
        CircuitBreakerService,
        CircuitBreakerConfig,
        CircuitBreakerResult,
        CircuitState,
        should_allow_request,
        force_open_circuit,
        force_close_circuit,
    )
"""

# Config and types
from .config import (
    CircuitBreakerConfig,
    CircuitBreakerResult,
    CircuitState,
)

# Rate limit tracking
from .rate_limit_tracker import (
    RateLimitTracker,
    get_rate_limit_tracker,
)

# Mixins (for extension)
from .protection import ProtectionMixin
from .manual_control import ManualControlMixin

# Main service
from .service import CircuitBreakerService

# Convenience functions
from .convenience import (
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
