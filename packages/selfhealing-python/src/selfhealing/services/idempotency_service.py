"""
Idempotency Service

Provides idempotency key management for safe retry operations.
Ensures that retried operations produce the same result as the original.

Idempotency Key Format Examples (domain-neutral):
- Operation: `entity_type` + `entity_id` + `action`
- Event: `event_id` or `event_type:entity_id`
- Resource: `resource_type` + `resource_id` + `operation`

Usage:
    service = IdempotencyService()
    key = IdempotencyKey.for_operation("order", 123, "process")
    result = service.check(key, lookup_fn)

Reference: docs/L3_SELF_HEALING_ARCHITECTURE.md §7
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, Generic, TypeVar, Optional, Callable

from selfhealing.core.timezone import now
from selfhealing.core.config import get_config
from selfhealing.core.time_provider import TimeProvider, get_time_provider

if TYPE_CHECKING:
    from selfhealing.interfaces import CacheProviderInterface
    from selfhealing.core.time_provider import TimeProvider

logger = logging.getLogger(__name__)

T = TypeVar("T")


class IdempotencyDomain(Enum):
    """Domains that support idempotency checking (domain-neutral)."""

    EXTERNAL_SERVICE = "external_service"
    INTERNAL_PROCESS = "internal_process"
    ASYNC_TASK = "async_task"
    EVENT = "event"
    CUSTOM = "custom"


@dataclass
class IdempotencyKey:
    """
    Represents an idempotency key with domain context.

    The key is a combination of domain-specific identifiers that
    uniquely identify an operation.
    """

    domain: IdempotencyDomain
    key: str
    components: dict[str, Any]

    @property
    def cache_key(self) -> str:
        """Get the cache key for Redis/memcached storage."""
        return f"idempotency:{self.domain.value}:{self.key}"

    @property
    def hash(self) -> str:
        """Get a hash of the key for indexing."""
        return hashlib.sha256(self.cache_key.encode()).hexdigest()[:32]

    @classmethod
    def for_operation(
        cls,
        entity_type: str,
        entity_id: int,
        operation: str,
        domain: IdempotencyDomain = IdempotencyDomain.EXTERNAL_SERVICE,
    ) -> "IdempotencyKey":
        """
        Create an idempotency key for a generic operation.

        Args:
            entity_type: Type of entity (e.g., "order", "user", "product")
            entity_id: The entity ID
            operation: The operation being performed (e.g., "process", "update")
            domain: The domain category

        Returns:
            IdempotencyKey for the operation
        """
        key = f"{entity_type}:{entity_id}:{operation}"
        return cls(
            domain=domain,
            key=key,
            components={
                "entity_type": entity_type,
                "entity_id": entity_id,
                "operation": operation,
            },
        )

    @classmethod
    def for_event(cls, event_id: str) -> "IdempotencyKey":
        """
        Create an idempotency key for event processing.

        Args:
            event_id: The unique event ID

        Returns:
            IdempotencyKey for the event
        """
        return cls(
            domain=IdempotencyDomain.EVENT,
            key=event_id,
            components={"event_id": event_id},
        )

    @classmethod
    def for_resource_action(
        cls,
        resource_type: str,
        resource_id: int,
        action: str,
        amount: Optional[int] = None,
    ) -> "IdempotencyKey":
        """
        Create an idempotency key for resource actions.

        Args:
            resource_type: Type of resource
            resource_id: The resource ID
            action: The action being performed
            amount: Optional amount for the action

        Returns:
            IdempotencyKey for the resource action
        """
        if amount is not None:
            key = f"{resource_type}:{resource_id}:{action}:{amount}"
        else:
            key = f"{resource_type}:{resource_id}:{action}"
        return cls(
            domain=IdempotencyDomain.INTERNAL_PROCESS,
            key=key,
            components={
                "resource_type": resource_type,
                "resource_id": resource_id,
                "action": action,
                "amount": amount,
            },
        )

    @classmethod
    def custom(cls, key: str, **components: Any) -> "IdempotencyKey":
        """
        Create a custom idempotency key.

        Args:
            key: The raw key string
            **components: Key components for debugging

        Returns:
            IdempotencyKey with custom domain
        """
        return cls(
            domain=IdempotencyDomain.CUSTOM,
            key=key,
            components=components,
        )


@dataclass
class IdempotencyResult(Generic[T]):
    """Result of an idempotency check."""

    is_duplicate: bool
    existing_record: T | None = None
    message: str = ""

    @property
    def should_proceed(self) -> bool:
        """Whether the operation should proceed (not a duplicate)."""
        return not self.is_duplicate


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
        time_provider: Optional["TimeProvider"] = None,
        clock_skew_tolerance_seconds: Optional[float] = None,
    ):
        """
        Initialize the idempotency service.

        Args:
            cache_ttl: Custom cache TTL in seconds
            time_provider: TimeProvider for testable time operations
            clock_skew_tolerance_seconds: Clock skew tolerance for distributed checks
        """
        from selfhealing.core.config import get_config
        from selfhealing.core.time_provider import TimeProvider, get_time_provider

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
    def time_provider(self) -> "TimeProvider":
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
        tolerance_seconds: Optional[float] = None,
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
        from datetime import timedelta

        tolerance = tolerance_seconds if tolerance_seconds is not None else self._clock_skew_tolerance
        return self._time_provider.is_within_tolerance(
            timestamp,
            timedelta(seconds=tolerance),
        )

    def check(
        self,
        key: IdempotencyKey,
        lookup_fn: Optional[Callable[..., Any]] = None,
        cache_ttl: Optional[int] = None,
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

        # Check cache first (fast path) with graceful degradation
        try:
            cached_value = cache.get(key.cache_key)
            if cached_value:
                logger.debug(f"[Idempotency] Cache hit: {key.key}")
                return IdempotencyResult(
                    is_duplicate=True,
                    existing_record=cached_value,
                    message="Found in cache",
                )
        except Exception as e:
            logger.warning(f"[Idempotency] Cache unavailable, falling back to DB: {e}")

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
                    logger.debug(f"[Idempotency] DB hit: {key.key}")
                    return IdempotencyResult(
                        is_duplicate=True,
                        existing_record=existing,
                        message="Found in database",
                    )
            except Exception as e:
                logger.warning(f"[Idempotency] Lookup failed: {e}")

        return IdempotencyResult(
            is_duplicate=False,
            message="Not found",
        )

    def check_event(self, event_id: str, exists_fn: Optional[Callable[[str], bool]] = None) -> IdempotencyResult:
        """
        Check if an event has already been processed.

        Args:
            event_id: The unique event ID
            exists_fn: Optional callback(event_id) -> bool to check if event exists

        Returns:
            IdempotencyResult with duplicate status
        """
        key = IdempotencyKey.for_event(event_id)

        # Check cache with graceful degradation
        try:
            if cache.get(key.cache_key):
                logger.info(f"[Idempotency] Duplicate event detected (cache): {event_id}")
                return IdempotencyResult(
                    is_duplicate=True,
                    message="Event already processed (cached)",
                )
        except Exception as e:
            logger.warning(f"[Idempotency] Cache unavailable for event check: {e}")

        # Check database if lookup provided
        if exists_fn:
            try:
                exists = exists_fn(event_id)
                if exists:
                    try:
                        cache.set(key.cache_key, True, timeout=self.cache_ttl)
                    except Exception:
                        pass
                    logger.info(f"[Idempotency] Duplicate event detected (DB): {event_id}")
                    return IdempotencyResult(
                        is_duplicate=True,
                        message="Event already processed (database)",
                    )
            except Exception as e:
                logger.warning(f"[Idempotency] Event lookup failed: {e}")

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
        value = record_id if record_id else True
        try:
            cache.set(key.cache_key, value, timeout=ttl or self.cache_ttl)
            logger.debug(f"[Idempotency] Marked as processed: {key.cache_key}")
            return True
        except Exception as e:
            # Redis unavailable - log but don't fail the operation
            logger.warning(f"[Idempotency] Failed to mark as processed (cache unavailable): {e}")
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
        try:
            cache.delete(key.cache_key)
            logger.debug(f"[Idempotency] Cleared: {key.cache_key}")
            return True
        except Exception as e:
            logger.warning(f"[Idempotency] Failed to clear key (cache unavailable): {e}")
            return False


# Singleton instance
_service: IdempotencyService | None = None


def get_idempotency_service() -> IdempotencyService:
    """Get the singleton IdempotencyService instance."""
    global _service
    if _service is None:
        _service = IdempotencyService()
    return _service
