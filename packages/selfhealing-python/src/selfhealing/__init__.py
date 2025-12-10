"""
Self-Healing Reliability Layer for Python Applications

A framework-agnostic library providing circuit breaker, dead letter queue,
retry mechanisms, and automatic recovery for distributed systems.
"""

__version__ = "0.1.0"
__author__ = "Shopping Mall Team"

from selfhealing.core.types import (
    FailureType,
    OperationStatus,
    CircuitState,
)

__all__ = [
    "__version__",
    "FailureType",
    "OperationStatus",
    "CircuitState",
]
