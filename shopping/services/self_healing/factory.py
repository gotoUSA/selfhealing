"""
Service Factory for Self-Healing Components

Provides centralized creation of self-healing services with proper
dependency injection. This factory pattern allows:

1. Easy testing with mock repositories
2. Consistent service instantiation
3. Future-proof for package extraction

Reference: docs/SELF_HEALING_EXTRACTION_WORK_PLAN.md Sprint 6
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .interfaces.repositories import (
        FailedOperationRepository,
        CircuitBreakerStateRepository,
        SecurityIncidentRepository,
    )

logger = logging.getLogger(__name__)


# =============================================================================
# Repository Factory Functions
# =============================================================================


def create_failed_operation_repository() -> "FailedOperationRepository":
    """
    Create a FailedOperationRepository instance.

    Returns Django adapter by default.

    Returns:
        FailedOperationRepository implementation
    """
    from .adapters.django_repositories import DjangoFailedOperationRepository

    return DjangoFailedOperationRepository()


def create_circuit_breaker_repository() -> "CircuitBreakerStateRepository":
    """
    Create a CircuitBreakerStateRepository instance.

    Returns Django adapter by default.

    Returns:
        CircuitBreakerStateRepository implementation
    """
    from .adapters.django_repositories import DjangoCircuitBreakerStateRepository

    return DjangoCircuitBreakerStateRepository()


def create_security_incident_repository() -> "SecurityIncidentRepository":
    """
    Create a SecurityIncidentRepository instance.

    Returns Django adapter by default.

    Returns:
        SecurityIncidentRepository implementation
    """
    from .adapters.django_repositories import DjangoSecurityIncidentRepository

    return DjangoSecurityIncidentRepository()


# =============================================================================
# Service Factory Functions
# =============================================================================


def create_dlq_service(
    repository: "FailedOperationRepository | None" = None,
):
    """
    Create a DLQService instance with optional repository injection.

    Args:
        repository: Optional FailedOperationRepository for testing

    Returns:
        DLQService instance
    """
    from .dlq_service import DLQService

    return DLQService(repository=repository)


def create_replay_service(
    failed_operation_repository: "FailedOperationRepository | None" = None,
):
    """
    Create a ReplayService instance with optional repository injection.

    Args:
        failed_operation_repository: Optional repository for testing

    Returns:
        ReplayService instance
    """
    from .replay_service import ReplayService

    return ReplayService(repository=failed_operation_repository)


def create_circuit_breaker_service(
    repository: "CircuitBreakerStateRepository | None" = None,
):
    """
    Create a CircuitBreakerService instance with optional repository injection.

    Args:
        repository: Optional CircuitBreakerStateRepository for testing

    Returns:
        CircuitBreakerService instance
    """
    from .circuit_breaker_service import CircuitBreakerService

    return CircuitBreakerService(repository=repository)


def create_security_violation_service(
    repository: "SecurityIncidentRepository | None" = None,
):
    """
    Create a SecurityViolationService instance with optional repository injection.

    Args:
        repository: Optional SecurityIncidentRepository for testing

    Returns:
        SecurityViolationService instance
    """
    from .security_violation_service import SecurityViolationService

    return SecurityViolationService(repository=repository)


# =============================================================================
# Singleton Service Accessors (with DI support)
# =============================================================================

# Service singletons - use these for production
_dlq_service_instance = None
_replay_service_instance = None
_circuit_breaker_service_instance = None
_security_violation_service_instance = None


def get_dlq_service_with_di():
    """
    Get or create the DLQ service singleton with DI.

    For production use. For testing, use create_dlq_service() directly.

    Returns:
        DLQService instance
    """
    global _dlq_service_instance
    if _dlq_service_instance is None:
        _dlq_service_instance = create_dlq_service()
    return _dlq_service_instance


def get_replay_service_with_di():
    """
    Get or create the Replay service singleton with DI.

    Returns:
        ReplayService instance
    """
    global _replay_service_instance
    if _replay_service_instance is None:
        _replay_service_instance = create_replay_service()
    return _replay_service_instance


def get_circuit_breaker_service_with_di():
    """
    Get or create the CircuitBreaker service singleton with DI.

    Returns:
        CircuitBreakerService instance
    """
    global _circuit_breaker_service_instance
    if _circuit_breaker_service_instance is None:
        _circuit_breaker_service_instance = create_circuit_breaker_service()
    return _circuit_breaker_service_instance


def get_security_violation_service_with_di():
    """
    Get or create the SecurityViolation service singleton with DI.

    Returns:
        SecurityViolationService instance
    """
    global _security_violation_service_instance
    if _security_violation_service_instance is None:
        _security_violation_service_instance = create_security_violation_service()
    return _security_violation_service_instance


def reset_service_singletons():
    """
    Reset all service singletons.

    Use this in tests to ensure clean state between test cases.
    """
    global _dlq_service_instance
    global _replay_service_instance
    global _circuit_breaker_service_instance
    global _security_violation_service_instance

    _dlq_service_instance = None
    _replay_service_instance = None
    _circuit_breaker_service_instance = None
    _security_violation_service_instance = None

    logger.debug("[Factory] Reset all service singletons")
