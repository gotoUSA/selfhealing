"""
Base ServiceFactory for Self-Healing Components.

Provides framework-specific service construction without Django fallbacks
in the services layer.

Usage:
    # Django project
    factory = ServiceFactory(framework=FrameworkType.DJANGO)
    cb_service = factory.create_circuit_breaker_service()

    # FastAPI project
    factory = ServiceFactory(framework=FrameworkType.FASTAPI)
    dlq_service = factory.create_dlq_service()

    # Standalone (testing)
    factory = ServiceFactory(framework=FrameworkType.STANDALONE)
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING, Optional, Dict, Any

if TYPE_CHECKING:
    from selfhealing.interfaces.repositories import (
        FailedOperationRepository,
        CircuitBreakerStateRepository,
        SecurityIncidentRepository,
    )

logger = logging.getLogger(__name__)


# =============================================================================
# Framework Type Enum
# =============================================================================


class FrameworkType(str, Enum):
    """Supported frameworks for adapter selection."""

    DJANGO = "django"
    FASTAPI = "fastapi"
    FLASK = "flask"
    STANDALONE = "standalone"  # In-memory, no framework


# =============================================================================
# Service Factory for Framework-Specific Construction
# =============================================================================


class ServiceFactory:
    """
    Factory for creating self-healing services with proper dependencies.

    Provides framework-specific service construction without Django fallbacks
    in the services layer.

    Usage:
        # Django project
        factory = ServiceFactory(framework=FrameworkType.DJANGO)
        cb_service = factory.create_circuit_breaker_service()

        # FastAPI project
        factory = ServiceFactory(framework=FrameworkType.FASTAPI)
        dlq_service = factory.create_dlq_service()

        # Standalone (testing)
        factory = ServiceFactory(framework=FrameworkType.STANDALONE)
    """

    def __init__(
        self,
        framework: FrameworkType = FrameworkType.STANDALONE,
        custom_repositories: Optional[Dict[str, Any]] = None,
    ):
        self._framework = framework
        self._custom_repos = custom_repositories or {}
        self._repo_cache: Dict[str, Any] = {}

    @property
    def framework(self) -> FrameworkType:
        """Get the current framework type."""
        return self._framework

    def get_failed_operation_repository(self) -> "FailedOperationRepository":
        """Get FailedOperation repository for current framework."""
        if "failed_operation" in self._custom_repos:
            return self._custom_repos["failed_operation"]

        if "failed_operation" in self._repo_cache:
            return self._repo_cache["failed_operation"]

        repo = self._create_repository("failed_operation")
        self._repo_cache["failed_operation"] = repo
        return repo

    def get_circuit_breaker_repository(self) -> "CircuitBreakerStateRepository":
        """Get CircuitBreakerState repository for current framework."""
        if "circuit_breaker" in self._custom_repos:
            return self._custom_repos["circuit_breaker"]

        if "circuit_breaker" in self._repo_cache:
            return self._repo_cache["circuit_breaker"]

        repo = self._create_repository("circuit_breaker")
        self._repo_cache["circuit_breaker"] = repo
        return repo

    def get_security_incident_repository(self) -> "SecurityIncidentRepository":
        """Get SecurityIncident repository for current framework."""
        if "security_incident" in self._custom_repos:
            return self._custom_repos["security_incident"]

        if "security_incident" in self._repo_cache:
            return self._repo_cache["security_incident"]

        repo = self._create_repository("security_incident")
        self._repo_cache["security_incident"] = repo
        return repo

    def _create_repository(self, repo_type: str) -> Any:
        """Create repository based on framework."""
        if self._framework == FrameworkType.DJANGO:
            return self._create_django_repository(repo_type)
        elif self._framework == FrameworkType.FASTAPI:
            return self._create_fastapi_repository(repo_type)
        elif self._framework == FrameworkType.FLASK:
            return self._create_flask_repository(repo_type)
        else:
            return self._create_inmemory_repository(repo_type)

    def _create_django_repository(self, repo_type: str) -> Any:
        """Create Django ORM based repository."""
        from selfhealing.adapters.django.repositories import (
            DjangoFailedOperationRepository,
            DjangoCircuitBreakerStateRepository,
            DjangoSecurityIncidentRepository,
        )

        mapping = {
            "failed_operation": DjangoFailedOperationRepository,
            "circuit_breaker": DjangoCircuitBreakerStateRepository,
            "security_incident": DjangoSecurityIncidentRepository,
        }
        return mapping[repo_type]()

    def _create_fastapi_repository(self, repo_type: str) -> Any:
        """Create SQLAlchemy based repository for FastAPI."""
        # TODO: Implement in Stage 28-3
        # For now, fall back to in-memory
        logger.warning(
            f"[ServiceFactory] FastAPI/SQLAlchemy repository not yet implemented. "
            f"Falling back to in-memory for: {repo_type}"
        )
        return self._create_inmemory_repository(repo_type)

    def _create_flask_repository(self, repo_type: str) -> Any:
        """Create Flask-SQLAlchemy based repository."""
        # Flask also uses SQLAlchemy
        return self._create_fastapi_repository(repo_type)

    def _create_inmemory_repository(self, repo_type: str) -> Any:
        """Create in-memory repository for testing/standalone."""
        from selfhealing.adapters.memory import (
            InMemoryFailedOperationRepository,
            InMemoryCircuitBreakerStateRepository,
            InMemorySecurityIncidentRepository,
        )

        mapping = {
            "failed_operation": InMemoryFailedOperationRepository,
            "circuit_breaker": InMemoryCircuitBreakerStateRepository,
            "security_incident": InMemorySecurityIncidentRepository,
        }
        return mapping[repo_type]()

    # Service creation methods
    def create_circuit_breaker_service(self):
        """Create CircuitBreakerService with proper repository."""
        from selfhealing.services.circuit_breaker_service import CircuitBreakerService

        return CircuitBreakerService(repository=self.get_circuit_breaker_repository())

    def create_dlq_service(self):
        """Create DLQService with proper repository."""
        from selfhealing.services.dlq_service import DLQService

        return DLQService(repository=self.get_failed_operation_repository())

    def create_replay_service(self):
        """Create ReplayService with proper repository."""
        from selfhealing.services.replay_service import ReplayService

        return ReplayService(repository=self.get_failed_operation_repository())

    def create_security_violation_service(self):
        """Create SecurityViolationService with proper repository."""
        from selfhealing.services.security_violation_service import SecurityViolationService

        return SecurityViolationService(repository=self.get_security_incident_repository())

    def reset_cache(self) -> None:
        """Reset repository cache (for testing)."""
        self._repo_cache.clear()


# =============================================================================
# Global Factory Instance Management
# =============================================================================

# Global factory instance
_service_factory: Optional[ServiceFactory] = None


def get_service_factory() -> ServiceFactory:
    """
    Get the global ServiceFactory instance.

    Creates a STANDALONE factory by default.
    Call configure_service_factory() first for framework-specific setup.
    """
    global _service_factory
    if _service_factory is None:
        _service_factory = ServiceFactory(framework=FrameworkType.STANDALONE)
    return _service_factory


def configure_service_factory(
    framework: FrameworkType,
    custom_repositories: Optional[Dict[str, Any]] = None,
) -> ServiceFactory:
    """
    Configure the global ServiceFactory with framework type.

    Call this during app initialization (e.g., Django AppConfig.ready()).

    Args:
        framework: The framework type to use
        custom_repositories: Optional custom repository implementations

    Returns:
        The configured ServiceFactory instance
    """
    global _service_factory
    _service_factory = ServiceFactory(
        framework=framework,
        custom_repositories=custom_repositories,
    )
    logger.info(f"[ServiceFactory] Configured for framework: {framework.value}")
    return _service_factory


def reset_service_factory() -> None:
    """Reset the global ServiceFactory (for testing)."""
    global _service_factory
    _service_factory = None
