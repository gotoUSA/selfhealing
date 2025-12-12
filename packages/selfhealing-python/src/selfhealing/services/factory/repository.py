"""
Repository Factory Functions.

Provides centralized creation of repository adapters.
Returns Django adapter by default.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selfhealing.interfaces.repositories import (
        FailedOperationRepository,
        CircuitBreakerStateRepository,
        SecurityIncidentRepository,
    )


def create_failed_operation_repository() -> "FailedOperationRepository":
    """
    Create a FailedOperationRepository instance.

    Returns Django adapter by default.

    Returns:
        FailedOperationRepository implementation
    """
    from selfhealing.adapters.django_repositories import DjangoFailedOperationRepository

    return DjangoFailedOperationRepository()


def create_circuit_breaker_repository() -> "CircuitBreakerStateRepository":
    """
    Create a CircuitBreakerStateRepository instance.

    Returns Django adapter by default.

    Returns:
        CircuitBreakerStateRepository implementation
    """
    from selfhealing.adapters.django_repositories import DjangoCircuitBreakerStateRepository

    return DjangoCircuitBreakerStateRepository()


def create_security_incident_repository() -> "SecurityIncidentRepository":
    """
    Create a SecurityIncidentRepository instance.

    Returns Django adapter by default.

    Returns:
        SecurityIncidentRepository implementation
    """
    from selfhealing.adapters.django_repositories import DjangoSecurityIncidentRepository

    return DjangoSecurityIncidentRepository()
