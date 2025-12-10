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

# Conditionally import adapters based on available dependencies
try:
    from selfhealing.adapters.cache.memcached_adapter import MemcachedCacheAdapter

    __all__.append("MemcachedCacheAdapter")
except ImportError:
    pass
