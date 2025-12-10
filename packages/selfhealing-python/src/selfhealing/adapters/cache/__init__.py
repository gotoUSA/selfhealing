"""
Cache adapters for the self-healing system.

This package contains implementations of CacheProviderInterface.
"""

from selfhealing.adapters.cache.redis_adapter import RedisCacheAdapter
from selfhealing.adapters.cache.memory_adapter import InMemoryCacheAdapter

__all__ = [
    "RedisCacheAdapter",
    "InMemoryCacheAdapter",
]
