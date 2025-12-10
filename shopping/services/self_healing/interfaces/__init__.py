"""
Self-Healing Interfaces Module

Abstract interfaces for repository pattern implementation.
These interfaces decouple the self-healing core logic from Django ORM,
enabling future extraction to an independent SaaS package.

Usage:
    from shopping.services.self_healing.interfaces import (
        FailedOperationRepository,
        CircuitBreakerStateRepository,
        SecurityIncidentRepository,
    )
"""

from shopping.services.self_healing.interfaces.repositories import (
    # Data Classes
    FailedOperationData,
    CircuitBreakerStateData,
    SecurityIncidentData,
    # Repository Interfaces
    FailedOperationRepository,
    CircuitBreakerStateRepository,
    SecurityIncidentRepository,
)

__all__ = [
    # Data Classes
    "FailedOperationData",
    "CircuitBreakerStateData",
    "SecurityIncidentData",
    # Repository Interfaces
    "FailedOperationRepository",
    "CircuitBreakerStateRepository",
    "SecurityIncidentRepository",
]
