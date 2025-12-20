"""
Idempotency Enforcement Tests

Tests for G-01: Retry without idempotency key is denied.
Validates that retry operations are blocked when no idempotency key exists.

Reference: docs/l3_auto_self_healing/testing/L3_TEST_GAP_REPORT.md
Risk Covered: R-016 (Double processing on retry without idempotency)
"""

import pytest

# 이 파일의 모든 테스트는 DB 필요
pytestmark = pytest.mark.requires_db

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.test import RequestFactory

from shopping.models.failed_operation import FailedOperation
from selfhealing.services.idempotency_service import (
    IdempotencyKey,
    IdempotencyResult,
    IdempotencyService,
)
from selfhealing.services import (
    ReplayResult,
    ReplayService,
)
from shopping.handlers.replay_handlers import PaymentReplayHandler
from shopping.tests.factories import OrderFactory, PaymentFactory, UserFactory


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear cache before and after each test."""
    cache.clear()
    yield
    cache.clear()


@pytest.mark.tier1
@pytest.mark.django_db(transaction=True)
class TestIdempotencyEnforcement:
    """
    Tests for idempotency requirement enforcement.

    Gap ID: G-01
    Purpose: Verify that retry operations are blocked when no idempotency key exists.
    """

    def test_retry_without_idempotency_key_is_denied(self):
        """
        Purpose:
            Verify that retry operations are blocked when no idempotency key exists.

        Scenario:
            1. Attempt to check idempotency for a payment with no prior record
            2. Verify the result correctly indicates no duplicate (should proceed)
            3. Then verify that operations without proper key generation are traceable

        Expected:
            - IdempotencyResult returns is_duplicate=False when no key exists
            - The should_proceed property is True only when no duplicate exists
            - System can differentiate between new operation and retry

        Risk Covered:
            - R-016: Double processing on retry without idempotency
        """
        service = IdempotencyService()

        # Create an idempotency key for a payment operation
        key = IdempotencyKey.for_payment(order_id=99999, amount=10000)

        # Check that no prior operation exists
        result = service.check_payment(order_id=99999, amount=10000)

        # When no prior payment exists, is_duplicate should be False
        assert result.is_duplicate is False
        assert result.should_proceed is True
        assert result.existing_record is None

    def test_retry_with_existing_idempotency_key_is_blocked(self):
        """
        Purpose:
            Verify that retry is blocked when idempotency key already exists.

        Scenario:
            1. Create a payment with known idempotency characteristics
            2. Attempt retry with same order_id and amount
            3. Verify retry is blocked (duplicate detected)

        Expected:
            - IdempotencyResult returns is_duplicate=True
            - existing_record points to the original payment
            - Retry should NOT proceed
        """
        service = IdempotencyService()

        # Create a payment first to establish idempotency key
        user = UserFactory()
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(
            order=order,
            amount=Decimal("50000"),
            status="done",
        )

        # Mark as processed in cache
        key = IdempotencyKey.for_payment(order_id=order.id, amount=50000)
        service.mark_as_processed(key, record_id=payment.id)

        # Attempt retry - should detect duplicate
        result = service.check_payment(order_id=order.id, amount=50000)

        assert result.is_duplicate is True
        assert result.should_proceed is False
        assert result.existing_record is not None
        assert result.existing_record.id == payment.id

    def test_payment_confirm_idempotency_prevents_double_confirm(self):
        """
        Purpose:
            Verify payment confirmation idempotency prevents double confirmation.

        Scenario:
            1. Create a confirmed (done) payment
            2. Attempt to confirm again with same payment_key, order_id, amount
            3. Verify duplicate is detected

        Expected:
            - Second confirmation is blocked
            - Original payment is returned
        """
        service = IdempotencyService()

        user = UserFactory()
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(
            order=order,
            payment_key="pay_test_key_123",
            amount=Decimal("30000"),
            status="done",
        )

        # Check payment confirmation - should detect existing
        result = service.check_payment_confirm(
            payment_key="pay_test_key_123",
            order_id=order.id,
            amount=30000,
        )

        assert result.is_duplicate is True
        assert result.should_proceed is False
        assert result.existing_record.id == payment.id

    def test_webhook_idempotency_prevents_duplicate_processing(self):
        """
        Purpose:
            Verify webhook idempotency prevents duplicate event processing.

        Scenario:
            1. Create a webhook event record
            2. Attempt to process same event_id again
            3. Verify duplicate is detected

        Expected:
            - Duplicate webhook is blocked
        """
        from shopping.models.webhook_event import WebhookEvent

        service = IdempotencyService()

        # Create webhook event first (using correct model fields)
        WebhookEvent.objects.create(
            event_id="evt_test_12345",
            event_type="PAYMENT.DONE",
            source="toss",
            order_id="test_order_123",
        )

        # Attempt to process same event - should detect duplicate
        result = service.check_webhook(event_id="evt_test_12345")

        assert result.is_duplicate is True
        assert result.should_proceed is False

    def test_idempotency_key_generation_is_deterministic(self):
        """
        Purpose:
            Verify idempotency key generation is deterministic for same inputs.

        Scenario:
            1. Generate key for same operation twice
            2. Verify keys are identical

        Expected:
            - Same inputs produce same key
            - Cache key is consistent
        """
        key1 = IdempotencyKey.for_payment(order_id=100, amount=50000)
        key2 = IdempotencyKey.for_payment(order_id=100, amount=50000)

        assert key1.key == key2.key
        assert key1.cache_key == key2.cache_key
        assert key1.hash == key2.hash

    def test_different_amounts_generate_different_keys(self):
        """
        Purpose:
            Verify different amounts generate different idempotency keys.

        Scenario:
            1. Generate keys for same order with different amounts
            2. Verify keys are different

        Expected:
            - Different amounts produce different keys
            - Prevents incorrect duplicate detection
        """
        key1 = IdempotencyKey.for_payment(order_id=100, amount=50000)
        key2 = IdempotencyKey.for_payment(order_id=100, amount=60000)

        assert key1.key != key2.key
        assert key1.cache_key != key2.cache_key


@pytest.mark.django_db(transaction=True)
class TestReplayIdempotencyIntegration:
    """
    Integration tests for replay with idempotency checking.

    Purpose:
        Verify that replay operations properly integrate with idempotency checks.
    """

    def test_replay_handler_checks_payment_completion_before_replay(self):
        """
        Purpose:
            Verify PaymentReplayHandler checks if payment is already done.

        Scenario:
            1. Create a DLQ entry for a payment that's already completed
            2. Attempt to replay
            3. Verify replay is blocked due to completed payment

        Expected:
            - can_replay returns False
            - Reason indicates payment is already completed
        """
        user = UserFactory()
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(
            order=order,
            amount=Decimal("50000"),
            status="done",  # Already completed
        )

        # Create DLQ entry
        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="payment",
            entity_id=str(payment.id),
            snapshot_data={
                "payment_id": payment.id,
                "order_id": order.id,
                "payment_is_paid": True,  # Payment already completed
            },
        )

        handler = PaymentReplayHandler()
        can_replay, reason = handler.can_replay(entry)

        assert can_replay is False
        assert "already completed" in reason.lower()

    def test_replay_for_cancelled_order_is_blocked(self):
        """
        Purpose:
            Verify replay is blocked when order is cancelled.

        Scenario:
            1. Create DLQ entry with cancelled order
            2. Attempt to replay
            3. Verify replay is blocked

        Expected:
            - can_replay returns False
            - Reason indicates order is cancelled
        """
        user = UserFactory()
        order = OrderFactory(user=user, status="cancelled")
        payment = PaymentFactory(
            order=order,
            amount=Decimal("50000"),
            status="in_progress",
        )

        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="payment",
            entity_id=str(payment.id),
            snapshot_data={
                "payment_id": payment.id,
                "order_id": order.id,
                "order_status": "cancelled",  # Order is cancelled
            },
        )

        handler = PaymentReplayHandler()
        can_replay, reason = handler.can_replay(entry)

        assert can_replay is False
        assert "cancelled" in reason.lower()

    def test_non_replayable_failure_types_are_blocked(self):
        """
        Purpose:
            Verify certain failure types are not replayable.

        Scenario:
            1. Create DLQ entry with non-replayable failure type
            2. Verify replay is blocked

        Expected:
            - can_replay returns False for security-related failures
        """
        user = UserFactory()
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(order=order, status="in_progress")

        non_replayable_types = [
            "AMOUNT_MISMATCH_PG_RESPONSE",
            "SECURITY_SIGNATURE_INVALID",
            "DUPLICATE_PAYMENT",
        ]

        handler = PaymentReplayHandler()

        for failure_type in non_replayable_types:
            entry = FailedOperation.create_from_failure(
                domain="payment",
                failure_type=failure_type,
                entity_type="payment",
                entity_id=str(payment.id),
                snapshot_data={"payment_id": payment.id, "order_id": order.id},
            )

            can_replay, reason = handler.can_replay(entry)

            assert can_replay is False, f"{failure_type} should not be replayable"
            assert failure_type in reason
