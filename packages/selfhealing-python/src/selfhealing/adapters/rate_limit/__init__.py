"""
Rate Limit Storage Adapters

Concrete implementations of RateLimitStorageInterface for different backends.

Available adapters:
- RedisRateLimitStorage: Fastest, requires Redis
- DatabaseRateLimitStorage: 100% compatible fallback using any database
- InMemoryRateLimitStorage: Single process only, for testing

Usage:
    from selfhealing.adapters.rate_limit import (
        get_rate_limit_storage,
        RedisRateLimitStorage,
        DatabaseRateLimitStorage,
        InMemoryRateLimitStorage,
    )

    # Auto-detect best available backend
    storage = get_rate_limit_storage()

    # Or explicitly choose
    storage = RedisRateLimitStorage(redis_client)
"""

from selfhealing.adapters.rate_limit.database_adapter import DatabaseRateLimitStorage
from selfhealing.adapters.rate_limit.factory import get_rate_limit_storage
from selfhealing.adapters.rate_limit.memory_adapter import InMemoryRateLimitStorage
from selfhealing.adapters.rate_limit.redis_adapter import RedisRateLimitStorage

__all__ = [
    "RedisRateLimitStorage",
    "DatabaseRateLimitStorage",
    "InMemoryRateLimitStorage",
    "get_rate_limit_storage",
]
