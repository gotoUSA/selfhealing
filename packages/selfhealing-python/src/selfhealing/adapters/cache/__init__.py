"""
Cache provider adapters for the self-healing system.

This module contains concrete implementations of CacheProviderInterface
for different cache backends.

Available Adapters:
    - RedisCacheAdapter: Redis-based caching with distributed locks
    - InMemoryCacheAdapter: In-memory caching for testing
"""

from selfhealing.adapters.cache.memory_adapter import (
    InMemoryCacheAdapter,
)
from selfhealing.adapters.cache.redis_adapter import (
    RedisCacheAdapter,
)

__all__ = [
    "RedisCacheAdapter",
    "InMemoryCacheAdapter",
]
