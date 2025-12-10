"""
Interface definitions for the self-healing system.

This module contains abstract base classes that define contracts
for repository implementations (Repository Pattern).
"""

from selfhealing.interfaces.repositories import (
    FailedOperationRepository,
    CircuitBreakerStateRepository,
    SecurityIncidentRepository,
)

__all__ = [
    "FailedOperationRepository",
    "CircuitBreakerStateRepository",
    "SecurityIncidentRepository",
]
