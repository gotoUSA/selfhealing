"""
Provider Registry and Factory for the Self-Healing System.

This module implements a centralized registry for all pluggable components,
allowing runtime registration and lookup of adapters.

Usage:
    from selfhealing.factory import ProviderRegistry

    # Get default providers
    cache = ProviderRegistry.get_cache()
    queue = ProviderRegistry.get_queue()

    # Get specific providers
    cache = ProviderRegistry.get_cache("redis")
    queue = ProviderRegistry.get_queue("sync")

    # Register custom provider
    ProviderRegistry.register_cache("custom", CustomCacheAdapter)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from selfhealing.interfaces.audit_adapter import AuditLogAdapter
    from selfhealing.interfaces.cache_provider import CacheProviderInterface
    from selfhealing.interfaces.repositories import (
        CircuitBreakerStateRepository,
        FailedOperationRepository,
        SecurityIncidentRepository,
    )
    from selfhealing.interfaces.statistics import StatisticsRepositoryInterface
    from selfhealing.interfaces.task_queue import TaskQueueInterface

logger = structlog.get_logger()


class ProviderRegistry:
    """
    Central registry for all pluggable components.

    Allows runtime registration and lookup of adapters.
    Thread-safe for read operations.
    """

    # Provider registries
    _cache_providers: dict[str, type] = {}
    _task_queues: dict[str, type] = {}
    _failed_op_repos: dict[str, type] = {}
    _circuit_breaker_repos: dict[str, type] = {}
    _security_repos: dict[str, type] = {}
    _audit_adapters: dict[str, type] = {}  # Audit adapters
    _traffic_routing_adapters: dict[str, type] = {}  # Traffic routing adapters
    _correlation_strategies: dict[str, type] = {}  # Correlation ML strategies
    _root_cause_strategies: dict[str, type] = {}  # Root cause analysis strategies
    _graph_build_strategies: dict[str, type] = {}  # Graph build strategies

    # Statistics adapter (singleton, registered by app)
    _statistics_adapter: StatisticsRepositoryInterface | None = None

    # Model class registry (registered by host app, not by selfhealing)
    _postmortem_model: type | None = None

    # Default provider names
    _default_cache: str = "memory"
    _default_queue: str = "sync"
    _default_repo: str = "redis"
    _default_audit: str = "file"  # Default audit adapter
    _default_traffic_routing: str = "logging"  # Default traffic routing adapter

    # Singleton instances (for reuse)
    _instances: dict[str, object] = {}

    # =========================================================================
    # Registration Methods
    # =========================================================================

    @classmethod
    def register_cache(cls, name: str, provider_class: type) -> None:
        """Register a cache provider adapter."""
        cls._cache_providers[name] = provider_class
        logger.debug(
            "cell_registry.bulkheads_registered",
            name=name,
        )

    @classmethod
    def register_queue(cls, name: str, provider_class: type) -> None:
        """Register a task queue adapter."""
        cls._task_queues[name] = provider_class
        logger.debug(
            "cell_registry.bulkheads_registered",
            name=name,
        )

    @classmethod
    def register_failed_operation_repo(cls, name: str, repo_class: type) -> None:
        """Register a failed operation repository."""
        cls._failed_op_repos[name] = repo_class
        logger.debug(
            "cell_registry.bulkheads_registered",
            name=name,
        )

    @classmethod
    def register_circuit_breaker_repo(cls, name: str, repo_class: type) -> None:
        """Register a circuit breaker state repository."""
        cls._circuit_breaker_repos[name] = repo_class
        logger.debug(
            "cell_registry.bulkheads_registered",
            name=name,
        )

    @classmethod
    def register_security_repo(cls, name: str, repo_class: type) -> None:
        """Register a security incident repository."""
        cls._security_repos[name] = repo_class
        logger.debug(
            "cell_registry.bulkheads_registered",
            name=name,
        )

    @classmethod
    def register_audit_adapter(cls, name: str, adapter_class: type) -> None:
        """Register an audit log adapter."""
        cls._audit_adapters[name] = adapter_class
        logger.debug(
            "cell_registry.bulkheads_registered",
            name=name,
        )

    @classmethod
    def register_traffic_routing(cls, name: str, adapter_class: type) -> None:
        """Register a traffic routing adapter."""
        cls._traffic_routing_adapters[name] = adapter_class
        logger.debug(
            "cell_registry.bulkheads_registered",
            name=name,
        )

    @classmethod
    def register_correlation_strategy(cls, name: str, strategy_class: type) -> None:
        """Correlation Engine 상관관계 분석 전략 등록."""
        cls._correlation_strategies[name] = strategy_class
        logger.debug(
            "cell_registry.bulkheads_registered",
            name=name,
        )

    @classmethod
    def register_root_cause_strategy(cls, name: str, strategy_class: type) -> None:
        """Correlation Engine 근본 원인 분석 전략 등록."""
        cls._root_cause_strategies[name] = strategy_class
        logger.debug(
            "cell_registry.bulkheads_registered",
            name=name,
        )

    @classmethod
    def register_graph_build_strategy(cls, name: str, strategy_class: type) -> None:
        """Correlation Engine DAG 구축 전략 등록."""
        cls._graph_build_strategies[name] = strategy_class
        logger.debug(
            "cell_registry.bulkheads_registered",
            name=name,
        )

    @classmethod
    def register_statistics_adapter(
        cls,
        adapter: StatisticsRepositoryInterface,
    ) -> None:
        """
        Register a statistics adapter.

        Should be called during app initialization (e.g., Django's AppConfig.ready()).
        Only one statistics adapter can be registered at a time.

        Args:
            adapter: StatisticsRepositoryInterface implementation

        Example (Django):
            # shopping/apps.py
            from selfhealing.factory import ProviderRegistry
            from selfhealing.adapters.django.statistics import DjangoStatisticsAdapter

            class ShoppingConfig(AppConfig):
                def ready(self):
                    ProviderRegistry.register_statistics_adapter(
                        DjangoStatisticsAdapter()
                    )
        """
        cls._statistics_adapter = adapter
        logger.info(
            "registry.statistics_adapter_registered",
            value=type(adapter).__name__,
        )

    @classmethod
    def register_postmortem_model(cls, model_class: type) -> None:
        """
        Register the PostmortemRecord model class.

        This allows selfhealing package to use the host app's concrete model
        without hardcoding import paths. Should be called during app initialization.

        Args:
            model_class: Concrete Django model inheriting AbstractPostmortemRecord

        Example (Django):
            # myapp/apps.py
            from selfhealing.factory import ProviderRegistry

            class MyAppConfig(AppConfig):
                def ready(self):
                    from selfhealing.adapters.django.models import PostmortemRecord
                    ProviderRegistry.register_postmortem_model(PostmortemRecord)
        """
        cls._postmortem_model = model_class
        logger.info(
            "registry.postmortem_model_registered",
            model_class=model_class.__name__,
        )

    @classmethod
    def get_postmortem_model(cls) -> type | None:
        """
        Get the registered PostmortemRecord model class.

        Returns:
            The registered model class, or None if not registered.
        """
        return cls._postmortem_model

    @classmethod
    def has_postmortem_model(cls) -> bool:
        """
        Check if a PostmortemRecord model is registered.

        Returns:
            True if a model is registered.
        """
        return cls._postmortem_model is not None

    # =========================================================================
    # Provider Getters
    # =========================================================================

    @classmethod
    def get_cache(
        cls,
        name: str | None = None,
        singleton: bool = True,
    ) -> CacheProviderInterface:
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
        name: str | None = None,
        singleton: bool = True,
    ) -> TaskQueueInterface:
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
        name: str | None = None,
        singleton: bool = True,
    ) -> FailedOperationRepository:
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
        name: str | None = None,
        singleton: bool = True,
    ) -> CircuitBreakerStateRepository:
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
        name: str | None = None,
        singleton: bool = True,
    ) -> SecurityIncidentRepository:
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
    # Statistics Repository (Hybrid Storage)
    # =========================================================================

    @classmethod
    def get_statistics_repo(cls) -> StatisticsRepositoryInterface:
        """
        Get statistics repository instance.

        Returns the registered statistics adapter, or NullStatisticsRepository
        if no adapter is registered.

        Unlike runtime repositories, statistics repository is a singleton
        registered by the application, not selected by name.

        Returns:
            StatisticsRepositoryInterface instance

        Example:
            stats_repo = ProviderRegistry.get_statistics_repo()
            counts = stats_repo.get_status_counts()
        """
        if cls._statistics_adapter is None:
            from selfhealing.adapters.statistics.null import NullStatisticsRepository

            return NullStatisticsRepository()
        return cls._statistics_adapter

    @classmethod
    def has_statistics_adapter(cls) -> bool:
        """
        Check if a statistics adapter is registered.

        Useful for conditionally showing dashboard features.

        Returns:
            True if a statistics adapter is registered
        """
        return cls._statistics_adapter is not None

    @classmethod
    def get_audit_adapter(
        cls,
        name: str | None = None,
        singleton: bool = True,
    ) -> AuditLogAdapter:
        """
        Get audit adapter instance.

        Args:
            name: Adapter name (e.g., 'file', 'stdout', 'null')
            singleton: If True, return cached instance

        Returns:
            AuditLogAdapter instance

        Raises:
            ValueError: If no adapter registered with the given name
        """

        name = name or cls._default_audit

        if singleton:
            key = f"audit:{name}"
            if key in cls._instances:
                return cls._instances[key]

        if name not in cls._audit_adapters:
            # Auto-register defaults if not registered
            cls._auto_register_audit_adapters()

        if name not in cls._audit_adapters:
            raise ValueError(f"Unknown audit adapter: {name}. " f"Available: {list(cls._audit_adapters.keys())}")

        adapter_class = cls._audit_adapters[name]

        # Create instance with default settings
        import os

        if name == "file":
            log_path = os.getenv("AUDIT_LOG_PATH", "logs/audit.jsonl")
            instance = adapter_class(log_path)
        else:
            instance = adapter_class()

        if singleton:
            cls._instances[key] = instance

        return instance

    @classmethod
    def _auto_register_audit_adapters(cls) -> None:
        """Auto-register default audit adapters."""
        try:
            from selfhealing.adapters.audit.file_adapter import FileAuditLogAdapter
            from selfhealing.adapters.audit.null_adapter import NullAuditLogAdapter
            from selfhealing.adapters.audit.stdout_adapter import StdoutAuditLogAdapter

            if "file" not in cls._audit_adapters:
                cls.register_audit_adapter("file", FileAuditLogAdapter)
            if "stdout" not in cls._audit_adapters:
                cls.register_audit_adapter("stdout", StdoutAuditLogAdapter)
            if "null" not in cls._audit_adapters:
                cls.register_audit_adapter("null", NullAuditLogAdapter)
        except ImportError:
            pass

    @classmethod
    def get_traffic_routing(
        cls,
        name: str | None = None,
        singleton: bool = True,
    ) -> "TrafficRoutingAdapter":
        """
        Get traffic routing adapter instance.

        Args:
            name: Adapter name (e.g., 'logging')
            singleton: If True, return cached instance

        Returns:
            TrafficRoutingAdapter instance

        Raises:
            ValueError: If no adapter registered with the given name
        """
        from selfhealing.interfaces.traffic_routing import TrafficRoutingAdapter

        name = name or cls._default_traffic_routing

        if singleton:
            key = f"traffic_routing:{name}"
            if key in cls._instances:
                return cls._instances[key]

        if name not in cls._traffic_routing_adapters:
            # Auto-register default
            cls._auto_register_traffic_routing_adapters()

        if name not in cls._traffic_routing_adapters:
            raise ValueError(
                f"Unknown traffic routing adapter: {name}. " f"Available: {list(cls._traffic_routing_adapters.keys())}"
            )

        instance = cls._traffic_routing_adapters[name]()

        if singleton:
            cls._instances[key] = instance

        return instance

    @classmethod
    def _auto_register_traffic_routing_adapters(cls) -> None:
        """Auto-register default traffic routing adapters."""
        try:
            from selfhealing.adapters.traffic_routing.logging_adapter import (
                LoggingTrafficRoutingAdapter,
            )

            if "logging" not in cls._traffic_routing_adapters:
                cls.register_traffic_routing("logging", LoggingTrafficRoutingAdapter)
        except ImportError:
            pass

        try:
            from selfhealing.adapters.traffic_routing.k8s_ingress_adapter import (
                K8sIngressTrafficRoutingAdapter,
            )

            if "k8s_ingress" not in cls._traffic_routing_adapters:
                cls.register_traffic_routing("k8s_ingress", K8sIngressTrafficRoutingAdapter)
        except ImportError:
            pass

    # =========================================================================
    # Correlation Engine Strategy Getters
    # =========================================================================

    @classmethod
    def get_correlation_strategy(cls, name: str) -> type:
        """등록된 Correlation 전략 클래스를 반환한다.

        Args:
            name: 전략 이름

        Returns:
            전략 클래스 (인스턴스화는 호출부 책임)

        Raises:
            ValueError: 등록되지 않은 전략 이름
        """
        if name not in cls._correlation_strategies:
            raise ValueError(
                f"Unknown correlation strategy: {name}. " f"Available: {list(cls._correlation_strategies.keys())}"
            )
        return cls._correlation_strategies[name]

    @classmethod
    def get_root_cause_strategy(cls, name: str) -> type:
        """등록된 Root Cause 분석 전략 클래스를 반환한다."""
        if name not in cls._root_cause_strategies:
            raise ValueError(f"Unknown root cause strategy: {name}. " f"Available: {list(cls._root_cause_strategies.keys())}")
        return cls._root_cause_strategies[name]

    @classmethod
    def get_graph_build_strategy(cls, name: str) -> type:
        """등록된 Graph Build 전략 클래스를 반환한다."""
        if name not in cls._graph_build_strategies:
            raise ValueError(
                f"Unknown graph build strategy: {name}. " f"Available: {list(cls._graph_build_strategies.keys())}"
            )
        return cls._graph_build_strategies[name]

    # =========================================================================
    # Configuration Methods
    # =========================================================================

    @classmethod
    def set_defaults(
        cls,
        cache: str | None = None,
        queue: str | None = None,
        repo: str | None = None,
    ) -> None:
        """
        Set default providers.

        Args:
            cache: Default cache provider name
            queue: Default task queue name
            repo: Default repository name
        """
        if cache:
            cls._default_cache = cache
        if queue:
            cls._default_queue = queue
        if repo:
            cls._default_repo = repo

        logger.info(
            "registry.defaults_updated",
            cls=cls._default_cache,
            cls_1=cls._default_queue,
            cls_2=cls._default_repo,
        )

    @classmethod
    def get_defaults(cls) -> dict[str, str]:
        """Get current default provider names."""
        return {
            "cache": cls._default_cache,
            "queue": cls._default_queue,
            "repo": cls._default_repo,
        }

    @classmethod
    def list_providers(cls) -> dict[str, Any]:
        """List all registered providers."""
        return {
            "cache": list(cls._cache_providers.keys()),
            "queue": list(cls._task_queues.keys()),
            "failed_operation_repo": list(cls._failed_op_repos.keys()),
            "circuit_breaker_repo": list(cls._circuit_breaker_repos.keys()),
            "security_repo": list(cls._security_repos.keys()),
            "audit_adapter": list(cls._audit_adapters.keys()),
            "traffic_routing": list(cls._traffic_routing_adapters.keys()),
            "statistics_adapter": (type(cls._statistics_adapter).__name__ if cls._statistics_adapter else None),
        }

    @classmethod
    def clear_instances(cls) -> None:
        """
        Clear all cached instances.

        For testing only. Use to reset singleton instances between tests.
        """
        cls._instances.clear()
        logger.debug("registry")

    @classmethod
    def reset(cls) -> None:
        """
        Reset registry to initial state.

        For testing only. Clears all registered providers and instances.
        """
        cls._instances.clear()
        cls._cache_providers.clear()
        cls._task_queues.clear()
        cls._failed_op_repos.clear()
        cls._circuit_breaker_repos.clear()
        cls._security_repos.clear()
        cls._audit_adapters.clear()
        cls._traffic_routing_adapters.clear()
        cls._correlation_strategies.clear()
        cls._root_cause_strategies.clear()
        cls._graph_build_strategies.clear()
        cls._statistics_adapter = None  # Reset statistics adapter
        cls._postmortem_model = None  # Reset postmortem model
        cls._default_cache = "memory"
        cls._default_queue = "sync"
        cls._default_repo = "redis"  # Changed from "django" to "redis"
        cls._default_audit = "file"
        cls._default_traffic_routing = "logging"
        logger.debug("registry")

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
            cache = cls.get_cache()
            results["cache"] = cache.health_check()
        except Exception as e:
            logger.exception(
                "registry.cache_health_check_failed",
                error=e,
            )
            results["cache"] = False

        try:
            queue = cls.get_queue()
            results["queue"] = queue.health_check()
        except Exception as e:
            logger.exception(
                "registry.queue_health_check_failed",
                error=e,
            )
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

    try:
        from selfhealing.adapters.cache.memcached_adapter import MemcachedCacheAdapter

        ProviderRegistry.register_cache("memcached", MemcachedCacheAdapter)
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

    try:
        from selfhealing.adapters.queues.rq_adapter import RQTaskAdapter

        ProviderRegistry.register_queue("rq", RQTaskAdapter)
    except ImportError:
        pass

    # NOTE: Django and SQLAlchemy adapters have been removed in v2.0.0.
    # Use Redis adapters (with ResilientStorageBackend fallback) instead.
    # See docs/self_healing/middleware_system/06_REDIS_MIGRATION.md

    # In-memory repositories (for testing, standalone)
    try:
        from selfhealing.adapters.memory import (
            InMemoryCircuitBreakerStateRepository,
            InMemoryFailedOperationRepository,
            InMemorySecurityIncidentRepository,
        )

        ProviderRegistry.register_failed_operation_repo("memory", InMemoryFailedOperationRepository)
        ProviderRegistry.register_circuit_breaker_repo("memory", InMemoryCircuitBreakerStateRepository)
        ProviderRegistry.register_security_repo("memory", InMemorySecurityIncidentRepository)
    except ImportError:
        pass

    # Redis-based repositories (using ResilientStorageBackend)
    try:
        from selfhealing.adapters.redis import (
            RedisCircuitBreakerStateRepository,
            RedisDLQRepository,
        )
        from selfhealing.adapters.resilient.backend import get_storage_backend

        def _create_redis_cb_repo():
            return RedisCircuitBreakerStateRepository(get_storage_backend())

        def _create_redis_dlq_repo():
            return RedisDLQRepository(get_storage_backend())

        ProviderRegistry.register_circuit_breaker_repo("redis", _create_redis_cb_repo)
        ProviderRegistry.register_failed_operation_repo("redis", _create_redis_dlq_repo)
    except ImportError:
        pass

    # Layered repository (L1=Memory + L2=Redis) — 프로덕션 권장 (#227 §7.4)
    try:
        from selfhealing.adapters.memory.layered_repository import (
            LayeredCircuitBreakerStateRepository,
        )
        from selfhealing.adapters.redis import (
            RedisCircuitBreakerStateRepository as _RedisCBRepo,
        )
        from selfhealing.adapters.resilient.backend import (
            get_storage_backend as _get_backend,
        )

        def _create_layered_cb_repo():
            l2_repo = _RedisCBRepo(_get_backend())
            return LayeredCircuitBreakerStateRepository(
                l2_repo=l2_repo,
                sync_interval_seconds=5.0,
                adapter_type="redis",
                use_bulkhead=True,
            )

        ProviderRegistry.register_circuit_breaker_repo("layered", _create_layered_cb_repo)
    except ImportError:
        pass

    # Audit adapters
    try:
        from selfhealing.adapters.audit.file_adapter import FileAuditLogAdapter
        from selfhealing.adapters.audit.null_adapter import NullAuditLogAdapter
        from selfhealing.adapters.audit.stdout_adapter import StdoutAuditLogAdapter

        ProviderRegistry.register_audit_adapter("file", FileAuditLogAdapter)
        ProviderRegistry.register_audit_adapter("stdout", StdoutAuditLogAdapter)
        ProviderRegistry.register_audit_adapter("null", NullAuditLogAdapter)
    except ImportError:
        pass


# Run auto-registration on module import
_auto_register_adapters()


# =============================================================================
# Resilient Storage Convenience Functions
# =============================================================================


def get_storage_backend():
    """
    Get ResilientStorageBackend instance.

    Provides unified storage with:
    - Redis-First architecture
    - Graceful degradation to Memory + WAL
    - Zero data loss guarantee

    Returns:
        ResilientStorageBackend singleton instance
    """
    from selfhealing.adapters.resilient.backend import (
        get_storage_backend as _get_backend,
    )

    return _get_backend()


def get_circuit_breaker_repo():
    """
    Get Redis-based Circuit Breaker Repository.

    Uses ResilientStorageBackend for zero data loss.
    Falls back to memory on Redis failure.

    Returns:
        RedisCircuitBreakerStateRepository instance
    """
    from selfhealing.adapters.redis.circuit_breaker import (
        get_redis_circuit_breaker_repo,
    )

    return get_redis_circuit_breaker_repo()


def get_dlq_repo():
    """
    Get Redis-based DLQ Repository.

    Uses ResilientStorageBackend for zero data loss.
    Falls back to memory on Redis failure.

    Returns:
        RedisDLQRepository instance
    """
    from selfhealing.adapters.redis.dlq import get_redis_dlq_repo

    return get_redis_dlq_repo()
