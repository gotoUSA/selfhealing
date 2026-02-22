"""
Rate Limit Storage Factory

Auto-detects the best available storage backend for rate limiting.
Ensures 100% Self-DDoS prevention regardless of infrastructure.

Priority order:
    1. Redis (if available) - fastest
    2. Database (always available) - 100% fallback
    3. In-Memory (last resort) - single process only

Usage:
    from selfhealing.adapters.rate_limit import get_rate_limit_storage

    # Auto-detect best backend
    storage = get_rate_limit_storage()

    # Force specific backend
    storage = get_rate_limit_storage(backend="database")
"""

from __future__ import annotations

from typing import Any, Literal

import structlog

from selfhealing.interfaces.rate_limit_storage import (
    RateLimitStorageInterface,
)

logger = structlog.get_logger()

# Cached storage instance
_storage_instance: RateLimitStorageInterface | None = None


def get_rate_limit_storage(
    backend: Literal["redis", "database", "memory", "auto"] | None = "auto",
    redis_client: Any | None = None,
    force_new: bool = False,
) -> RateLimitStorageInterface:
    """
    Get the rate limit storage backend.

    Args:
        backend: Storage backend to use:
            - "auto": Auto-detect best available (default)
            - "redis": Force Redis (fails if unavailable)
            - "database": Force database
            - "memory": Force in-memory (single process only)
        redis_client: Optional Redis client instance for Redis backend
        force_new: If True, create new instance instead of using cached

    Returns:
        RateLimitStorageInterface implementation

    Raises:
        RuntimeError: If forced backend is unavailable

    Example:
        # Auto-detect
        storage = get_rate_limit_storage()

        # Force Redis with custom client
        storage = get_rate_limit_storage(
            backend="redis",
            redis_client=my_redis_client,
        )
    """
    global _storage_instance

    if not force_new and _storage_instance is not None:
        return _storage_instance

    storage = _create_storage(backend, redis_client)

    if not force_new:
        _storage_instance = storage

    logger.info(
        "rate_limit_storage.initialized_storage_backend",
        storage_type=storage.storage_type.value,
    )

    return storage


def _create_storage(
    backend: str | None,
    redis_client: Any | None,
) -> RateLimitStorageInterface:
    """Create storage instance based on backend preference."""

    if backend == "redis":
        return _create_redis_storage(redis_client, required=True)

    if backend == "database":
        return _create_database_storage()

    if backend == "memory":
        return _create_memory_storage()

    # Auto-detect
    return _auto_detect_storage(redis_client)


def _auto_detect_storage(
    redis_client: Any | None,
) -> RateLimitStorageInterface:
    """Auto-detect the best available storage backend."""

    # 1. Try Redis first (fastest)
    try:
        storage = _create_redis_storage(redis_client, required=False)
        if storage and storage.is_available():
            logger.info("rate_limit_storage.auto_detected_redis")
            return storage
    except Exception as e:
        logger.debug(
            "rate_limit_storage.redis_available",
            error=e,
        )

    # 2. Try Database (100% fallback)
    try:
        storage = _create_database_storage()
        if storage.is_available():
            logger.info("rate_limit_storage.auto_detected_database")
            return storage
    except Exception as e:
        logger.debug(
            "rate_limit_storage.database_available",
            error=e,
        )

    # 3. Fall back to In-Memory (single process)
    logger.warning("rate_limit_storage.falling_back_memory_storage")
    return _create_memory_storage()


def _create_redis_storage(
    redis_client: Any | None,
    required: bool = False,
) -> RateLimitStorageInterface | None:
    """Create Redis storage backend."""
    from selfhealing.adapters.rate_limit.redis_adapter import RedisRateLimitStorage

    client = redis_client or _get_default_redis_client()

    if client is None:
        if required:
            raise RuntimeError("Redis client not available")
        return None

    return RedisRateLimitStorage(client)


def _get_default_redis_client() -> Any | None:
    """Try to get Redis client from common sources."""

    # Try Django cache
    try:
        from django.core.cache import caches

        cache = caches.get("default")
        if hasattr(cache, "client"):
            client = cache.client.get_client()
            return client
    except Exception:
        pass

    # Try django-redis
    try:
        from django_redis import get_redis_connection

        return get_redis_connection("default")
    except Exception:
        pass

    # Try direct Redis connection from settings
    try:
        import redis
        from django.conf import settings

        redis_url = getattr(settings, "REDIS_URL", None)
        if redis_url:
            return redis.from_url(redis_url)

        # Try CACHES setting
        caches_config = getattr(settings, "CACHES", {})
        default_cache = caches_config.get("default", {})
        location = default_cache.get("LOCATION")

        if location and "redis" in str(location).lower():
            return redis.from_url(location)
    except Exception:
        pass

    return None


def _create_database_storage() -> RateLimitStorageInterface:
    """Create database storage backend."""
    from selfhealing.adapters.rate_limit.database_adapter import (
        DatabaseRateLimitStorage,
    )

    return DatabaseRateLimitStorage()


def _create_memory_storage() -> RateLimitStorageInterface:
    """Create in-memory storage backend."""
    from selfhealing.adapters.rate_limit.memory_adapter import InMemoryRateLimitStorage

    return InMemoryRateLimitStorage.get_instance()


def reset_storage() -> None:
    """Reset cached storage instance (for testing)."""
    global _storage_instance
    _storage_instance = None
