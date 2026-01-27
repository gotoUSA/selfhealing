"""
Service Factory Functions.

Provides factory functions for creating service instances with
optional dependency injection support.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selfhealing.interfaces.repositories import (
        CircuitBreakerStateRepository,
        FailedOperationRepository,
        SecurityIncidentRepository,
    )


def create_dlq_service(
    repository: FailedOperationRepository | None = None,
):
    """
    Create a DLQService instance with optional repository injection.

    Args:
        repository: Optional FailedOperationRepository for testing

    Returns:
        DLQService instance
    """
    from selfhealing.services.dlq_service import DLQService

    return DLQService(repository=repository)


def create_replay_service(
    failed_operation_repository: FailedOperationRepository | None = None,
):
    """
    Create a ReplayService instance with optional repository injection.

    Args:
        failed_operation_repository: Optional repository for testing

    Returns:
        ReplayService instance
    """
    from selfhealing.services.replay_service import ReplayService

    return ReplayService(repository=failed_operation_repository)


def create_circuit_breaker_service(
    repository: CircuitBreakerStateRepository | None = None,
):
    """
    Create a CircuitBreakerService instance with optional repository injection.

    Args:
        repository: Optional CircuitBreakerStateRepository for testing

    Returns:
        CircuitBreakerService instance
    """
    from selfhealing.services.circuit_breaker_service import CircuitBreakerService

    return CircuitBreakerService(repository=repository)


def create_security_violation_service(
    repository: SecurityIncidentRepository | None = None,
):
    """
    Create a SecurityViolationService instance with optional repository injection.

    Args:
        repository: Optional SecurityIncidentRepository for testing

    Returns:
        SecurityViolationService instance
    """
    from selfhealing.services.security import SecurityViolationService

    return SecurityViolationService(repository=repository)
