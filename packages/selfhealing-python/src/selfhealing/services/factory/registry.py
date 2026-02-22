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

Test Isolation:
    >>> # 테스트에서 Provider 임시 교체
    >>> with ProviderRegistry.override_provider("cache", mock_cache):
    ...     # 이 블록 내에서만 mock_cache 사용
    ...     do_something()
    >>> # 자동 복원
    >>>
    >>> # 완전 격리된 테스트 컨텍스트
    >>> with ProviderRegistry.isolated_test_context() as registry:
    ...     registry.set_defaults(cache="memory", queue="sync")
    ...     # 격리된 환경에서 테스트
    >>> # 자동 복원
"""

from __future__ import annotations

import structlog
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from selfhealing.interfaces.cache_provider import CacheProviderInterface
    from selfhealing.interfaces.task_queue import TaskQueueInterface

logger = structlog.get_logger()


class ServiceProviderRegistry:
    """
    Central registry for all pluggable components.

    Allows runtime registration and retrieval of adapter implementations.
    Supports cache backends and task queues.
    """

    # Provider registries
    _cache_providers: dict[str, type] = {}
    _task_queues: dict[str, type] = {}

    # Provider instances (singletons)
    _cache_instances: dict[str, Any] = {}
    _queue_instances: dict[str, Any] = {}

    # Default provider names
    _default_cache: str = "redis"
    _default_queue: str = "celery"

    # =========================================================================
    # Cache Provider Registry
    # =========================================================================

    @classmethod
    def register_cache(cls, name: str, provider_class: type) -> None:
        """
        Register a cache provider adapter.

        Args:
            name: Provider identifier (e.g., 'redis', 'memory')
            provider_class: Adapter class implementing CacheProviderInterface
        """
        cls._cache_providers[name] = provider_class
        logger.info(
            "cell_registry.bulkheads_registered",
            name=name,
        )

    @classmethod
    def get_cache(
        cls,
        name: str | None = None,
        force_new: bool = False,
        **kwargs,
    ) -> CacheProviderInterface:
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
    def register_queue(cls, name: str, provider_class: type) -> None:
        """
        Register a task queue adapter.

        Args:
            name: Provider identifier (e.g., 'celery', 'sync')
            provider_class: Adapter class implementing TaskQueueInterface
        """
        cls._task_queues[name] = provider_class
        logger.info(
            "cell_registry.bulkheads_registered",
            name=name,
        )

    @classmethod
    def get_queue(
        cls,
        name: str | None = None,
        force_new: bool = False,
        **kwargs,
    ) -> TaskQueueInterface:
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
        cache: str | None = None,
        queue: str | None = None,
    ) -> None:
        """
        Set default providers.

        Args:
            cache: Default cache provider name
            queue: Default task queue name
        """
        if cache:
            cls._default_cache = cache
            logger.debug(
                "service_provider_registry.default_cache",
                cache=cache,
            )
        if queue:
            cls._default_queue = queue
            logger.debug(
                "service_provider_registry.default_queue",
                queue=queue,
            )

    @classmethod
    def list_providers(cls) -> dict[str, list]:
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
    def get_defaults(cls) -> dict[str, str]:
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
    def health_check_all(cls) -> dict[str, bool]:
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
            logger.error(
                "service_provider_registry.cache_health_check_failed",
                error=e,
            )
            results["cache"] = False

        try:
            queue = cls.get_queue()
            results["queue"] = queue.health_check()
        except Exception as e:
            logger.error(
                "service_provider_registry.queue_health_check_failed",
                error=e,
            )
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
    def get_health_summary(cls) -> dict[str, Any]:
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
            from selfhealing.adapters.cache import (
                InMemoryCacheAdapter,
                RedisCacheAdapter,
            )

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
        logger.debug("service_provider_registry.reset_all_registrations")

    @classmethod
    def reset_instances(cls) -> None:
        """
        Reset only instances, keeping registrations.

        Useful for testing with fresh instances.
        """
        cls._cache_instances.clear()
        cls._queue_instances.clear()
        logger.debug("service_provider_registry.reset_all_instances")

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

        logger.info("service_provider_registry.configured_testing")

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

        logger.info("service_provider_registry.configured_production")

    # =========================================================================
    # Test Isolation Context Managers
    # =========================================================================

    @classmethod
    @contextmanager
    def override_provider(cls, provider_type: str, mock_instance: Any) -> Generator[None, None, None]:
        """
        테스트용 Provider 임시 교체 (Context Manager).

        전역 상태를 안전하게 교체하고 자동으로 복원합니다.
        private 속성에 직접 접근하는 대신 이 메서드를 사용하세요.

        Args:
            provider_type: "cache" 또는 "queue"
            mock_instance: Mock 인스턴스

        Usage:
            >>> mock_cache = MagicMock()
            >>> with ProviderRegistry.override_provider("cache", mock_cache):
            ...     # 이 블록 내에서만 mock_cache 사용
            ...     result = ProviderRegistry.get_cache()
            ...     assert result is mock_cache
            >>> # 자동 복원

        Thread Safety:
            이 메서드는 thread-local이 아니므로,
            멀티스레드 테스트에서는 각 테스트가 독립 프로세스에서 실행되어야 합니다.

        Raises:
            ValueError: provider_type이 "cache" 또는 "queue"가 아닌 경우
        """
        if provider_type not in ("cache", "queue"):
            raise ValueError(f"Unknown provider_type: {provider_type}. " f"Must be 'cache' or 'queue'.")

        instances = cls._cache_instances if provider_type == "cache" else cls._queue_instances
        default_name = cls._default_cache if provider_type == "cache" else cls._default_queue

        # 기존 인스턴스 백업
        old_instance = instances.get(default_name)

        # Mock 인스턴스 설정
        instances[default_name] = mock_instance
        logger.debug(
            "service_provider_registry.override",
            provider_type=provider_type,
            value=type(mock_instance).__name__,
        )

        try:
            yield
        finally:
            # 복원
            if old_instance is not None:
                instances[default_name] = old_instance
            else:
                instances.pop(default_name, None)
            logger.debug(
                "service_provider_registry.restored",
                provider_type=provider_type,
            )

    @classmethod
    @contextmanager
    def isolated_test_context(cls) -> Generator[ProviderRegistry, None, None]:
        """
        완전히 격리된 테스트 컨텍스트 제공.

        모든 인스턴스와 기본값을 임시로 교체하고 자동 복원합니다.
        테스트 간 전역 상태 오염을 방지합니다.

        Usage:
            >>> with ProviderRegistry.isolated_test_context() as registry:
            ...     registry.set_defaults(cache="memory", queue="sync")
            ...     # 격리된 환경에서 테스트
            ...     cache = registry.get_cache()
            >>> # 자동 복원 - 기존 상태로 돌아감

        Returns:
            ProviderRegistry 클래스 자체 (메서드 체이닝용)
        """
        # 전체 상태 백업
        old_cache_instances = cls._cache_instances.copy()
        old_queue_instances = cls._queue_instances.copy()
        old_default_cache = cls._default_cache
        old_default_queue = cls._default_queue

        # 초기화 (빈 상태로 시작)
        cls._cache_instances = {}
        cls._queue_instances = {}

        logger.debug("service_provider_registry.entering_isolated_test_context")

        try:
            yield cls
        finally:
            # 복원
            cls._cache_instances = old_cache_instances
            cls._queue_instances = old_queue_instances
            cls._default_cache = old_default_cache
            cls._default_queue = old_default_queue
            logger.debug("service_provider_registry.exited_isolated_test_context")


# 하위 호환 alias (deprecated)
ProviderRegistry = ServiceProviderRegistry
