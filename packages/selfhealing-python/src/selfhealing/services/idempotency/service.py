"""
Idempotency Service

Core service for checking and managing idempotency of operations.

Canonical location: ``selfhealing.services.idempotency.service``
"""

from __future__ import annotations

import structlog
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from selfhealing.core.time_provider import get_time_provider
from selfhealing.settings import get_config

from .models import IdempotencyKey, IdempotencyResult

if TYPE_CHECKING:
    from selfhealing.core.time_provider import TimeProvider

logger = structlog.get_logger()


class IdempotencyService:
    """
    Service for checking and managing idempotency of operations.

    Provides both cache-based (fast) and database-based (reliable)
    idempotency checking.

    For framework-agnostic usage, provide lookup callbacks when calling check().

    Example:
        # Framework-agnostic usage with TimeProvider
        from selfhealing.core.time_provider import MockTimeProvider

        service = IdempotencyService(time_provider=MockTimeProvider())
        key = IdempotencyKey.for_operation("order", 123, "process")
        result = service.check(key, lookup_fn=my_lookup)
    """

    def __init__(
        self,
        cache_ttl: int | None = None,
        time_provider: TimeProvider | None = None,
        clock_skew_tolerance_seconds: float | None = None,
    ):
        """
        Initialize the idempotency service.

        Args:
            cache_ttl: Custom cache TTL in seconds
            time_provider: TimeProvider for testable time operations
            clock_skew_tolerance_seconds: Clock skew tolerance for distributed checks
        """

        config = get_config()
        self._default_cache_ttl = config.idempotency.default_cache_ttl
        self._extended_cache_ttl = config.idempotency.extended_cache_ttl
        self.cache_ttl = cache_ttl or self._default_cache_ttl

        # Clock skew tolerance
        self._clock_skew_tolerance = (
            clock_skew_tolerance_seconds
            if clock_skew_tolerance_seconds is not None
            else config.idempotency.clock_skew_tolerance_seconds
        )
        self._time_provider: TimeProvider = time_provider or get_time_provider()
        self._cache = None  # Lazy initialized

    def _get_cache(self):
        """Django cache 인터페이스를 lazy load합니다."""
        if self._cache is None:
            try:
                from django.core.cache import cache

                self._cache = cache
            except ImportError:
                # Django not available - use noop cache
                class NoopCache:
                    def get(self, key):
                        return None

                    def set(self, key, value, timeout=None):
                        pass

                    def delete(self, key):
                        pass

                self._cache = NoopCache()
        return self._cache

    @property
    def DEFAULT_CACHE_TTL(self) -> int:
        """Default TTL for cache-based idempotency."""
        return self._default_cache_ttl

    @property
    def EXTENDED_CACHE_TTL(self) -> int:
        """Extended TTL for operations requiring longer TTL."""
        return self._extended_cache_ttl

    @property
    def clock_skew_tolerance(self) -> float:
        """Clock skew tolerance in seconds for distributed checks."""
        return self._clock_skew_tolerance

    @property
    def time_provider(self) -> TimeProvider:
        """Get the time provider for this service."""
        return self._time_provider

    def now(self) -> datetime:
        """
        Get current time using the configured TimeProvider.

        Returns:
            Current datetime from time provider
        """
        return self._time_provider.now()

    def is_timestamp_valid(
        self,
        timestamp: datetime,
        tolerance_seconds: float | None = None,
    ) -> bool:
        """
        Check if a timestamp is within acceptable clock skew tolerance.

        Useful for validating incoming events or API requests where
        the timestamp may differ due to clock skew between systems.

        Args:
            timestamp: The timestamp to validate
            tolerance_seconds: Override tolerance (uses config default if None)

        Returns:
            True if timestamp is within tolerance of current time
        """

        tolerance = tolerance_seconds if tolerance_seconds is not None else self._clock_skew_tolerance
        return self._time_provider.is_within_tolerance(
            timestamp,
            timedelta(seconds=tolerance),
        )

    def check(
        self,
        key: IdempotencyKey,
        lookup_fn: Callable[..., Any] | None = None,
        cache_ttl: int | None = None,
    ) -> IdempotencyResult:
        """
        Check if an operation has already been processed.

        Args:
            key: The idempotency key to check
            lookup_fn: Optional callback to check database
            cache_ttl: Optional custom TTL for cache

        Returns:
            IdempotencyResult with duplicate status

        Note:
            Gracefully degrades to DB-only check if Redis is unavailable.
        """
        ttl = cache_ttl or self.cache_ttl
        cache = self._get_cache()

        # Check cache first (fast path) with graceful degradation
        try:
            cached_value = cache.get(key.cache_key)
            if cached_value:
                logger.debug(
                    "idempotency.cache_hit",
                    key=key.key,
                )
                return IdempotencyResult(
                    is_duplicate=True,
                    existing_record=cached_value,
                    message="Found in cache",
                )
        except Exception as e:
            logger.warning(
                "idempotency.cache_unavailable_falling_back",
                error=e,
            )

        # Check database if lookup provided
        if lookup_fn:
            try:
                existing = lookup_fn(**key.components)
                if existing:
                    # Update cache for future lookups (best-effort)
                    try:
                        record_id = getattr(existing, "id", existing)
                        cache.set(key.cache_key, record_id, timeout=ttl)
                    except Exception:
                        pass
                    logger.debug(
                        "idempotency.db_hit",
                        key=key.key,
                    )
                    return IdempotencyResult(
                        is_duplicate=True,
                        existing_record=existing,
                        message="Found in database",
                    )
            except Exception as e:
                logger.warning(
                    "idempotency.lookup_failed",
                    error=e,
                )

        return IdempotencyResult(
            is_duplicate=False,
            message="Not found",
        )

    def check_event(self, event_id: str, exists_fn: Callable[[str], bool] | None = None) -> IdempotencyResult:
        """
        Check if an event has already been processed.

        Args:
            event_id: The unique event ID
            exists_fn: Optional callback(event_id) -> bool to check if event exists

        Returns:
            IdempotencyResult with duplicate status
        """
        key = IdempotencyKey.for_event(event_id)
        cache = self._get_cache()

        # Check cache with graceful degradation
        try:
            if cache.get(key.cache_key):
                logger.info(
                    "idempotency.duplicate_event_detected_cache",
                    event_id=event_id,
                )
                return IdempotencyResult(
                    is_duplicate=True,
                    message="Event already processed (cached)",
                )
        except Exception as e:
            logger.warning(
                "idempotency.cache_unavailable_event_check",
                error=e,
            )

        # Check database if lookup provided
        if exists_fn:
            try:
                exists = exists_fn(event_id)
                if exists:
                    try:
                        cache.set(key.cache_key, True, timeout=self.cache_ttl)
                    except Exception:
                        pass
                    logger.info(
                        "idempotency.duplicate_event_detected_db",
                        event_id=event_id,
                    )
                    return IdempotencyResult(
                        is_duplicate=True,
                        message="Event already processed (database)",
                    )
            except Exception as e:
                logger.warning(
                    "idempotency.event_lookup_failed",
                    error=e,
                )

        return IdempotencyResult(
            is_duplicate=False,
            message="Event not yet processed",
        )

    def mark_as_processed(
        self,
        key: IdempotencyKey,
        record_id: int | None = None,
        ttl: int | None = None,
    ) -> bool:
        """
        Mark an operation as processed in the cache.

        Call this after successfully completing an operation.

        Args:
            key: The idempotency key
            record_id: Optional record ID to cache
            ttl: Optional custom TTL

        Returns:
            True if cache was updated, False if cache was unavailable.
            The operation is still considered successful even if cache fails,
            as the DB is the source of truth.
        """
        cache = self._get_cache()
        value = record_id if record_id else True
        try:
            cache.set(key.cache_key, value, timeout=ttl or self.cache_ttl)
            logger.debug(
                "idempotency.marked_processed",
                key=key.cache_key,
            )
            return True
        except Exception as e:
            # Redis unavailable - log but don't fail the operation
            logger.warning(
                "idempotency.failed_mark_processed_cache",
                error=e,
            )
            return False

    def clear(self, key: IdempotencyKey) -> bool:
        """
        Clear an idempotency key from cache.

        Use with caution - only for cleanup or testing.

        Args:
            key: The idempotency key to clear

        Returns:
            True if cache was cleared, False if cache was unavailable.
        """
        cache = self._get_cache()
        try:
            cache.delete(key.cache_key)
            logger.debug(
                "idempotency.cleared",
                key=key.cache_key,
            )
            return True
        except Exception as e:
            logger.warning(
                "idempotency.failed_clear_key_cache",
                error=e,
            )
            return False


# Singleton instance
_service: IdempotencyService | None = None


def get_idempotency_service() -> IdempotencyService:
    """Get the singleton IdempotencyService instance."""
    global _service
    if _service is None:
        _service = IdempotencyService()
    return _service
