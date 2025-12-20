"""
L3 Self-Healing Phase 2 Integration Tests

Tests for retry logic, idempotency checking, and DLQ integration.
Validates the implementation of Architecture §7 (Idempotency) and §8 (Retry Strategy).

Test Categories:
    A. Backoff Calculator Tests:
        - Exponential backoff calculation
        - Maximum delay cap enforcement
        - Jitter distribution
    B. Retry Handler Tests:
        - Retry on transient errors
        - DLQ routing on max retries
        - Non-retryable error handling
    C. Idempotency Service Tests:
        - Payment idempotency checking
        - Webhook duplicate detection
        - Point operation deduplication
    D. Forensic Context Tests:
        - State snapshot capture
        - Retry history tracking
        - DLQ context completeness

Reference: docs/L3_SELF_HEALING_ARCHITECTURE.md §7, §8
"""

import threading
import time
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.db import connection

import pytest

# E2E integration test - requires database connection
pytestmark = [pytest.mark.e2e, pytest.mark.requires_db]

from shopping.models.failed_operation import FailedOperation
from shopping.models.order import Order
from shopping.models.payment import Payment
from selfhealing.core import (
    BackoffCalculator,
    BackoffConfig,
    calculate_backoff,
)
from selfhealing.services.backoff_calculator import get_calculator_for_domain
from selfhealing.services import ForensicContext
from selfhealing.core.forensic import create_snapshot_data
from selfhealing.services.forensic_context import (
    ForensicContextBuilder,
    capture_forensic_context,
)
from selfhealing.services import (
    IdempotencyKey,
    IdempotencyService,
    get_idempotency_service,
)
from selfhealing.services import (
    MaxRetriesExceededError,
    RetryAction,
    RetryConfig,
    RetryHandler,
    RetryResult,
)
from selfhealing.services.retry_handler import with_retry
from shopping.tests.factories import (
    OrderFactory,
    PaymentFactory,
    ProductFactory,
    UserFactory,
)


# =============================================================================
# Test Configuration
# =============================================================================


TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-self-healing",
    }
}


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear cache before and after each test."""
    cache.clear()
    yield
    cache.clear()


# =============================================================================
# A. Backoff Calculator Tests
# =============================================================================


class TestBackoffCalculator:
    """
    Tests for exponential backoff calculation.

    Validates:
    - Correct exponential sequence (base^n)
    - Maximum delay enforcement
    - Jitter adds randomness
    """

    def test_exponential_backoff_sequence(self):
        """
        Purpose:
            Verify exponential backoff produces correct delay sequence.
        Expected:
            Delays follow base^n pattern: 4, 16, 64 for base=4
        """
        config = BackoffConfig(base=4, max_delay=180, jitter_percent=0)
        calculator = BackoffCalculator(config)

        delays = calculator.get_delays_sequence(3, with_jitter=False)

        assert delays == [4, 16, 64], f"Expected [4, 16, 64], got {delays}"

    def test_maximum_delay_cap(self):
        """
        Purpose:
            Verify delays are capped at max_delay.
        Expected:
            Delay never exceeds max_delay even for high attempt numbers.
        """
        config = BackoffConfig(base=4, max_delay=100, jitter_percent=0)
        calculator = BackoffCalculator(config)

        # 4^5 = 1024, but should be capped at 100
        delay = calculator.calculate(5, with_jitter=False)

        assert delay == 100, f"Expected 100 (capped), got {delay}"

    def test_jitter_adds_variance(self):
        """
        Purpose:
            Verify jitter produces varying delays for same attempt.
        Expected:
            Multiple calculations produce different values within range.
        """
        config = BackoffConfig(base=4, max_delay=180, jitter_percent=25)
        calculator = BackoffCalculator(config)

        # Calculate 100 times for attempt 2 (base delay = 16)
        delays = [calculator.calculate(2, with_jitter=True) for _ in range(100)]

        # Should have variance
        unique_delays = len(set(delays))
        min_delay = min(delays)
        max_delay = max(delays)

        # With 25% jitter on 16, range should be 12-20
        assert min_delay >= 12, f"Min delay {min_delay} below expected range"
        assert max_delay <= 20, f"Max delay {max_delay} above expected range"
        assert unique_delays > 1, "Jitter should produce varying delays"

    def test_jitter_prevents_thundering_herd(self):
        """
        Purpose:
            Verify jitter distributes retries to prevent thundering herd.
        Expected:
            Delays are spread across the jitter range.
        """
        config = BackoffConfig(base=4, max_delay=180, jitter_percent=25)
        calculator = BackoffCalculator(config)

        # Simulate 1000 clients retrying at same time
        delays = [calculator.calculate(1, with_jitter=True) for _ in range(1000)]

        # Check distribution across range (3-5 for base delay of 4)
        delay_set = set(delays)

        # Should have multiple unique values (at least 2 for small base delay)
        assert len(delay_set) >= 2, "Jitter should produce some variance"

    def test_calculate_backoff_convenience_function(self):
        """
        Purpose:
            Verify convenience function works correctly.
        """
        delay = calculate_backoff(attempt=2, base=4, max_delay=180, jitter_percent=0)

        assert delay == 16, f"Expected 16, got {delay}"

    def test_domain_specific_calculator(self):
        """
        Purpose:
            Verify domain-specific calculator loading.
        """
        # This tests the caching mechanism
        calc1 = get_calculator_for_domain("payment")
        calc2 = get_calculator_for_domain("payment")

        assert calc1 is calc2, "Should return cached calculator"


# =============================================================================
# B. Retry Handler Tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestRetryHandler:
    """
    Tests for retry handler with DLQ integration.

    Validates:
    - Successful retry execution
    - DLQ routing on max retries
    - Non-retryable error handling
    """

    def test_successful_execution_no_retry(self):
        """
        Purpose:
            Verify successful function executes once without retry.
        Expected:
            Success on first attempt, no DLQ entry.
        """
        config = RetryConfig(max_attempts=3, enable_dlq=False)
        handler = RetryHandler(config=config)

        def successful_func():
            return "success"

        result = handler.execute(successful_func)

        assert result.success is True
        assert result.action == RetryAction.SUCCESS
        assert result.attempt == 1
        assert result.value == "success"

    def test_retry_on_transient_error(self):
        """
        Purpose:
            Verify retry occurs on transient errors.
        Expected:
            Function retried until success.
        """
        config = RetryConfig(max_attempts=3, enable_dlq=False)
        handler = RetryHandler(config=config)

        call_count = 0

        def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("Transient error")
            return "eventual success"

        result = handler.execute(flaky_func)

        assert result.success is True
        assert result.attempt == 2
        assert result.value == "eventual success"
        assert call_count == 2

    def test_dlq_on_max_retries_exceeded(self):
        """
        Purpose:
            Verify DLQ entry created when max retries exceeded.
        Expected:
            FailedOperation record created with correct data.
        """
        user = UserFactory.with_points(10000)
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(order=order, status="in_progress")

        config = RetryConfig(max_attempts=2, enable_dlq=True, domain="payment")
        handler = RetryHandler(config=config)

        def always_fails():
            raise ConnectionError("Persistent error")

        result = handler.execute(
            always_fails,
            context={
                "order": order,
                "payment": payment,
                "user": user,
            },
        )

        assert result.success is False
        assert result.action == RetryAction.DLQ
        assert result.attempt == 2
        assert result.dlq_id is not None

        # Verify DLQ record
        dlq = FailedOperation.objects.get(pk=result.dlq_id)
        assert dlq.domain == "payment"
        assert "MAX_RETRIES" in dlq.failure_type
        assert dlq.order == order
        assert dlq.payment == payment
        assert dlq.user == user
        assert dlq.status == FailedOperation.Status.PENDING

    def test_non_retryable_exception_immediate_fail(self):
        """
        Purpose:
            Verify non-retryable exceptions don't trigger retry.
        Expected:
            Immediate failure without retry attempts.
        """

        class PermanentError(Exception):
            pass

        config = RetryConfig(
            max_attempts=3,
            enable_dlq=True,
            domain="payment",
            non_retryable_exceptions=(PermanentError,),
        )
        handler = RetryHandler(config=config)

        call_count = 0

        def permanent_failure():
            nonlocal call_count
            call_count += 1
            raise PermanentError("Cannot recover")

        result = handler.execute(permanent_failure)

        assert result.success is False
        assert result.attempt == 1
        assert call_count == 1  # Only called once, no retry

    def test_retry_context_in_dlq_metadata(self):
        """
        Purpose:
            Verify retry history is captured in DLQ metadata.
        Expected:
            DLQ entry contains retry_history with attempt details.
        """
        config = RetryConfig(max_attempts=2, enable_dlq=True, domain="payment")
        handler = RetryHandler(config=config)

        def always_fails():
            raise ValueError("Test error")

        result = handler.execute(always_fails)

        dlq = FailedOperation.objects.get(pk=result.dlq_id)

        assert "retry_history" in dlq.metadata
        assert len(dlq.metadata["retry_history"]) == 2
        assert dlq.metadata["max_attempts"] == 2


@pytest.mark.django_db(transaction=True)
class TestWithRetryDecorator:
    """Tests for @with_retry decorator."""

    def test_decorator_success(self):
        """
        Purpose:
            Verify decorator allows successful execution.
        """

        @with_retry(domain="payment", max_attempts=3)
        def test_func():
            return "decorated success"

        result = test_func()
        assert result == "decorated success"

    def test_decorator_raises_on_max_retries(self):
        """
        Purpose:
            Verify decorator raises MaxRetriesExceededError on exhaustion.
        """

        @with_retry(domain="payment", max_attempts=2)
        def always_fails():
            raise ConnectionError("Error")

        with pytest.raises(MaxRetriesExceededError) as exc_info:
            always_fails()

        assert exc_info.value.retry_count == 2
        assert exc_info.value.max_retries == 2


# =============================================================================
# C. Idempotency Service Tests
# =============================================================================


@pytest.mark.skip(
    reason="IdempotencyService uses generic check_event API; Django-specific check_payment tests moved to Django adapter layer"
)
@pytest.mark.django_db(transaction=True)
class TestIdempotencyService:
    """
    Tests for idempotency checking service.

    Validates:
    - Payment duplicate detection
    - Webhook event deduplication
    - Cache and database consistency
    """

    @pytest.fixture(autouse=True)
    def setup_cache(self, settings):
        """Configure test cache settings."""
        settings.CACHES = TEST_CACHES

    def test_payment_idempotency_no_duplicate(self, category):
        """
        Purpose:
            Verify new payment is not detected as duplicate.
        Expected:
            is_duplicate=False for new payment.
        """
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(price=Decimal("10000"), stock=10, category=category)
        order = OrderFactory(user=user, status="confirmed")

        service = IdempotencyService()
        result = service.check_payment(order_id=order.id, amount=10000)

        assert result.is_duplicate is False
        assert result.should_proceed is True

    def test_payment_idempotency_detects_duplicate(self, category):
        """
        Purpose:
            Verify existing payment is detected as duplicate.
        Expected:
            is_duplicate=True with existing payment returned.
        """
        user = UserFactory(is_email_verified=True)
        order = OrderFactory(user=user, status="confirmed")
        existing_payment = PaymentFactory(
            order=order,
            amount=Decimal("10000"),
            status="done",
        )

        service = IdempotencyService()
        result = service.check_payment(order_id=order.id, amount=10000)

        assert result.is_duplicate is True
        assert result.existing_record == existing_payment
        assert result.should_proceed is False

    def test_payment_confirm_idempotency(self, category):
        """
        Purpose:
            Verify payment confirmation duplicate detection.
        Expected:
            Already confirmed payments are detected.
        """
        user = UserFactory(is_email_verified=True)
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(
            order=order,
            amount=Decimal("10000"),
            status="done",
            payment_key="test_key_123",
        )

        service = IdempotencyService()
        result = service.check_payment_confirm(
            payment_key="test_key_123",
            order_id=order.id,
            amount=10000,
        )

        assert result.is_duplicate is True
        assert result.existing_record == payment

    def test_idempotency_cache_hit(self, category):
        """
        Purpose:
            Verify cache is used for subsequent checks.
        Expected:
            Second check hits cache without database query.
        """
        user = UserFactory(is_email_verified=True)
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(
            order=order,
            amount=Decimal("10000"),
            status="done",
        )

        service = IdempotencyService()

        # First check populates cache
        result1 = service.check_payment(order_id=order.id, amount=10000)
        assert result1.is_duplicate is True

        # Second check should hit cache
        result2 = service.check_payment(order_id=order.id, amount=10000)
        assert result2.is_duplicate is True
        assert "cache" in result2.message.lower() or "database" in result2.message.lower()

    def test_idempotency_key_creation(self):
        """
        Purpose:
            Verify idempotency key creation for different domains.
        """
        # Payment key
        payment_key = IdempotencyKey.for_payment(order_id=123, amount=10000)
        assert "payment" in payment_key.cache_key
        assert "123" in payment_key.key

        # Webhook key
        webhook_key = IdempotencyKey.for_webhook(event_id="evt_12345")
        assert "webhook" in webhook_key.cache_key

        # Point operation key
        point_key = IdempotencyKey.for_point_operation(
            order_id=123,
            point_type="earn",
            amount=500,
        )
        assert "point" in point_key.cache_key


# =============================================================================
# D. Forensic Context Tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestForensicContext:
    """
    Tests for forensic context capture.

    Validates:
    - State snapshot completeness
    - Retry history tracking
    - Context builder fluent API
    """

    def test_state_snapshot_capture(self, category):
        """
        Purpose:
            Verify state snapshots capture all relevant data.
        Expected:
            order_status, payment_status, user_points captured.
        """
        user = UserFactory.with_points(5000)
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(order=order, status="in_progress")

        context = ForensicContext()
        context.capture_state_before(order=order, payment=payment, user=user)

        assert context.state_before is not None
        assert context.state_before.order_status == "confirmed"
        assert context.state_before.payment_status == "in_progress"
        assert context.state_before.user_points == 5000

    def test_retry_history_tracking(self):
        """
        Purpose:
            Verify retry attempts are tracked in history.
        Expected:
            Each attempt recorded with error details.
        """
        context = ForensicContext()

        context.add_retry_attempt(
            attempt=1,
            error_code="TIMEOUT",
            error_message="Request timed out",
            backoff_seconds=4,
        )
        context.add_retry_attempt(
            attempt=2,
            error_code="TIMEOUT",
            error_message="Request timed out again",
            backoff_seconds=16,
        )

        assert len(context.retry_history) == 2
        assert context.retry_history[0].attempt == 1
        assert context.retry_history[0].error_code == "TIMEOUT"
        assert context.retry_history[0].backoff_seconds == 4
        assert context.retry_history[1].attempt == 2

    def test_forensic_context_builder(self, category):
        """
        Purpose:
            Verify builder pattern creates complete context.
        """
        user = UserFactory.with_points(5000)
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(order=order, status="in_progress")

        context = (
            ForensicContextBuilder()
            .start_timing()
            .with_task(task_id="task-123", task_name="process_payment")
            .with_state_before(order=order, payment=payment, user=user)
            .with_extra(custom_field="custom_value")
            .end_timing()
            .build()
        )

        assert context.task_id == "task-123"
        assert context.task_name == "process_payment"
        assert context.state_before is not None
        assert context.latency_ms >= 0
        assert context.extra["custom_field"] == "custom_value"

    def test_snapshot_data_creation(self, category):
        """
        Purpose:
            Verify snapshot data for DLQ contains all needed info.
        """
        user = UserFactory.with_points(5000)
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(
            order=order,
            status="in_progress",
            payment_key="pay_key_123",
            amount=Decimal("10000"),
        )

        snapshot = create_snapshot_data(order=order, payment=payment, user=user)

        assert snapshot["order_id"] == order.id
        assert snapshot["payment_id"] == payment.id
        assert snapshot["user_id"] == user.id
        assert snapshot["payment_key"] == "pay_key_123"
        assert snapshot["user_points"] == 5000

    def test_metadata_to_dict_conversion(self):
        """
        Purpose:
            Verify ForensicContext converts to storable dict.
        """
        context = ForensicContext(
            request_timestamp="2025-01-01T10:00:00Z",
            client_ip="127.0.0.1",
            task_name="test_task",
        )
        context.add_retry_attempt(
            attempt=1,
            error_code="ERROR",
            error_message="Test",
            backoff_seconds=4,
        )

        metadata = context.to_metadata()

        assert metadata["request_timestamp"] == "2025-01-01T10:00:00Z"
        assert metadata["client_ip"] == "127.0.0.1"
        assert metadata["task_name"] == "test_task"
        assert len(metadata["retry_history"]) == 1
        assert metadata["retry_history"][0]["attempt"] == 1


# =============================================================================
# E. Integration Tests - Full DLQ Flow
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestDLQIntegrationFlow:
    """
    End-to-end tests for the self-healing DLQ flow.

    Validates:
    - Failure → Retry → DLQ complete flow
    - Forensic context in DLQ entries
    - DLQ state transitions
    """

    def test_full_failure_to_dlq_flow(self, category):
        """
        Purpose:
            Verify complete flow from failure to DLQ entry.
        Expected:
            All context captured, DLQ entry created correctly.
        """
        user = UserFactory.with_points(10000)
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(order=order, status="in_progress")

        # Create forensic context
        forensic = (
            ForensicContextBuilder()
            .start_timing()
            .with_task(task_id="celery-task-123", task_name="confirm_payment")
            .with_state_before(order=order, payment=payment, user=user)
            .build()
        )

        # Simulate retry exhaustion
        config = RetryConfig(max_attempts=2, enable_dlq=True, domain="payment")
        handler = RetryHandler(config=config)

        def simulate_pg_timeout():
            raise TimeoutError("PG API timeout after 30s")

        result = handler.execute(
            simulate_pg_timeout,
            context={
                "order": order,
                "payment": payment,
                "user": user,
                "snapshot_data": create_snapshot_data(order, payment, user),
            },
        )

        # Verify result
        assert result.success is False
        assert result.action == RetryAction.DLQ
        assert result.dlq_id is not None

        # Verify DLQ entry
        dlq = FailedOperation.objects.get(pk=result.dlq_id)
        assert dlq.domain == "payment"
        assert dlq.order == order
        assert dlq.payment == payment
        assert dlq.status == FailedOperation.Status.PENDING
        assert dlq.is_replayable is True

    def test_dlq_replay_success(self, category):
        """
        Purpose:
            Verify DLQ entry can be queued for replay.
        Expected:
            Status changes to REPLAYED, retry_count increments.
        """
        user = UserFactory.with_points(10000)
        order = OrderFactory(user=user, status="confirmed")

        dlq = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(order.id),
            user=user,
            error_code="TIMEOUT",
            error_message="Connection timed out",
        )

        assert dlq.retry_count == 0
        assert dlq.is_replayable is True

        # Queue for replay
        dlq.queue_for_replay()

        assert dlq.status == FailedOperation.Status.REPLAYED
        assert dlq.retry_count == 1

    def test_dlq_max_replay_attempts(self, category):
        """
        Purpose:
            Verify replay is blocked after max attempts.
        Expected:
            ValueError raised on third replay attempt.
        """
        user = UserFactory.with_points(10000)
        order = OrderFactory(user=user, status="confirmed")

        dlq = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(order.id),
            user=user,
            error_code="TIMEOUT",
            error_message="Connection timed out",
        )

        # First replay
        dlq.queue_for_replay()
        dlq.revert_to_pending()

        # Second replay
        dlq.queue_for_replay()
        dlq.revert_to_pending()

        # Third replay should fail
        assert dlq.retry_count == 2
        assert dlq.is_replayable is False

        with pytest.raises(ValueError, match="Maximum replay attempts"):
            dlq.queue_for_replay()

    def test_dlq_resolution_tracking(self, category):
        """
        Purpose:
            Verify resolution metadata is captured correctly.
        """
        user = UserFactory.with_points(10000)
        admin_user = UserFactory(is_staff=True)
        order = OrderFactory(user=user, status="confirmed")

        dlq = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="AMOUNT_MISMATCH",
            entity_type="order",
            entity_id=str(order.id),
            user=user,
            error_code="MISMATCH",
            error_message="Amount mismatch detected",
        )

        # Resolve the entry
        dlq.mark_as_resolved(
            resolved_by=admin_user,
            note="Manually verified with PG dashboard",
            resolution_type=FailedOperation.ResolutionType.MANUAL_FIX,
        )

        assert dlq.status == FailedOperation.Status.RESOLVED
        assert dlq.resolved_by == admin_user
        assert dlq.resolution_note == "Manually verified with PG dashboard"
        assert dlq.resolved_at is not None
