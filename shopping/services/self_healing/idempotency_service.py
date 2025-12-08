"""
Idempotency Service

Provides idempotency key management for safe retry operations.
Ensures that retried operations produce the same result as the original.

Idempotency Key Format by Domain:
- Payment: `toss_order_id` + `payment_key`
- Webhook: `event_id` from PG
- Points: `order_id` + `type` + `timestamp`
- Inventory: `order_item_id` + `action`
- Notification: `user_id` + `type` + `reference_id`

Reference: docs/L3_SELF_HEALING_ARCHITECTURE.md §7
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from django.core.cache import cache
from django.db import models
from django.utils import timezone

if TYPE_CHECKING:
    from shopping.models.payment import Payment
    from shopping.models.point import PointHistory

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=models.Model)


class IdempotencyDomain(Enum):
    """Domains that support idempotency checking."""

    PAYMENT = "payment"
    WEBHOOK = "webhook"
    POINT = "point"
    INVENTORY = "inventory"
    NOTIFICATION = "notification"


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
    def for_payment(cls, order_id: int, amount: int) -> "IdempotencyKey":
        """
        Create an idempotency key for payment operations.

        Args:
            order_id: The order ID
            amount: The payment amount in KRW

        Returns:
            IdempotencyKey for the payment
        """
        key = f"{order_id}:{amount}"
        return cls(
            domain=IdempotencyDomain.PAYMENT,
            key=key,
            components={"order_id": order_id, "amount": amount},
        )

    @classmethod
    def for_payment_confirm(cls, payment_key: str, order_id: int, amount: int) -> "IdempotencyKey":
        """
        Create an idempotency key for payment confirmation.

        Args:
            payment_key: Toss payment key
            order_id: The order ID
            amount: The payment amount

        Returns:
            IdempotencyKey for payment confirmation
        """
        key = f"confirm:{payment_key}:{order_id}:{amount}"
        return cls(
            domain=IdempotencyDomain.PAYMENT,
            key=key,
            components={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": amount,
            },
        )

    @classmethod
    def for_webhook(cls, event_id: str) -> "IdempotencyKey":
        """
        Create an idempotency key for webhook processing.

        Args:
            event_id: The unique event ID from the PG

        Returns:
            IdempotencyKey for the webhook
        """
        return cls(
            domain=IdempotencyDomain.WEBHOOK,
            key=event_id,
            components={"event_id": event_id},
        )

    @classmethod
    def for_point_operation(
        cls,
        order_id: int,
        point_type: str,
        amount: int,
    ) -> "IdempotencyKey":
        """
        Create an idempotency key for point operations.

        Args:
            order_id: The order ID
            point_type: Type of point operation (earn, use, refund)
            amount: Point amount

        Returns:
            IdempotencyKey for the point operation
        """
        key = f"{order_id}:{point_type}:{amount}"
        return cls(
            domain=IdempotencyDomain.POINT,
            key=key,
            components={
                "order_id": order_id,
                "point_type": point_type,
                "amount": amount,
            },
        )

    @classmethod
    def for_inventory(
        cls,
        order_item_id: int,
        action: str,
    ) -> "IdempotencyKey":
        """
        Create an idempotency key for inventory operations.

        Args:
            order_item_id: The order item ID
            action: The action (deduct, restore)

        Returns:
            IdempotencyKey for the inventory operation
        """
        key = f"{order_item_id}:{action}"
        return cls(
            domain=IdempotencyDomain.INVENTORY,
            key=key,
            components={"order_item_id": order_item_id, "action": action},
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
    """

    def __init__(self, cache_ttl: int | None = None):
        """
        Initialize the idempotency service.

        Args:
            cache_ttl: Custom cache TTL in seconds
        """
        from shopping.services.self_healing.config import get_idempotency_config

        config = get_idempotency_config()
        self._default_cache_ttl = config.default_cache_ttl
        self._payment_cache_ttl = config.payment_cache_ttl
        self.cache_ttl = cache_ttl or self._default_cache_ttl

    @property
    def DEFAULT_CACHE_TTL(self) -> int:
        """Default TTL for cache-based idempotency (for backward compatibility)."""
        return self._default_cache_ttl

    @property
    def PAYMENT_CACHE_TTL(self) -> int:
        """Extended TTL for payment operations (for backward compatibility)."""
        return self._payment_cache_ttl

    def check_payment(
        self,
        order_id: int,
        amount: int,
    ) -> IdempotencyResult["Payment"]:
        """
        Check if a payment for this order/amount already exists.

        Args:
            order_id: The order ID
            amount: The payment amount

        Returns:
            IdempotencyResult with duplicate status
        """
        from shopping.models.payment import Payment

        key = IdempotencyKey.for_payment(order_id, amount)

        # Check cache first (fast path)
        cached_payment_id = cache.get(key.cache_key)
        if cached_payment_id:
            try:
                payment = Payment.objects.get(pk=cached_payment_id)
                logger.debug(f"[Idempotency] Cache hit for payment: {key.key}")
                return IdempotencyResult(
                    is_duplicate=True,
                    existing_record=payment,
                    message="Payment found in cache",
                )
            except Payment.DoesNotExist:
                # Cache is stale, delete it
                cache.delete(key.cache_key)

        # Check database (reliable path)
        existing = Payment.objects.filter(
            order_id=order_id,
            amount=amount,
            status__in=["done", "in_progress", "ready"],
        ).first()

        if existing:
            # Update cache for future lookups
            cache.set(key.cache_key, existing.id, timeout=self.PAYMENT_CACHE_TTL)
            logger.debug(f"[Idempotency] DB hit for payment: {key.key}")
            return IdempotencyResult(
                is_duplicate=True,
                existing_record=existing,
                message="Payment found in database",
            )

        return IdempotencyResult(
            is_duplicate=False,
            message="No existing payment found",
        )

    def check_payment_confirm(
        self,
        payment_key: str,
        order_id: int,
        amount: int,
    ) -> IdempotencyResult["Payment"]:
        """
        Check if a payment confirmation already succeeded.

        Args:
            payment_key: Toss payment key
            order_id: The order ID
            amount: The payment amount

        Returns:
            IdempotencyResult with duplicate status
        """
        from shopping.models.payment import Payment

        key = IdempotencyKey.for_payment_confirm(payment_key, order_id, amount)

        # Check cache first
        cached_payment_id = cache.get(key.cache_key)
        if cached_payment_id:
            try:
                payment = Payment.objects.get(pk=cached_payment_id, status="done")
                logger.info(f"[Idempotency] Duplicate confirm detected (cache): {key.key}")
                return IdempotencyResult(
                    is_duplicate=True,
                    existing_record=payment,
                    message="Payment already confirmed (cached)",
                )
            except Payment.DoesNotExist:
                cache.delete(key.cache_key)

        # Check database
        existing = Payment.objects.filter(
            payment_key=payment_key,
            order_id=order_id,
            amount=amount,
            status="done",
        ).first()

        if existing:
            cache.set(key.cache_key, existing.id, timeout=self.PAYMENT_CACHE_TTL)
            logger.info(f"[Idempotency] Duplicate confirm detected (DB): {key.key}")
            return IdempotencyResult(
                is_duplicate=True,
                existing_record=existing,
                message="Payment already confirmed (database)",
            )

        return IdempotencyResult(
            is_duplicate=False,
            message="Payment confirmation not yet processed",
        )

    def check_webhook(self, event_id: str) -> IdempotencyResult:
        """
        Check if a webhook event has already been processed.

        Args:
            event_id: The unique event ID from the PG

        Returns:
            IdempotencyResult with duplicate status
        """
        from shopping.models.webhook_event import WebhookEvent

        key = IdempotencyKey.for_webhook(event_id)

        # Check cache
        if cache.get(key.cache_key):
            logger.info(f"[Idempotency] Duplicate webhook detected (cache): {event_id}")
            return IdempotencyResult(
                is_duplicate=True,
                message="Webhook already processed (cached)",
            )

        # Check database
        exists = WebhookEvent.objects.filter(event_id=event_id).exists()

        if exists:
            # Cache for future lookups
            cache.set(key.cache_key, True, timeout=self.cache_ttl)
            logger.info(f"[Idempotency] Duplicate webhook detected (DB): {event_id}")
            return IdempotencyResult(
                is_duplicate=True,
                message="Webhook already processed (database)",
            )

        return IdempotencyResult(
            is_duplicate=False,
            message="Webhook not yet processed",
        )

    def check_point_operation(
        self,
        order_id: int,
        point_type: str,
        amount: int,
    ) -> IdempotencyResult["PointHistory"]:
        """
        Check if a point operation has already been processed.

        Args:
            order_id: The order ID
            point_type: Type of point operation
            amount: Point amount

        Returns:
            IdempotencyResult with duplicate status
        """
        from shopping.models.point import PointHistory

        key = IdempotencyKey.for_point_operation(order_id, point_type, amount)

        # Check cache
        cached_id = cache.get(key.cache_key)
        if cached_id:
            try:
                record = PointHistory.objects.get(pk=cached_id)
                logger.info(f"[Idempotency] Duplicate point op detected: {key.key}")
                return IdempotencyResult(
                    is_duplicate=True,
                    existing_record=record,
                    message="Point operation already processed (cached)",
                )
            except PointHistory.DoesNotExist:
                cache.delete(key.cache_key)

        # Check database
        existing = PointHistory.objects.filter(
            order_id=order_id,
            change_type=point_type,
            amount=amount,
        ).first()

        if existing:
            cache.set(key.cache_key, existing.id, timeout=self.cache_ttl)
            return IdempotencyResult(
                is_duplicate=True,
                existing_record=existing,
                message="Point operation already processed (database)",
            )

        return IdempotencyResult(
            is_duplicate=False,
            message="Point operation not yet processed",
        )

    def mark_as_processed(
        self,
        key: IdempotencyKey,
        record_id: int | None = None,
        ttl: int | None = None,
    ) -> None:
        """
        Mark an operation as processed in the cache.

        Call this after successfully completing an operation.

        Args:
            key: The idempotency key
            record_id: Optional record ID to cache
            ttl: Optional custom TTL
        """
        value = record_id if record_id else True
        cache.set(key.cache_key, value, timeout=ttl or self.cache_ttl)
        logger.debug(f"[Idempotency] Marked as processed: {key.cache_key}")

    def clear(self, key: IdempotencyKey) -> None:
        """
        Clear an idempotency key from cache.

        Use with caution - only for cleanup or testing.

        Args:
            key: The idempotency key to clear
        """
        cache.delete(key.cache_key)
        logger.debug(f"[Idempotency] Cleared: {key.cache_key}")


# Singleton instance
_service: IdempotencyService | None = None


def get_idempotency_service() -> IdempotencyService:
    """Get the singleton IdempotencyService instance."""
    global _service
    if _service is None:
        _service = IdempotencyService()
    return _service
