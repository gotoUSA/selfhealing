"""
Self-Healing Reliability Layer for Python Applications

A framework-agnostic library providing circuit breaker, dead letter queue,
retry mechanisms, and automatic recovery for distributed systems.
"""

__version__ = "0.1.0"
__author__ = "SelfHealing Contributors"

from selfhealing.core.types import (
    CircuitState,
    FailureType,
    OperationStatus,
)

__all__ = [
    "__version__",
    "FailureType",
    "OperationStatus",
    "CircuitState",
]
