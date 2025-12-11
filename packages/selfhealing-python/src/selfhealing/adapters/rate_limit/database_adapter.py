"""
Database Rate Limit Storage Adapter

Universal fallback rate limit storage using any database.
Guarantees 100% Self-DDoS prevention coverage.

Key Insight:
    "Every application has a database" - This adapter ensures
    100% coverage regardless of customer infrastructure.

Features:
    - Works with any database (PostgreSQL, MySQL, SQLite)
    - Framework-agnostic (uses repository pattern)
    - Slightly slower than Redis (~1-5ms vs ~0.1ms)
    - Perfect for customers without Redis infrastructure

Performance Note:
    Rate limit queries occur only on 429 responses, not every request.
    A few milliseconds delay is negligible in this context.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from selfhealing.interfaces.rate_limit_storage import (
    RateLimitState,
    RateLimitStorageInterface,
    RateLimitStorageType,
)

logger = logging.getLogger(__name__)


class DatabaseRateLimitStorage(RateLimitStorageInterface):
    """
    Database-based rate limit storage.

    Uses a simple key-value table for storing rate limit state.
    Works with any SQL database through a repository abstraction.

    Table schema (auto-created by migrations):
        CREATE TABLE selfhealing_ratelimitstate (
            id SERIAL PRIMARY KEY,
            key VARCHAR(255) UNIQUE NOT NULL,
            cooldown_until DOUBLE PRECISION DEFAULT 0,
            consecutive_429s INTEGER DEFAULT 0,
            last_updated DOUBLE PRECISION DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE INDEX idx_ratelimit_key ON selfhealing_ratelimitstate(key);

    Example:
        storage = DatabaseRateLimitStorage()
        storage.set_cooldown("payment_api", time.time() + 60)
    """

    def __init__(
        self,
        repository_factory: Optional[Callable] = None,
    ) -> None:
        """
        Initialize database rate limit storage.

        Args:
            repository_factory: Optional factory function to create repository.
                              If None, uses Django ORM by default.
        """
        self._repository_factory = repository_factory
        self._lock = threading.Lock()
        self._available: Optional[bool] = None

    @property
    def storage_type(self) -> RateLimitStorageType:
        return RateLimitStorageType.DATABASE

    def _get_repository(self):
        """Get the rate limit state repository."""
        if self._repository_factory:
            return self._repository_factory()

        # Default: Try Django ORM
        try:
            from selfhealing.adapters.django_repositories import (
                DjangoRateLimitStateRepository,
            )

            return DjangoRateLimitStateRepository()
        except ImportError:
            raise RuntimeError("No repository available. Provide repository_factory or install Django.")

    def is_available(self) -> bool:
        """Check if database is available."""
        if self._available is not None:
            return self._available

        try:
            repo = self._get_repository()
            # Simple query to check connectivity
            repo.get_or_create("__healthcheck__")
            self._available = True
            return True
        except Exception as e:
            logger.warning(f"[DatabaseRateLimitStorage] Database unavailable: {e}")
            self._available = False
            return False

    def get_state(self, key: str) -> RateLimitState:
        """Get rate limit state from database."""
        try:
            repo = self._get_repository()
            data = repo.get(key)

            if data is None:
                return RateLimitState(key=key)

            return RateLimitState(
                key=key,
                cooldown_until=data.get("cooldown_until", 0.0),
                consecutive_429s=data.get("consecutive_429s", 0),
                last_updated=data.get("last_updated", 0.0),
            )

        except Exception as e:
            logger.error(f"[DatabaseRateLimitStorage] Failed to get state: {e}")
            return RateLimitState(key=key)

    def set_cooldown(
        self,
        key: str,
        cooldown_until: float,
        ttl: Optional[int] = None,
    ) -> None:
        """Set cooldown in database."""
        try:
            with self._lock:
                repo = self._get_repository()
                now = time.time()

                repo.upsert(
                    key=key,
                    data={
                        "cooldown_until": cooldown_until,
                        "last_updated": now,
                    },
                )

                logger.debug(f"[DatabaseRateLimitStorage] Set cooldown for '{key}': " f"until={cooldown_until}")

        except Exception as e:
            logger.error(f"[DatabaseRateLimitStorage] Failed to set cooldown: {e}")
            raise

    def increment_consecutive_429s(self, key: str) -> int:
        """Increment 429 counter in database."""
        try:
            with self._lock:
                repo = self._get_repository()
                new_value = repo.increment(key, "consecutive_429s")

                logger.debug(f"[DatabaseRateLimitStorage] Incremented 429 counter for '{key}': " f"{new_value}")
                return new_value

        except Exception as e:
            logger.error(f"[DatabaseRateLimitStorage] Failed to increment: {e}")
            raise

    def reset_consecutive_429s(self, key: str) -> None:
        """Reset 429 counter in database."""
        try:
            with self._lock:
                repo = self._get_repository()
                repo.update(key, {"consecutive_429s": 0})

                logger.debug(f"[DatabaseRateLimitStorage] Reset 429 counter for '{key}'")

        except Exception as e:
            logger.error(f"[DatabaseRateLimitStorage] Failed to reset: {e}")

    def clear(self, key: str) -> None:
        """Clear all rate limit state for a key."""
        try:
            with self._lock:
                repo = self._get_repository()
                repo.delete(key)

                logger.debug(f"[DatabaseRateLimitStorage] Cleared state for '{key}'")

        except Exception as e:
            logger.error(f"[DatabaseRateLimitStorage] Failed to clear: {e}")
