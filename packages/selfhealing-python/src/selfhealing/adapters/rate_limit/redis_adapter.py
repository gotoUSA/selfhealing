"""
Redis Rate Limit Storage Adapter

High-performance distributed rate limit storage using Redis.
Provides atomic operations for multi-server environments.

Requirements:
    - redis>=4.0.0

Features:
    - Atomic increment/set operations
    - Automatic TTL-based cleanup
    - Fastest option for distributed rate limiting
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from selfhealing.interfaces.rate_limit_storage import (
    RateLimitState,
    RateLimitStorageInterface,
    RateLimitStorageType,
    RateLimitStorageUnavailableError,
)

logger = logging.getLogger(__name__)


class RedisRateLimitStorage(RateLimitStorageInterface):
    """
    Redis-based rate limit storage.
    
    Uses Redis for atomic distributed rate limit state management.
    Recommended for production multi-server environments.
    
    Key schema:
        ratelimit:{key}:cooldown_until - float timestamp
        ratelimit:{key}:consecutive_429s - int counter
        ratelimit:{key}:last_updated - float timestamp
    
    Example:
        redis_client = redis.Redis(host='localhost', port=6379, db=0)
        storage = RedisRateLimitStorage(redis_client)
        
        # Set cooldown after 429
        storage.set_cooldown("payment_api", time.time() + 60)
    """
    
    KEY_PREFIX = "ratelimit"
    DEFAULT_TTL = 3600  # 1 hour
    
    def __init__(self, redis_client: Any) -> None:
        """
        Initialize Redis rate limit storage.
        
        Args:
            redis_client: Redis client instance (redis.Redis or compatible)
        """
        self._redis = redis_client
        self._available: Optional[bool] = None
    
    @property
    def storage_type(self) -> RateLimitStorageType:
        return RateLimitStorageType.REDIS
    
    def _make_key(self, key: str, suffix: str) -> str:
        """Generate Redis key with prefix."""
        return f"{self.KEY_PREFIX}:{key}:{suffix}"
    
    def is_available(self) -> bool:
        """Check if Redis is available."""
        if self._available is not None:
            return self._available
        
        try:
            self._redis.ping()
            self._available = True
            return True
        except Exception as e:
            logger.warning(f"[RedisRateLimitStorage] Redis unavailable: {e}")
            self._available = False
            return False
    
    def get_state(self, key: str) -> RateLimitState:
        """Get rate limit state from Redis."""
        try:
            pipeline = self._redis.pipeline()
            pipeline.get(self._make_key(key, "cooldown_until"))
            pipeline.get(self._make_key(key, "consecutive_429s"))
            pipeline.get(self._make_key(key, "last_updated"))
            
            results = pipeline.execute()
            
            cooldown_until = float(results[0]) if results[0] else 0.0
            consecutive_429s = int(results[1]) if results[1] else 0
            last_updated = float(results[2]) if results[2] else 0.0
            
            return RateLimitState(
                key=key,
                cooldown_until=cooldown_until,
                consecutive_429s=consecutive_429s,
                last_updated=last_updated,
            )
            
        except Exception as e:
            logger.error(f"[RedisRateLimitStorage] Failed to get state: {e}")
            return RateLimitState(key=key)
    
    def set_cooldown(
        self,
        key: str,
        cooldown_until: float,
        ttl: Optional[int] = None,
    ) -> None:
        """Set cooldown in Redis with TTL."""
        try:
            ttl = ttl or self.DEFAULT_TTL
            now = time.time()
            
            pipeline = self._redis.pipeline()
            pipeline.set(
                self._make_key(key, "cooldown_until"),
                str(cooldown_until),
                ex=ttl,
            )
            pipeline.set(
                self._make_key(key, "last_updated"),
                str(now),
                ex=ttl,
            )
            pipeline.execute()
            
            logger.debug(
                f"[RedisRateLimitStorage] Set cooldown for '{key}': "
                f"until={cooldown_until}, ttl={ttl}"
            )
            
        except Exception as e:
            logger.error(f"[RedisRateLimitStorage] Failed to set cooldown: {e}")
            raise RateLimitStorageUnavailableError(str(e)) from e
    
    def increment_consecutive_429s(self, key: str) -> int:
        """Atomically increment 429 counter in Redis."""
        try:
            redis_key = self._make_key(key, "consecutive_429s")
            
            # Atomic increment with TTL
            pipeline = self._redis.pipeline()
            pipeline.incr(redis_key)
            pipeline.expire(redis_key, self.DEFAULT_TTL)
            results = pipeline.execute()
            
            new_value = results[0]
            logger.debug(
                f"[RedisRateLimitStorage] Incremented 429 counter for '{key}': {new_value}"
            )
            return new_value
            
        except Exception as e:
            logger.error(f"[RedisRateLimitStorage] Failed to increment: {e}")
            raise RateLimitStorageUnavailableError(str(e)) from e
    
    def reset_consecutive_429s(self, key: str) -> None:
        """Reset 429 counter in Redis."""
        try:
            self._redis.delete(self._make_key(key, "consecutive_429s"))
            logger.debug(f"[RedisRateLimitStorage] Reset 429 counter for '{key}'")
            
        except Exception as e:
            logger.error(f"[RedisRateLimitStorage] Failed to reset: {e}")
    
    def clear(self, key: str) -> None:
        """Clear all rate limit state for a key."""
        try:
            pipeline = self._redis.pipeline()
            pipeline.delete(self._make_key(key, "cooldown_until"))
            pipeline.delete(self._make_key(key, "consecutive_429s"))
            pipeline.delete(self._make_key(key, "last_updated"))
            pipeline.execute()
            
            logger.debug(f"[RedisRateLimitStorage] Cleared state for '{key}'")
            
        except Exception as e:
            logger.error(f"[RedisRateLimitStorage] Failed to clear: {e}")
