"""
Redis Failure Scenarios Tests

This module tests the self-healing system's behavior when Redis is unavailable.
Redis is used as both a Celery broker and a cache backend, so failures need
careful handling to maintain system availability.

Test Coverage:
- Circuit Breaker graceful degradation when cache is unavailable
- Idempotency service fallback behavior on Redis failure
- DLQ service database fallback when Redis cache fails
- Rate limit tracker behavior during cache outages

Key Principles:
- Redis failures should NOT block critical operations (like payments)
- Circuit Breaker should default to CLOSED (available) on cache miss
- Idempotency checks should skip (allow processing) on cache failure
- DLQ should always write to database regardless of cache state

Reference:
- docs/SELF_HEALING_TEST_GAP_ANALYSIS.md §2.3
- docs/L3_SELF_HEALING_ARCHITECTURE.md §6 (Cache Strategy)

Note: This module is skipped - infrastructure tests require Redis.
"""

import pytest

# Skip entire module - Infrastructure tests require Redis
pytestmark = pytest.mark.requires_redis
from unittest.mock import patch, MagicMock, PropertyMock
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError


@pytest.mark.django_db
class TestCircuitBreakerRedisFailure:
    """
    Tests for Circuit Breaker behavior when Redis cache is unavailable.

    The Circuit Breaker uses Redis to cache state for distributed consistency.
    When Redis fails, the Circuit Breaker should:
    - Default to CLOSED (available) state
    - Continue to function using database as source of truth
    - Log the cache failure for monitoring
    """

    def test_circuit_breaker_defaults_to_available_on_cache_miss(self):
        """
        Verify Circuit Breaker returns 'available' when cache read fails.

        When Redis is down, we should fail-open (allow requests) rather than
        fail-closed (block requests). This prevents Redis issues from causing
        complete service outages.

        Expected behavior:
        - Cache get() fails with ConnectionError
        - Circuit Breaker falls back to database lookup
        - If no DB record exists, default to CLOSED (available)
        """
        from selfhealing.services import (
            CircuitBreakerService,
        )

        with patch("django.core.cache.cache.get", side_effect=RedisConnectionError("Connection refused")):
            service = CircuitBreakerService()

            # should_allow returns True (available) even when cache fails
            # Falls back to database or defaults to CLOSED state
            is_available = service.should_allow("test_service")

            assert is_available is True

    def test_circuit_breaker_continues_with_db_state_on_cache_failure(self):
        """
        Verify Circuit Breaker uses database state when cache is unavailable.

        The Circuit Breaker maintains state in both cache (for speed) and
        database (for durability). When cache fails, database is the fallback.

        Expected behavior:
        - Cache operations fail
        - Service reads state from database
        - Correct state is returned based on DB record
        """
        from shopping.models.failed_payment import CircuitBreakerState
        from selfhealing.services import (
            CircuitBreakerService,
        )

        # Create a CLOSED (available) state in database
        # CircuitBreakerState uses 'state' field, not 'is_open'
        cb_state, _ = CircuitBreakerState.objects.get_or_create(
            service_name="db_fallback_test",
            defaults={
                "state": "closed",  # closed = available
                "failure_count": 0,
            },
        )

        with patch("django.core.cache.cache.get", side_effect=RedisConnectionError("Connection refused")):
            with patch("django.core.cache.cache.set", side_effect=RedisConnectionError("Connection refused")):
                service = CircuitBreakerService()

                # Should read from database and return available (closed state)
                is_available = service.should_allow("db_fallback_test")
                assert is_available is True

    def test_circuit_breaker_force_open_persists_to_db_on_cache_failure(self):
        """
        Verify force_open writes to database even when cache is down.

        Critical operations like force_open must persist to database regardless
        of cache availability. This ensures operational control is maintained.

        Expected behavior:
        - Cache set() fails
        - Database write succeeds
        - State change is durable
        """
        from shopping.models.failed_payment import CircuitBreakerState
        from selfhealing.services import (
            CircuitBreakerService,
        )

        # Clean up any existing state
        CircuitBreakerState.objects.filter(service_name="force_open_test").delete()

        with patch("django.core.cache.cache.set", side_effect=RedisConnectionError("Connection refused")):
            with patch("django.core.cache.cache.delete", side_effect=RedisConnectionError("Connection refused")):
                service = CircuitBreakerService()

                # Force open should succeed despite cache failure
                service.force_open("force_open_test", reason="Test force open")

                # Verify database state - check 'state' field, not 'is_open'
                cb_state = CircuitBreakerState.objects.get(service_name="force_open_test")
                assert cb_state.state == "open"


@pytest.mark.django_db
class TestIdempotencyServiceRedisFailure:
    """
    Tests for Idempotency Service behavior when Redis cache is unavailable.

    The Idempotency Service uses Redis to track processed operations and
    prevent duplicate processing. When Redis fails:
    - Check operations should fall back to database
    - Payment idempotency is also enforced by Toss API and DB constraints

    The IdempotencyService implements graceful degradation:
    - Cache failures are caught and logged
    - DB-only checks continue to work
    - Duplicate detection still functions via database
    """

    def test_idempotency_check_payment_falls_back_to_db_on_cache_failure(self):
        """
        Verify IdempotencyService gracefully degrades to DB when cache fails.

        When cache.get() raises an exception, the service should:
        - Catch the exception and log a warning
        - Fall back to database-only idempotency check
        - Continue to detect duplicates via DB queries

        This ensures the self-healing system works even during Redis outages.
        """
        from selfhealing.services import (
            IdempotencyService,
        )
        from shopping.tests.factories import OrderFactory, PaymentFactory, UserFactory

        user = UserFactory()
        order = OrderFactory(user=user)
        # Create an existing payment that should be detected via DB
        payment = PaymentFactory(order=order, status="done", amount=10000)

        with patch("django.core.cache.cache.get", side_effect=RedisConnectionError("Connection refused")):
            service = IdempotencyService()

            # Should NOT raise exception - graceful degradation
            result = service.check_payment(order_id=order.id, amount=10000)

            # Should still detect duplicate via database
            assert result.is_duplicate is True
            assert result.existing_record is not None
            assert result.existing_record.id == payment.id

    def test_idempotency_check_payment_no_duplicate_with_cache_failure(self):
        """
        Verify non-duplicate detection works when cache fails.

        When cache fails and no existing payment in DB:
        - Should return is_duplicate=False
        - Operation can proceed
        """
        from selfhealing.services import (
            IdempotencyService,
        )
        from shopping.tests.factories import OrderFactory, UserFactory

        user = UserFactory()
        order = OrderFactory(user=user)
        # No payment exists

        with patch("django.core.cache.cache.get", side_effect=RedisConnectionError("Connection refused")):
            with patch("django.core.cache.cache.set", side_effect=RedisConnectionError("Connection refused")):
                service = IdempotencyService()

                # Should NOT raise exception
                result = service.check_payment(order_id=order.id, amount=10000)

                # Should return not duplicate
                assert result.is_duplicate is False
                assert result.should_proceed is True

    def test_idempotency_mark_as_processed_gracefully_handles_cache_failure(self):
        """
        Verify mark_as_processed gracefully handles cache failure.

        When cache.set() fails, the service should:
        - Catch the exception and log a warning
        - Return False to indicate cache was not updated
        - NOT raise an exception (the main operation succeeded)

        This ensures completed operations are not failed due to cache issues.
        """
        from selfhealing.services import (
            IdempotencyService,
            IdempotencyKey,
            IdempotencyDomain,
        )

        with patch("django.core.cache.cache.set", side_effect=RedisConnectionError("Connection refused")):
            service = IdempotencyService()
            key = IdempotencyKey(
                domain=IdempotencyDomain.PAYMENT,
                key="test_order_456:20000",
                components={"order_id": 456, "amount": 20000},
            )

            # Should NOT raise exception - returns False instead
            result = service.mark_as_processed(key, record_id=123)

            # Returns False to indicate cache update failed
            assert result is False

    def test_idempotency_check_with_working_db_and_cache(self):
        """
        Verify idempotency check works with both DB and cache available.

        This is the happy path test to ensure normal operation works.

        Expected behavior:
        - Cache returns None (miss)
        - Database check finds existing payment
        - Returns is_duplicate=True
        """
        from selfhealing.services import (
            IdempotencyService,
        )
        from shopping.tests.factories import OrderFactory, PaymentFactory, UserFactory

        user = UserFactory()
        order = OrderFactory(user=user)
        payment = PaymentFactory(order=order, status="done", amount=10000)

        service = IdempotencyService()

        # With working cache and DB, should detect duplicate
        result = service.check_payment(order_id=order.id, amount=10000)

        assert result.is_duplicate is True
        assert result.existing_record is not None


@pytest.mark.django_db
class TestDLQServiceRedisFailure:
    """
    Tests for DLQ Service behavior when Redis cache is unavailable.

    The DLQ Service primarily uses the database for storage, with Redis
    used for caching pending counts and status. When Redis fails:
    - Core DLQ operations (store, retrieve) must continue via database
    - Cached metrics may be stale or unavailable
    - This should not block failure recovery operations
    """

    def test_dlq_store_succeeds_without_cache(self):
        """
        Verify DLQ entries are stored to database despite cache failure.

        The DLQ is critical for failure recovery. Database writes must succeed
        regardless of cache state. Cache is only for performance optimization.

        Expected behavior:
        - Cache operations fail
        - Database write succeeds
        - DLQ entry is persisted
        """
        from shopping.models.failed_operation import FailedOperation
        from selfhealing.services import DLQService
        from shopping.tests.factories import OrderFactory, UserFactory

        user = UserFactory()
        order = OrderFactory(user=user)

        with patch("django.core.cache.cache.set", side_effect=RedisConnectionError("Connection refused")):
            with patch("django.core.cache.cache.delete", side_effect=RedisConnectionError("Connection refused")):
                service = DLQService()

                # Store failure should succeed via database
                # Using correct API parameters: domain, failure_type, error_message
                result = service.store_failure(
                    domain="payment",
                    failure_type="PG_TIMEOUT",
                    order=order,
                    error_message="Test error for Redis failure scenario",
                    metadata={"test": "data"},
                )

                assert result.success is True
                assert result.dlq_id is not None

                # Verify database record exists
                assert FailedOperation.objects.filter(id=result.dlq_id).exists()

    def test_dlq_retrieve_works_without_cache(self):
        """
        Verify DLQ entries can be retrieved when cache is unavailable.

        Retrieval operations must work for operators to review and replay
        failed operations, even during cache outages.

        Expected behavior:
        - Cache get() fails
        - Database query succeeds
        - Entries are returned
        """
        from shopping.models.failed_operation import FailedOperation
        from selfhealing.services import DLQService
        from shopping.tests.factories import OrderFactory, UserFactory

        # Create a test DLQ entry directly
        user = UserFactory()
        order = OrderFactory(user=user)

        failed_op = FailedOperation.objects.create(
            domain="payment",
            failure_type="test_retrieve",
            status="pending",
            entity_type="order",
            entity_id=order.id,
            error_message="Test entry for retrieve test",
        )

        with patch("django.core.cache.cache.get", side_effect=RedisConnectionError("Connection refused")):
            service = DLQService()

            # Get pending should work via database
            pending = service.get_pending_entries()

            # Should find the entry we created
            pending_ids = [e.id for e in pending]
            assert failed_op.id in pending_ids

    def test_dlq_pending_count_falls_back_to_db_query(self):
        """
        Verify pending count calculation works without cached value.

        The pending count may be cached for performance. When cache is
        unavailable, a database COUNT query should be used instead.

        Expected behavior:
        - Cached count unavailable
        - Database COUNT query executed
        - Correct count returned
        """
        from shopping.models.failed_operation import FailedOperation
        from selfhealing.services import DLQService
        from shopping.tests.factories import OrderFactory, UserFactory

        # Clear existing entries and create known count
        FailedOperation.objects.filter(failure_type="count_test").delete()

        user = UserFactory()
        for i in range(3):
            order = OrderFactory(user=user)
            FailedOperation.objects.create(
                domain="payment",
                failure_type="count_test",
                status="pending",
                entity_type="order",
                entity_id=order.id,
                error_message=f"Test entry {i}",
            )

        with patch("django.core.cache.cache.get", return_value=None):  # Cache miss
            service = DLQService()

            # Should fall back to database query
            all_pending = service.get_pending_entries(failure_type="count_test")

            assert len(list(all_pending)) == 3


@pytest.mark.django_db
class TestRateLimitTrackerCacheIndependence:
    """
    Tests for Rate Limit Tracker behavior during cache outages.

    The Rate Limit Tracker uses in-memory storage (not Redis) for
    performance-critical rate limit detection. This ensures rate limiting
    works independently of cache availability.
    """

    def test_rate_limit_tracker_uses_memory_not_redis(self):
        """
        Verify Rate Limit Tracker operates independently of Redis.

        The tracker uses thread-safe in-memory storage to avoid Redis
        latency in the hot path. This means it continues working during
        Redis outages.

        Expected behavior:
        - Rate limit events are recorded in memory
        - No Redis calls made
        - Counts are accurate
        """
        from selfhealing.services import (
            get_rate_limit_tracker,
        )

        tracker = get_rate_limit_tracker()
        service_name = "memory_test_service"

        # Clear any existing data
        tracker.clear_service(service_name)

        with patch("django.core.cache.cache.get", side_effect=RedisConnectionError("Connection refused")):
            with patch("django.core.cache.cache.set", side_effect=RedisConnectionError("Connection refused")):
                # Record some rate limit events
                tracker.record_rate_limit(service_name)
                tracker.record_rate_limit(service_name)
                tracker.record_rate_limit(service_name)

                # Should work without Redis
                count = tracker.get_rate_limit_count(service_name, window_seconds=60)

                assert count == 3

    def test_backoff_level_tracking_works_without_redis(self):
        """
        Verify backoff level management works during cache outages.

        Backoff levels are used to implement adaptive retry strategies.
        These must continue to function during Redis failures.

        Expected behavior:
        - Backoff levels are tracked in memory
        - Increment and reset operations work
        - No Redis dependency
        """
        from selfhealing.services import (
            get_rate_limit_tracker,
        )

        tracker = get_rate_limit_tracker()
        service_name = "backoff_test_service"

        # Clear and reset
        tracker.clear_service(service_name)

        with patch("django.core.cache.cache.get", side_effect=RedisConnectionError("Connection refused")):
            # Increment backoff levels
            level1 = tracker.increment_backoff(service_name)
            level2 = tracker.increment_backoff(service_name)
            level3 = tracker.get_backoff_level(service_name)

            assert level1 == 1
            assert level2 == 2
            assert level3 == 2

            # Reset works
            tracker.reset_backoff(service_name)
            assert tracker.get_backoff_level(service_name) == 0


@pytest.mark.django_db
class TestCascadePreventionDuringRedisOutage:
    """
    Tests to ensure Redis failures don't cascade to other system components.

    Redis failures should be isolated to cache-dependent features.
    Core business operations (payments, orders) must continue to function.
    """

    def test_payment_flow_continues_during_redis_outage(self, mocker):
        """
        Verify payment processing continues when Redis is unavailable.

        This is the most critical test: payments must process even when
        the entire caching layer is down. Redis is a performance optimization,
        not a requirement for correctness.

        Expected behavior:
        - Redis operations fail
        - Payment confirmation succeeds
        - Order status is updated
        - Customer is not blocked
        """
        from shopping.services.payment_service import PaymentService
        from shopping.tests.factories import (
            OrderFactory,
            OrderItemFactory,
            PaymentFactory,
            ProductFactory,
            TossResponseBuilder,
            UserFactory,
        )

        user = UserFactory.with_points(5000)
        product = ProductFactory(stock=10)
        order = OrderFactory(user=user, status="confirmed", total_amount=product.price)
        OrderItemFactory(order=order, product=product)
        payment = PaymentFactory(order=order, status="ready")

        # Mock Toss API success
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            return_value=TossResponseBuilder.success_response(),
        )

        # Simulate Redis failure for all cache operations
        mocker.patch("django.core.cache.cache.get", side_effect=RedisConnectionError("Connection refused"))
        mocker.patch("django.core.cache.cache.set", side_effect=RedisConnectionError("Connection refused"))
        mocker.patch("django.core.cache.cache.delete", side_effect=RedisConnectionError("Connection refused"))

        # Payment should succeed despite Redis failure
        result = PaymentService.confirm_payment_sync(
            payment=payment,
            payment_key="test_payment_key",
            order_id=order.id,
            amount=int(order.total_amount),
            user=user,
        )

        assert result["payment"].status == "done"

    def test_multiple_redis_error_types_handled(self):
        """
        Verify various Redis error types are all handled gracefully.

        Different Redis failure modes should all result in graceful
        degradation, not system failures.

        Expected behavior:
        - ConnectionError: Handled
        - TimeoutError: Handled
        - BrokenPipeError wrapped in Redis error: Handled
        """
        from selfhealing.services import (
            CircuitBreakerService,
        )

        error_types = [
            RedisConnectionError("Connection refused"),
            RedisTimeoutError("Read timeout"),
            RedisConnectionError("Connection reset by peer"),
        ]

        for error in error_types:
            with patch("django.core.cache.cache.get", side_effect=error):
                service = CircuitBreakerService()

                # Should not raise, should return safe default
                try:
                    is_available = service.should_allow("error_type_test")
                    # Should default to available (closed state)
                    assert is_available is True
                except (RedisConnectionError, RedisTimeoutError):
                    pytest.fail(f"Circuit Breaker should handle {type(error).__name__} gracefully")
