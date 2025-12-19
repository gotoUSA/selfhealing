"""
Provider Registry for Pluggable Architecture.

Central registry for all pluggable components - cache backends and task queues.

Usage:
    >>> # Register adapters
    >>> ProviderRegistry.register_cache("redis", RedisCacheAdapter)
    >>> ProviderRegistry.register_queue("celery", CeleryQueueAdapter)
    >>>
    >>> # Set defaults
    >>> ProviderRegistry.set_defaults(cache="redis", queue="celery")
    >>>
    >>> # Get instances
    >>> cache = ProviderRegistry.get_cache()
    >>> queue = ProviderRegistry.get_queue()

Testing Example:
    >>> # Use mock adapters for testing
    >>> ProviderRegistry.register_cache("memory", InMemoryCacheAdapter)
    >>> ProviderRegistry.register_queue("sync", SyncTaskAdapter)
    >>> ProviderRegistry.set_defaults(
    ...     cache="memory",
    ...     queue="sync",
    ... )
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional, Dict, Type, Any

if TYPE_CHECKING:
    from selfhealing.interfaces.cache_provider import CacheProviderInterface
    from selfhealing.interfaces.task_queue import TaskQueueInterface

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """
    Central registry for all pluggable components.

    Allows runtime registration and retrieval of adapter implementations.
    Supports cache backends and task queues.
    """

    # Provider registries
    _cache_providers: Dict[str, Type] = {}
    _task_queues: Dict[str, Type] = {}

    # Provider instances (singletons)
    _cache_instances: Dict[str, Any] = {}
    _queue_instances: Dict[str, Any] = {}

    # Default provider names
    _default_cache: str = "redis"
    _default_queue: str = "celery"

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
        cache: Optional[str] = None,
        queue: Optional[str] = None,
    ) -> None:
        """
        Set default providers.

        Args:
            cache: Default cache provider name
            queue: Default task queue name
        """
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
            >>> # {'cache': True, 'queue': False}
        """
        results = {}

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
        cls._cache_providers.clear()
        cls._task_queues.clear()
        cls._cache_instances.clear()
        cls._queue_instances.clear()
        cls._default_cache = "redis"
        cls._default_queue = "celery"
        logger.debug("[ProviderRegistry] Reset all registrations")

    @classmethod
    def reset_instances(cls) -> None:
        """
        Reset only instances, keeping registrations.

        Useful for testing with fresh instances.
        """
        cls._cache_instances.clear()
        cls._queue_instances.clear()
        logger.debug("[ProviderRegistry] Reset all instances")

    @classmethod
    def configure_for_testing(cls) -> None:
        """
        Configure registry for testing with mock/sync adapters.

        Sets up:
            - InMemoryCacheAdapter for cache
            - SyncTaskAdapter for tasks
        """
        cls._auto_register_cache_adapters()
        cls._auto_register_queue_adapters()

        cls.set_defaults(
            cache="memory",
            queue="sync",
        )

        logger.info("[ProviderRegistry] Configured for testing")

    @classmethod
    def configure_for_production(cls) -> None:
        """
        Configure registry for production with real adapters.

        Sets up:
            - RedisCacheAdapter for cache
            - CeleryTaskAdapter for tasks
        """
        cls._auto_register_cache_adapters()
        cls._auto_register_queue_adapters()

        cls.set_defaults(
            cache="redis",
            queue="celery",
        )

        logger.info("[ProviderRegistry] Configured for production")
