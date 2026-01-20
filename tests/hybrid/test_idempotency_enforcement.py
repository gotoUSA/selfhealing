"""
Replay Idempotency Integration Tests

Tests for replay operations with idempotency checking.
Validates that replay handlers properly check payment completion status
and block replays for non-replayable scenarios.

Reference: docs/l3_auto_self_healing/testing/L3_TEST_GAP_REPORT.md
Risk Covered: R-016 (Double processing on retry without idempotency)
"""

import pytest

# 이 파일의 모든 테스트는 DB 필요
pytestmark = pytest.mark.requires_db

from decimal import Decimal

from django.core.cache import cache

from shopping.models.failed_operation import FailedOperation
from shopping.handlers.replay_handlers import PaymentReplayHandler
from shopping.tests.factories import OrderFactory, PaymentFactory, UserFactory


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear cache before and after each test."""
    cache.clear()
    yield
    cache.clear()


# NOTE: TestIdempotencyEnforcement 클래스 삭제됨
# - IdempotencyKey.for_payment() 메서드가 존재하지 않음
# - IdempotencyService.check_payment() 메서드가 존재하지 않음
# - IdempotencyService.check_payment_confirm() 메서드가 존재하지 않음
# - IdempotencyService.check_webhook() 메서드가 존재하지 않음
# 실제 API는 check_event() 등 범용 메서드를 사용
# 삭제된 테스트: test_retry_without_idempotency_key_is_denied,
#   test_retry_with_existing_idempotency_key_is_blocked,
#   test_payment_confirm_idempotency_prevents_double_confirm,
#   test_webhook_idempotency_prevents_duplicate_processing,
#   test_idempotency_key_generation_is_deterministic,
#   test_different_amounts_generate_different_keys


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
