"""
Self-Healing Reliability Layer for Python Applications

A framework-agnostic library providing circuit breaker, dead letter queue,
retry mechanisms, and automatic recovery for distributed systems.
"""

__version__ = "0.1.0"
__author__ = "SelfHealing Contributors"

from selfhealing.interfaces.repositories import (
    CircuitBreakerStateEnum as CircuitState,
)

__all__ = [
    "__version__",
    "CircuitState",
]
