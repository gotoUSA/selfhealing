"""
Repository Factory Functions.

Provides centralized creation of repository adapters.
Returns Django adapter by default.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selfhealing.interfaces.repositories import (
        CircuitBreakerStateRepository,
        FailedOperationRepository,
        SecurityIncidentRepository,
    )


def create_failed_operation_repository() -> FailedOperationRepository:
    """
    Create a FailedOperationRepository instance.

    Returns InMemory adapter by default (Django adapter not available).

    Returns:
        FailedOperationRepository implementation
    """
    from selfhealing.adapters.memory import InMemoryFailedOperationRepository

    return InMemoryFailedOperationRepository()


def create_circuit_breaker_repository() -> CircuitBreakerStateRepository:
    """
    Create a CircuitBreakerStateRepository instance.

    Returns InMemory adapter by default (Django adapter not available).

    Returns:
        CircuitBreakerStateRepository implementation
    """
    from selfhealing.adapters.memory import InMemoryCircuitBreakerStateRepository

    return InMemoryCircuitBreakerStateRepository()


def create_security_incident_repository() -> SecurityIncidentRepository:
    """
    Create a SecurityIncidentRepository instance.

    Returns InMemory adapter by default (Django adapter not available).

    Returns:
        SecurityIncidentRepository implementation
    """
    from selfhealing.adapters.memory import InMemorySecurityIncidentRepository

    return InMemorySecurityIncidentRepository()
