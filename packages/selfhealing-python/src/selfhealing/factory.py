"""
Provider Registry and Factory for the Self-Healing System.

This module implements a centralized registry for all pluggable components,
allowing runtime registration and lookup of adapters.

Usage:
    from selfhealing.factory import ProviderRegistry

    # Get default providers
    cache = ProviderRegistry.get_cache()
    queue = ProviderRegistry.get_queue()
    payment = ProviderRegistry.get_payment()

    # Get specific providers
    cache = ProviderRegistry.get_cache("redis")
    queue = ProviderRegistry.get_queue("sync")

    # Register custom provider
    ProviderRegistry.register_cache("custom", CustomCacheAdapter)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional, Type

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


class ProviderRegistry:
    """
    Central registry for all pluggable components.

    Allows runtime registration and lookup of adapters.
    Thread-safe for read operations.
    """

    # Provider registries
    _payment_providers: dict[str, Type] = {}
    _cache_providers: dict[str, Type] = {}
    _task_queues: dict[str, Type] = {}
    _failed_op_repos: dict[str, Type] = {}
    _circuit_breaker_repos: dict[str, Type] = {}
    _security_repos: dict[str, Type] = {}

    # Default provider names
    _default_payment: str = "mock"
    _default_cache: str = "memory"
    _default_queue: str = "sync"
    _default_repo: str = "django"

    # Singleton instances (for reuse)
    _instances: dict[str, object] = {}

    # =========================================================================
    # Registration Methods
    # =========================================================================

    @classmethod
    def register_payment(cls, name: str, provider_class: Type) -> None:
        """Register a payment provider adapter."""
        cls._payment_providers[name] = provider_class
        logger.debug(f"[Registry] Registered payment provider: {name}")

    @classmethod
    def register_cache(cls, name: str, provider_class: Type) -> None:
        """Register a cache provider adapter."""
        cls._cache_providers[name] = provider_class
        logger.debug(f"[Registry] Registered cache provider: {name}")

    @classmethod
    def register_queue(cls, name: str, provider_class: Type) -> None:
        """Register a task queue adapter."""
        cls._task_queues[name] = provider_class
        logger.debug(f"[Registry] Registered task queue: {name}")

    @classmethod
    def register_failed_operation_repo(cls, name: str, repo_class: Type) -> None:
        """Register a failed operation repository."""
        cls._failed_op_repos[name] = repo_class
        logger.debug(f"[Registry] Registered failed operation repo: {name}")

    @classmethod
    def register_circuit_breaker_repo(cls, name: str, repo_class: Type) -> None:
        """Register a circuit breaker state repository."""
        cls._circuit_breaker_repos[name] = repo_class
        logger.debug(f"[Registry] Registered circuit breaker repo: {name}")

    @classmethod
    def register_security_repo(cls, name: str, repo_class: Type) -> None:
        """Register a security incident repository."""
        cls._security_repos[name] = repo_class
        logger.debug(f"[Registry] Registered security repo: {name}")

    # =========================================================================
    # Provider Getters
    # =========================================================================

    @classmethod
    def get_payment(
        cls,
        name: Optional[str] = None,
        singleton: bool = True,
    ) -> "PaymentProviderInterface":
        """
        Get payment provider instance.

        Args:
            name: Provider name (e.g., 'toss', 'stripe', 'mock')
            singleton: If True, return cached instance

        Returns:
            PaymentProviderInterface instance
        """
        name = name or cls._default_payment

        if singleton:
            key = f"payment:{name}"
            if key in cls._instances:
                return cls._instances[key]

        if name not in cls._payment_providers:
            raise ValueError(f"Unknown payment provider: {name}. " f"Available: {list(cls._payment_providers.keys())}")

        instance = cls._payment_providers[name]()

        if singleton:
            cls._instances[key] = instance

        return instance

    @classmethod
    def get_cache(
        cls,
        name: Optional[str] = None,
        singleton: bool = True,
    ) -> "CacheProviderInterface":
        """
        Get cache provider instance.

        Args:
            name: Provider name (e.g., 'redis', 'memory')
            singleton: If True, return cached instance

        Returns:
            CacheProviderInterface instance
        """
        name = name or cls._default_cache

        if singleton:
            key = f"cache:{name}"
            if key in cls._instances:
                return cls._instances[key]

        if name not in cls._cache_providers:
            raise ValueError(f"Unknown cache provider: {name}. " f"Available: {list(cls._cache_providers.keys())}")

        instance = cls._cache_providers[name]()

        if singleton:
            cls._instances[key] = instance

        return instance

    @classmethod
    def get_queue(
        cls,
        name: Optional[str] = None,
        singleton: bool = True,
    ) -> "TaskQueueInterface":
        """
        Get task queue instance.

        Args:
            name: Provider name (e.g., 'celery', 'sync', 'rq')
            singleton: If True, return cached instance

        Returns:
            TaskQueueInterface instance
        """
        name = name or cls._default_queue

        if singleton:
            key = f"queue:{name}"
            if key in cls._instances:
                return cls._instances[key]

        if name not in cls._task_queues:
            raise ValueError(f"Unknown task queue: {name}. " f"Available: {list(cls._task_queues.keys())}")

        instance = cls._task_queues[name]()

        if singleton:
            cls._instances[key] = instance

        return instance

    @classmethod
    def get_failed_operation_repo(
        cls,
        name: Optional[str] = None,
        singleton: bool = True,
    ) -> "FailedOperationRepository":
        """Get failed operation repository instance."""
        name = name or cls._default_repo

        if singleton:
            key = f"repo:failed_op:{name}"
            if key in cls._instances:
                return cls._instances[key]

        if name not in cls._failed_op_repos:
            raise ValueError(f"Unknown repository: {name}. " f"Available: {list(cls._failed_op_repos.keys())}")

        instance = cls._failed_op_repos[name]()

        if singleton:
            cls._instances[key] = instance

        return instance

    @classmethod
    def get_circuit_breaker_repo(
        cls,
        name: Optional[str] = None,
        singleton: bool = True,
    ) -> "CircuitBreakerStateRepository":
        """Get circuit breaker state repository instance."""
        name = name or cls._default_repo

        if singleton:
            key = f"repo:circuit_breaker:{name}"
            if key in cls._instances:
                return cls._instances[key]

        if name not in cls._circuit_breaker_repos:
            raise ValueError(f"Unknown repository: {name}. " f"Available: {list(cls._circuit_breaker_repos.keys())}")

        instance = cls._circuit_breaker_repos[name]()

        if singleton:
            cls._instances[key] = instance

        return instance

    @classmethod
    def get_security_repo(
        cls,
        name: Optional[str] = None,
        singleton: bool = True,
    ) -> "SecurityIncidentRepository":
        """Get security incident repository instance."""
        name = name or cls._default_repo

        if singleton:
            key = f"repo:security:{name}"
            if key in cls._instances:
                return cls._instances[key]

        if name not in cls._security_repos:
            raise ValueError(f"Unknown repository: {name}. " f"Available: {list(cls._security_repos.keys())}")

        instance = cls._security_repos[name]()

        if singleton:
            cls._instances[key] = instance

        return instance

    # =========================================================================
    # Configuration Methods
    # =========================================================================

    @classmethod
    def set_defaults(
        cls,
        payment: Optional[str] = None,
        cache: Optional[str] = None,
        queue: Optional[str] = None,
        repo: Optional[str] = None,
    ) -> None:
        """
        Set default providers.

        Args:
            payment: Default payment provider name
            cache: Default cache provider name
            queue: Default task queue name
            repo: Default repository name
        """
        if payment:
            cls._default_payment = payment
        if cache:
            cls._default_cache = cache
        if queue:
            cls._default_queue = queue
        if repo:
            cls._default_repo = repo

        logger.info(
            f"[Registry] Defaults updated: "
            f"payment={cls._default_payment}, cache={cls._default_cache}, "
            f"queue={cls._default_queue}, repo={cls._default_repo}"
        )

    @classmethod
    def get_defaults(cls) -> dict[str, str]:
        """Get current default provider names."""
        return {
            "payment": cls._default_payment,
            "cache": cls._default_cache,
            "queue": cls._default_queue,
            "repo": cls._default_repo,
        }

    @classmethod
    def list_providers(cls) -> dict[str, list[str]]:
        """List all registered providers."""
        return {
            "payment": list(cls._payment_providers.keys()),
            "cache": list(cls._cache_providers.keys()),
            "queue": list(cls._task_queues.keys()),
            "failed_operation_repo": list(cls._failed_op_repos.keys()),
            "circuit_breaker_repo": list(cls._circuit_breaker_repos.keys()),
            "security_repo": list(cls._security_repos.keys()),
        }

    @classmethod
    def clear_instances(cls) -> None:
        """Clear all cached instances (useful for testing)."""
        cls._instances.clear()
        logger.debug("[Registry] Cleared all cached instances")

    @classmethod
    def reset(cls) -> None:
        """Reset registry to initial state (useful for testing)."""
        cls._instances.clear()
        cls._payment_providers.clear()
        cls._cache_providers.clear()
        cls._task_queues.clear()
        cls._failed_op_repos.clear()
        cls._circuit_breaker_repos.clear()
        cls._security_repos.clear()
        cls._default_payment = "mock"
        cls._default_cache = "memory"
        cls._default_queue = "sync"
        cls._default_repo = "django"
        logger.debug("[Registry] Reset to initial state")

    # =========================================================================
    # Health Check
    # =========================================================================

    @classmethod
    def health_check_all(cls) -> dict[str, bool]:
        """
        Run health checks on all default providers.

        Returns:
            Dict mapping provider type to health status
        """
        results = {}

        try:
            payment = cls.get_payment()
            results["payment"] = payment.health_check()
        except Exception as e:
            logger.error(f"[Registry] Payment health check failed: {e}")
            results["payment"] = False

        try:
            cache = cls.get_cache()
            results["cache"] = cache.health_check()
        except Exception as e:
            logger.error(f"[Registry] Cache health check failed: {e}")
            results["cache"] = False

        try:
            queue = cls.get_queue()
            results["queue"] = queue.health_check()
        except Exception as e:
            logger.error(f"[Registry] Queue health check failed: {e}")
            results["queue"] = False

        return results


# =============================================================================
# Auto-registration on import
# =============================================================================


def _auto_register_adapters() -> None:
    """Auto-register available adapters based on installed packages."""

    # Cache providers
    try:
        from selfhealing.adapters.cache.redis_adapter import RedisCacheAdapter

        ProviderRegistry.register_cache("redis", RedisCacheAdapter)
    except ImportError:
        pass

    try:
        from selfhealing.adapters.cache.memory_adapter import InMemoryCacheAdapter

        ProviderRegistry.register_cache("memory", InMemoryCacheAdapter)
    except ImportError:
        pass

    # Task queues
    try:
        from selfhealing.adapters.queues.celery_adapter import CeleryTaskAdapter

        ProviderRegistry.register_queue("celery", CeleryTaskAdapter)
    except ImportError:
        pass

    try:
        from selfhealing.adapters.queues.sync_adapter import SyncTaskAdapter

        ProviderRegistry.register_queue("sync", SyncTaskAdapter)
    except ImportError:
        pass

    # Payment providers
    try:
        from selfhealing.adapters.payments.mock_adapter import MockPaymentAdapter

        ProviderRegistry.register_payment("mock", MockPaymentAdapter)
    except ImportError:
        pass

    # Django repositories
    try:
        from selfhealing.adapters.django.repositories import (
            DjangoFailedOperationRepository,
            DjangoCircuitBreakerStateRepository,
            DjangoSecurityIncidentRepository,
        )

        ProviderRegistry.register_failed_operation_repo("django", DjangoFailedOperationRepository)
        ProviderRegistry.register_circuit_breaker_repo("django", DjangoCircuitBreakerStateRepository)
        ProviderRegistry.register_security_repo("django", DjangoSecurityIncidentRepository)
    except ImportError:
        pass


# Run auto-registration on module import
_auto_register_adapters()
