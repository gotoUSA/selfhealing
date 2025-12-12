"""
Service Factory for Self-Healing Components

Provides centralized creation of self-healing services with proper
dependency injection. This factory pattern allows:

1. Easy testing with mock repositories
2. Consistent service instantiation
3. Future-proof for package extraction
4. Pluggable provider architecture (Phase 2)

Key Components:
    - Repository Factory Functions: Create repository adapters
    - Service Factory Functions: Create service instances with DI
    - ProviderRegistry: Register and retrieve pluggable adapters
    - ServiceFactory: Framework-aware service creation
    - Singleton Accessors: Manage service lifecycle

Reference:
    - docs/SELF_HEALING_EXTRACTION_WORK_PLAN.md Sprint 6
    - docs/PLUGGABLE_ARCHITECTURE.md Section 5
    - docs/STAGE_28_PACKAGE_CLEANUP.md Phase 28-1
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING, Optional, Dict, Type, Any

if TYPE_CHECKING:
    from selfhealing.interfaces.repositories import (
        FailedOperationRepository,
        CircuitBreakerStateRepository,
        SecurityIncidentRepository,
    )
    from selfhealing.interfaces.payment_provider import PaymentProviderInterface
    from selfhealing.interfaces.cache_provider import CacheProviderInterface
    from selfhealing.interfaces.task_queue import TaskQueueInterface

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


# =============================================================================
# Provider Registry for Pluggable Architecture (Phase 2)
# =============================================================================


class ProviderRegistry:
    """
    Central registry for all pluggable components.

    Allows runtime registration and retrieval of adapter implementations.
    Supports payment providers, cache backends, and task queues.

    Usage:
        >>> # Register adapters
        >>> ProviderRegistry.register_payment("toss", TossPaymentAdapter)
        >>> ProviderRegistry.register_cache("redis", RedisCacheAdapter)
        >>>
        >>> # Set defaults
        >>> ProviderRegistry.set_defaults(payment="toss", cache="redis")
        >>>
        >>> # Get instances
        >>> payment = ProviderRegistry.get_payment()
        >>> cache = ProviderRegistry.get_cache()

    Testing Example:
        >>> # Use mock adapters for testing
        >>> ProviderRegistry.register_payment("mock", MockPaymentAdapter)
        >>> ProviderRegistry.register_cache("memory", InMemoryCacheAdapter)
        >>> ProviderRegistry.register_queue("sync", SyncTaskAdapter)
        >>> ProviderRegistry.set_defaults(
        ...     payment="mock",
        ...     cache="memory",
        ...     queue="sync",
        ... )
    """

    # Provider registries
    _payment_providers: Dict[str, Type] = {}
    _cache_providers: Dict[str, Type] = {}
    _task_queues: Dict[str, Type] = {}

    # Provider instances (singletons)
    _payment_instances: Dict[str, Any] = {}
    _cache_instances: Dict[str, Any] = {}
    _queue_instances: Dict[str, Any] = {}

    # Default provider names
    _default_payment: str = "toss"
    _default_cache: str = "redis"
    _default_queue: str = "celery"

    # =========================================================================
    # Payment Provider Registry
    # =========================================================================

    @classmethod
    def register_payment(cls, name: str, provider_class: Type) -> None:
        """
        Register a payment provider adapter.

        Args:
            name: Provider identifier (e.g., 'toss', 'stripe')
            provider_class: Adapter class implementing PaymentProviderInterface
        """
        cls._payment_providers[name] = provider_class
        logger.info(f"[ProviderRegistry] Registered payment provider: {name}")

    @classmethod
    def get_payment(
        cls,
        name: Optional[str] = None,
        force_new: bool = False,
        **kwargs,
    ) -> "PaymentProviderInterface":
        """
        Get a payment provider instance.

        Args:
            name: Provider name (uses default if None)
            force_new: Create new instance instead of using cached
            **kwargs: Arguments passed to provider constructor

        Returns:
            PaymentProviderInterface implementation

        Raises:
            ValueError: If provider is not registered
        """
        name = name or cls._default_payment

        if name not in cls._payment_providers:
            # Auto-register known adapters
            cls._auto_register_payment_adapters()
            if name not in cls._payment_providers:
                raise ValueError(f"Unknown payment provider: {name}")

        if force_new or name not in cls._payment_instances:
            cls._payment_instances[name] = cls._payment_providers[name](**kwargs)

        return cls._payment_instances[name]

    # =========================================================================
    # Cache Provider Registry
    # =========================================================================

    @classmethod
    def register_cache(cls, name: str, provider_class: Type) -> None:
        """
        Register a cache provider adapter.

        Args:
            name: Provider identifier (e.g., 'redis', 'memory')
            provider_class: Adapter class implementing CacheProviderInterface
        """
        cls._cache_providers[name] = provider_class
        logger.info(f"[ProviderRegistry] Registered cache provider: {name}")

    @classmethod
    def get_cache(
        cls,
        name: Optional[str] = None,
        force_new: bool = False,
        **kwargs,
    ) -> "CacheProviderInterface":
        """
        Get a cache provider instance.

        Args:
            name: Provider name (uses default if None)
            force_new: Create new instance instead of using cached
            **kwargs: Arguments passed to provider constructor

        Returns:
            CacheProviderInterface implementation

        Raises:
            ValueError: If provider is not registered
        """
        name = name or cls._default_cache

        if name not in cls._cache_providers:
            cls._auto_register_cache_adapters()
            if name not in cls._cache_providers:
                raise ValueError(f"Unknown cache provider: {name}")

        if force_new or name not in cls._cache_instances:
            cls._cache_instances[name] = cls._cache_providers[name](**kwargs)

        return cls._cache_instances[name]

    # =========================================================================
    # Task Queue Registry
    # =========================================================================

    @classmethod
    def register_queue(cls, name: str, provider_class: Type) -> None:
        """
        Register a task queue adapter.

        Args:
            name: Provider identifier (e.g., 'celery', 'sync')
            provider_class: Adapter class implementing TaskQueueInterface
        """
        cls._task_queues[name] = provider_class
        logger.info(f"[ProviderRegistry] Registered task queue: {name}")

    @classmethod
    def get_queue(
        cls,
        name: Optional[str] = None,
        force_new: bool = False,
        **kwargs,
    ) -> "TaskQueueInterface":
        """
        Get a task queue instance.

        Args:
            name: Provider name (uses default if None)
            force_new: Create new instance instead of using cached
            **kwargs: Arguments passed to provider constructor

        Returns:
            TaskQueueInterface implementation

        Raises:
            ValueError: If provider is not registered
        """
        name = name or cls._default_queue

        if name not in cls._task_queues:
            cls._auto_register_queue_adapters()
            if name not in cls._task_queues:
                raise ValueError(f"Unknown task queue: {name}")

        if force_new or name not in cls._queue_instances:
            cls._queue_instances[name] = cls._task_queues[name](**kwargs)

        return cls._queue_instances[name]

    # =========================================================================
    # Configuration
    # =========================================================================

    @classmethod
    def set_defaults(
        cls,
        payment: Optional[str] = None,
        cache: Optional[str] = None,
        queue: Optional[str] = None,
    ) -> None:
        """
        Set default providers.

        Args:
            payment: Default payment provider name
            cache: Default cache provider name
            queue: Default task queue name
        """
        if payment:
            cls._default_payment = payment
            logger.debug(f"[ProviderRegistry] Default payment: {payment}")
        if cache:
            cls._default_cache = cache
            logger.debug(f"[ProviderRegistry] Default cache: {cache}")
        if queue:
            cls._default_queue = queue
            logger.debug(f"[ProviderRegistry] Default queue: {queue}")

    @classmethod
    def list_providers(cls) -> Dict[str, list]:
        """
        List all registered providers.

        Returns:
            Dict with lists of registered provider names by type
        """
        return {
            "payment": list(cls._payment_providers.keys()),
            "cache": list(cls._cache_providers.keys()),
            "queue": list(cls._task_queues.keys()),
        }

    @classmethod
    def get_defaults(cls) -> Dict[str, str]:
        """
        Get current default providers.

        Returns:
            Dict with current default provider names
        """
        return {
            "payment": cls._default_payment,
            "cache": cls._default_cache,
            "queue": cls._default_queue,
        }

    # =========================================================================
    # Health Check Aggregation
    # =========================================================================

    @classmethod
    def health_check_all(cls) -> Dict[str, bool]:
        """
        Perform health check on all providers.

        Returns:
            Dict mapping provider type to health status

        Example:
            >>> results = ProviderRegistry.health_check_all()
            >>> # {'payment': True, 'cache': True, 'queue': False}
        """
        results = {}

        try:
            payment = cls.get_payment()
            results["payment"] = payment.health_check()
        except Exception as e:
            logger.error(f"[ProviderRegistry] Payment health check failed: {e}")
            results["payment"] = False

        try:
            cache = cls.get_cache()
            results["cache"] = cache.health_check()
        except Exception as e:
            logger.error(f"[ProviderRegistry] Cache health check failed: {e}")
            results["cache"] = False

        try:
            queue = cls.get_queue()
            results["queue"] = queue.health_check()
        except Exception as e:
            logger.error(f"[ProviderRegistry] Queue health check failed: {e}")
            results["queue"] = False

        return results

    @classmethod
    def is_healthy(cls) -> bool:
        """
        Check if all providers are healthy.

        Returns:
            True if all providers are healthy
        """
        results = cls.health_check_all()
        return all(results.values())

    @classmethod
    def get_health_summary(cls) -> Dict[str, Any]:
        """
        Get detailed health summary for monitoring.

        Returns:
            Dict with health status, provider info, and defaults
        """
        health = cls.health_check_all()
        return {
            "healthy": all(health.values()),
            "providers": health,
            "defaults": cls.get_defaults(),
            "registered": cls.list_providers(),
        }

    # =========================================================================
    # Auto-Registration
    # =========================================================================

    @classmethod
    def _auto_register_payment_adapters(cls) -> None:
        """Auto-register available payment adapters."""
        # Try selfhealing package first
        try:
            from selfhealing.adapters.payments import MockPaymentAdapter

            if "mock" not in cls._payment_providers:
                cls.register_payment("mock", MockPaymentAdapter)
        except ImportError:
            pass

        # Then try local adapters
        try:
            from .adapters.payments.toss_adapter import TossPaymentAdapter

            if "toss" not in cls._payment_providers:
                cls.register_payment("toss", TossPaymentAdapter)
        except ImportError:
            pass

        try:
            from .adapters.payments.mock_adapter import MockPaymentAdapter

            if "mock" not in cls._payment_providers:
                cls.register_payment("mock", MockPaymentAdapter)
        except ImportError:
            pass

    @classmethod
    def _auto_register_cache_adapters(cls) -> None:
        """Auto-register available cache adapters."""
        # Try selfhealing package first
        try:
            from selfhealing.adapters.cache import RedisCacheAdapter, InMemoryCacheAdapter

            if "redis" not in cls._cache_providers:
                cls.register_cache("redis", RedisCacheAdapter)
            if "memory" not in cls._cache_providers:
                cls.register_cache("memory", InMemoryCacheAdapter)
        except ImportError:
            pass

        # Then try local adapters
        try:
            from .adapters.cache.redis_adapter import RedisCacheAdapter

            if "redis" not in cls._cache_providers:
                cls.register_cache("redis", RedisCacheAdapter)
        except ImportError:
            pass

        try:
            from .adapters.cache.memory_adapter import InMemoryCacheAdapter

            if "memory" not in cls._cache_providers:
                cls.register_cache("memory", InMemoryCacheAdapter)
        except ImportError:
            pass

    @classmethod
    def _auto_register_queue_adapters(cls) -> None:
        """Auto-register available queue adapters."""
        # Try selfhealing package first
        try:
            from selfhealing.adapters.queues import CeleryTaskAdapter, SyncTaskAdapter

            if "celery" not in cls._task_queues:
                cls.register_queue("celery", CeleryTaskAdapter)
            if "sync" not in cls._task_queues:
                cls.register_queue("sync", SyncTaskAdapter)
        except ImportError:
            pass

        # Then try local adapters
        try:
            from .adapters.queues.celery_adapter import CeleryTaskAdapter

            if "celery" not in cls._task_queues:
                cls.register_queue("celery", CeleryTaskAdapter)
        except ImportError:
            pass

        try:
            from .adapters.queues.sync_adapter import SyncTaskAdapter

            if "sync" not in cls._task_queues:
                cls.register_queue("sync", SyncTaskAdapter)
        except ImportError:
            pass

    # =========================================================================
    # Testing Utilities
    # =========================================================================

    @classmethod
    def reset(cls) -> None:
        """
        Reset all registrations and instances.

        Use in tests to ensure clean state.
        """
        cls._payment_providers.clear()
        cls._cache_providers.clear()
        cls._task_queues.clear()
        cls._payment_instances.clear()
        cls._cache_instances.clear()
        cls._queue_instances.clear()
        cls._default_payment = "toss"
        cls._default_cache = "redis"
        cls._default_queue = "celery"
        logger.debug("[ProviderRegistry] Reset all registrations")

    @classmethod
    def reset_instances(cls) -> None:
        """
        Reset only instances, keeping registrations.

        Useful for testing with fresh instances.
        """
        cls._payment_instances.clear()
        cls._cache_instances.clear()
        cls._queue_instances.clear()
        logger.debug("[ProviderRegistry] Reset all instances")

    @classmethod
    def configure_for_testing(cls) -> None:
        """
        Configure registry for testing with mock/sync adapters.

        Sets up:
            - MockPaymentAdapter for payments
            - InMemoryCacheAdapter for cache
            - SyncTaskAdapter for tasks
        """
        cls._auto_register_payment_adapters()
        cls._auto_register_cache_adapters()
        cls._auto_register_queue_adapters()

        cls.set_defaults(
            payment="mock",
            cache="memory",
            queue="sync",
        )

        logger.info("[ProviderRegistry] Configured for testing")

    @classmethod
    def configure_for_production(cls) -> None:
        """
        Configure registry for production with real adapters.

        Sets up:
            - TossPaymentAdapter for payments
            - RedisCacheAdapter for cache
            - CeleryTaskAdapter for tasks
        """
        cls._auto_register_payment_adapters()
        cls._auto_register_cache_adapters()
        cls._auto_register_queue_adapters()

        cls.set_defaults(
            payment="toss",
            cache="redis",
            queue="celery",
        )

        logger.info("[ProviderRegistry] Configured for production")


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
