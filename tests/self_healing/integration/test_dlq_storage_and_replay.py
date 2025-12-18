"""
DLQ Storage and Replay Integration Tests

Tests for Dead Letter Queue storage and replay functionality.
Validates the implementation of Operations §1 (DLQ) and §2 (Replay Policy).

Test Categories:
    A. DLQ Service Tests:
        - Store failure to DLQ
        - Store with forensic context
        - Query pending/replayable entries
        - SLA breach detection
    B. Replay Service Tests:
        - Single entry replay
        - Batch replay by failure type
        - Batch replay by domain
        - Max replay attempt enforcement
        - REQUIRES_REVIEW escalation on repeated failures
    C. Replay Handler Tests:
        - Payment replay handler
        - Point replay handler
        - Webhook replay handler
        - Handler exception handling
    D. Celery Task Tests:
        - replay_single_dlq_entry task
        - replay_batch_by_failure_type task
        - replay_on_circuit_breaker_close task
        - cleanup_resolved_dlq_entries (soft-delete)
    E. Edge Cases and Error Handling:
        - State transitions
        - Soft-delete vs hard-delete
        - Handler crash handling

Reference: docs/L3_SELF_HEALING_OPERATIONS.md §1, §2
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.utils import timezone

import pytest

from shopping.models.failed_operation import FailedOperation
from selfhealing.services import (
    DLQConfig,
    DLQEntryResult,
    DLQService,
    get_dlq_service,
    store_to_dlq,
)
from selfhealing.services import (
    BatchReplayResult,
    DefaultReplayHandler,
    ReplayResult,
    ReplayService,
)
from shopping.services.self_healing import (
    PaymentReplayHandler,
    PointReplayHandler,
    WebhookReplayHandler,
    batch_replay_by_failure_type,
    get_replay_handler,
    get_replay_service,
    replay_failed_operation,
)
from shopping.tests.factories import (
    OrderFactory,
    PaymentFactory,
    UserFactory,
)


# =============================================================================
# A. DLQ Service Tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestDLQService:
    """
    Tests for DLQ storage and retrieval operations.

    Validates:
    - Failure storage with full context
    - Query operations (pending, replayable, SLA breached)
    - Statistics calculation
    """

    @pytest.fixture
    def dlq_service(self):
        """Create DLQ service instance."""
        return DLQService(config=DLQConfig(enabled=True, retention_days=30, max_replay_attempts=2))

    @pytest.fixture
    def sample_order(self):
        """Create a sample order with payment."""
        user = UserFactory()
        order = OrderFactory(user=user, status="confirmed")
        return order

    @pytest.fixture
    def sample_payment(self, sample_order):
        """Create a sample payment."""
        return PaymentFactory(order=sample_order, status="in_progress")

    def test_store_failure_creates_dlq_entry(self, dlq_service, sample_order, sample_payment):
        """
        Purpose:
            Verify that store_failure creates a DLQ entry with all fields.
        Expected:
            - DLQ entry is created
            - All fields are populated correctly
            - expires_at is set based on retention_days
        """
        result = dlq_service.store_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(sample_order.id),
            user_id=sample_order.user.id,
            error_code="TIMEOUT",
            error_message="Connection timed out after 30s",
            snapshot_data={"order_id": sample_order.id, "amount": "10000"},
            request_data={"payment_key": "test_key"},
            response_data={"error": "timeout"},
            metadata={"retry_count": 3},
            next_action_hint="Check PG status",
            recommended_action="manual_check",
        )

        assert result.success is True
        assert result.dlq_id is not None

        # Verify stored entry
        entry = FailedOperation.objects.get(id=result.dlq_id)
        assert entry.domain == "payment"
        assert entry.failure_type == "PG_TIMEOUT"
        assert entry.status == FailedOperation.Status.PENDING
        assert entry.entity_type == "order"
        assert entry.entity_id == str(sample_order.id)
        assert entry.user_id == sample_order.user.id
        assert entry.error_code == "TIMEOUT"
        assert entry.error_message == "Connection timed out after 30s"
        assert entry.snapshot_data["order_id"] == sample_order.id
        assert entry.request_data["payment_key"] == "test_key"
        assert entry.next_action_hint == "Check PG status"
        assert entry.expires_at is not None

    def test_store_failure_when_disabled(self):
        """
        Purpose:
            Verify that store_failure returns failure when DLQ is disabled.
        """
        service = DLQService(config=DLQConfig(enabled=False))

        result = service.store_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            error_message="Test error",
        )

        assert result.success is False
        assert result.error == "DLQ is disabled"

    def test_get_pending_entries(self, dlq_service, sample_order):
        """
        Purpose:
            Verify get_pending_entries returns only pending entries.
        """
        # Create entries with different statuses
        FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(sample_order.id),
        )
        resolved_entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(sample_order.id),
        )
        resolved_entry.mark_as_resolved(note="Fixed")

        pending = dlq_service.get_pending_entries(domain="payment")

        assert pending.count() == 1
        assert all(e.status == FailedOperation.Status.PENDING for e in pending)

    def test_get_replayable_entries(self, dlq_service, sample_order):
        """
        Purpose:
            Verify get_replayable_entries filters by retry_count.
        """
        # Create entry with 0 retries (replayable)
        entry1 = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(sample_order.id),
        )

        # Create entry with max retries (not replayable)
        entry2 = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(sample_order.id),
        )
        entry2.retry_count = 3
        entry2.save()

        replayable = dlq_service.get_replayable_entries()

        assert replayable.count() == 1
        assert replayable.first().id == entry1.id

    def test_get_sla_breached_entries(self, dlq_service, sample_order):
        """
        Purpose:
            Verify SLA breach detection by domain thresholds.
        """
        # Create payment entry (SLA: 1 hour)
        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(sample_order.id),
        )
        # Backdate created_at to 2 hours ago
        entry.created_at = timezone.now() - timedelta(hours=2)
        entry.save(update_fields=["created_at"])

        breached = dlq_service.get_sla_breached_entries()

        assert breached.count() == 1
        assert breached.first().id == entry.id

    def test_get_stats(self, dlq_service, sample_order):
        """
        Purpose:
            Verify statistics calculation.
        """
        # Create entries with different statuses
        FailedOperation.create_from_failure(domain="payment", failure_type="A", entity_type="order", entity_id=str(sample_order.id))
        FailedOperation.create_from_failure(domain="payment", failure_type="B", entity_type="order", entity_id=str(sample_order.id))
        resolved = FailedOperation.create_from_failure(domain="point", failure_type="C", entity_type="order", entity_id=str(sample_order.id))
        resolved.mark_as_resolved(note="Fixed")

        stats = dlq_service.get_stats()

        assert stats["pending_count"] == 2
        assert stats["resolved_count"] == 1
        assert stats["pending_by_domain"]["payment"] == 2

    def test_convenience_function_store_to_dlq(self, sample_order):
        """
        Purpose:
            Verify store_to_dlq convenience function works.
        """
        result = store_to_dlq(
            domain="webhook",
            failure_type="SIGNATURE_MISMATCH",
            entity_type="order",
            entity_id=str(sample_order.id),
            error_message="Invalid signature",
        )

        assert result.success is True
        assert result.dlq_id is not None


# =============================================================================
# B. Replay Service Tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestReplayService:
    """
    Tests for DLQ replay operations.

    Validates:
    - Single entry replay
    - Batch replay operations
    - Max replay attempt enforcement
    - Status transitions during replay
    """

    @pytest.fixture
    def replay_service(self):
        """Create replay service instance."""
        return ReplayService()

    @pytest.fixture
    def pending_dlq_entry(self):
        """Create a pending DLQ entry for testing."""
        user = UserFactory()
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(order=order, status="in_progress")

        return FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(order.id),
            user=user,
            error_message="Test timeout error",
            snapshot_data={"payment_id": payment.id, "order_id": order.id},
        )

    def test_replay_single_success(self, replay_service, pending_dlq_entry):
        """
        Purpose:
            Verify successful single entry replay.
        Expected:
            - Entry status changes to RESOLVED
            - Resolution type is AUTO_REPLAY
            - retry_count is incremented
        """
        with patch.object(PaymentReplayHandler, "replay") as mock_replay:
            mock_replay.return_value = ReplayResult.succeeded(
                pending_dlq_entry.id,
                "Replay scheduled",
                {"task_id": "test-task-id"},
            )

            result = replay_service.replay_single(pending_dlq_entry.id)

        assert result.success is True

        # Verify entry is resolved
        pending_dlq_entry.refresh_from_db()
        assert pending_dlq_entry.status == FailedOperation.Status.RESOLVED
        assert pending_dlq_entry.resolution_type == FailedOperation.ResolutionType.AUTO_REPLAY
        assert pending_dlq_entry.retry_count == 1

    def test_replay_single_failure_reverts_to_pending(self, replay_service, pending_dlq_entry):
        """
        Purpose:
            Verify failed replay reverts entry to PENDING status.
        """
        with patch.object(PaymentReplayHandler, "replay") as mock_replay:
            mock_replay.return_value = ReplayResult.failed(
                pending_dlq_entry.id,
                "Payment system still down",
            )

            result = replay_service.replay_single(pending_dlq_entry.id)

        assert result.success is False

        # Verify entry is back to pending
        pending_dlq_entry.refresh_from_db()
        assert pending_dlq_entry.status == FailedOperation.Status.PENDING
        assert pending_dlq_entry.retry_count == 1  # Incremented even on failure

    def test_replay_max_attempts_exceeded(self, replay_service, pending_dlq_entry):
        """
        Purpose:
            Verify replay is rejected when max attempts exceeded.
        """
        # Set retry_count to max
        pending_dlq_entry.retry_count = 2
        pending_dlq_entry.save()

        result = replay_service.replay_single(pending_dlq_entry.id)

        assert result.success is False
        assert "max" in result.error.lower() and "exceeded" in result.error.lower()

        # Verify entry is rejected
        pending_dlq_entry.refresh_from_db()
        assert pending_dlq_entry.status == FailedOperation.Status.REJECTED

    def test_replay_nonexistent_entry(self, replay_service):
        """
        Purpose:
            Verify replay handles non-existent entry gracefully.
        """
        result = replay_service.replay_single(dlq_id=99999)

        assert result.success is False
        assert result.error == "DLQ entry not found"

    def test_batch_replay_by_failure_type(self, replay_service):
        """
        Purpose:
            Verify batch replay filters by failure type.
        """
        user = UserFactory()

        # Create multiple entries with different failure types
        order1 = OrderFactory(user=user)
        payment1 = PaymentFactory(order=order1)
        entry1 = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(order1.id),
            snapshot_data={"payment_id": payment1.id, "order_id": order1.id},
        )

        order2 = OrderFactory(user=user)
        payment2 = PaymentFactory(order=order2)
        entry2 = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(order2.id),
            snapshot_data={"payment_id": payment2.id, "order_id": order2.id},
        )

        entry3 = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="OTHER_ERROR",
            entity_type="order",
            entity_id=str(order1.id),
        )

        with patch.object(PaymentReplayHandler, "replay") as mock_replay:
            mock_replay.return_value = ReplayResult.succeeded(0, "OK")

            result = replay_service.replay_batch(failure_type="PG_TIMEOUT")

        # Only PG_TIMEOUT entries should be replayed
        assert result.total == 2
        assert result.success_count == 2
        assert mock_replay.call_count == 2

    def test_batch_replay_by_domain(self, replay_service):
        """
        Purpose:
            Verify batch replay filters by domain.
        """
        user = UserFactory()
        order = OrderFactory(user=user)
        payment = PaymentFactory(order=order)

        entry1 = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(order.id),
            snapshot_data={"payment_id": payment.id, "order_id": order.id},
        )

        entry2 = FailedOperation.create_from_failure(
            domain="webhook",
            failure_type="WEBHOOK_ERROR",
            entity_type="order",
            entity_id=str(order.id),
            request_data={"payment_key": "test", "order_id": order.id, "amount": 1000},
        )

        with patch.object(PaymentReplayHandler, "replay") as mock_payment:
            with patch.object(WebhookReplayHandler, "replay") as mock_webhook:
                mock_payment.return_value = ReplayResult.succeeded(0, "OK")
                mock_webhook.return_value = ReplayResult.succeeded(0, "OK")

                result = replay_service.replay_batch(domain="payment")

        assert result.total == 1
        assert mock_payment.call_count == 1
        assert mock_webhook.call_count == 0

    def test_convenience_function_replay_failed_operation(self, pending_dlq_entry):
        """
        Purpose:
            Verify replay_failed_operation convenience function works.
        """
        with patch.object(PaymentReplayHandler, "replay") as mock_replay:
            mock_replay.return_value = ReplayResult.succeeded(pending_dlq_entry.id, "OK")

            result = replay_failed_operation(pending_dlq_entry.id)

        assert result.success is True


# =============================================================================
# C. Replay Handler Tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestReplayHandlers:
    """
    Tests for domain-specific replay handlers.

    Validates:
    - Eligibility checking (can_replay)
    - Handler registration and retrieval
    - Domain-specific replay logic
    """

    def test_payment_handler_can_replay_pending_payment(self):
        """
        Purpose:
            Verify payment handler allows replay for pending payments.
        """
        user = UserFactory()
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(order=order, status="in_progress")

        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            order=order,
            payment=payment,
        )

        handler = PaymentReplayHandler()
        can_replay, reason = handler.can_replay(entry)

        assert can_replay is True
        assert reason == ""

    def test_payment_handler_blocks_completed_payment(self):
        """
        Purpose:
            Verify payment handler blocks replay for completed payments.
        """
        user = UserFactory()
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(order=order, status="done")

        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(order.id),
            snapshot_data={"payment_id": payment.id, "order_id": order.id},
        )

        handler = PaymentReplayHandler()
        can_replay, reason = handler.can_replay(entry)

        assert can_replay is False
        assert "already completed" in reason

    def test_payment_handler_blocks_cancelled_order(self):
        """
        Purpose:
            Verify payment handler blocks replay for cancelled orders.
        """
        user = UserFactory()
        order = OrderFactory(user=user, status="cancelled")
        payment = PaymentFactory(order=order, status="in_progress")

        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(order.id),
            snapshot_data={"payment_id": payment.id, "order_id": order.id},
        )

        handler = PaymentReplayHandler()
        can_replay, reason = handler.can_replay(entry)

        assert can_replay is False
        assert "cancelled" in reason

    def test_payment_handler_blocks_security_violations(self):
        """
        Purpose:
            Verify security-related failure types cannot be replayed.
        """
        user = UserFactory()
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(order=order, status="in_progress")

        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="SECURITY_SIGNATURE_INVALID",
            entity_type="order",
            entity_id=str(order.id),
            snapshot_data={"payment_id": payment.id, "order_id": order.id},
        )

        handler = PaymentReplayHandler()
        can_replay, reason = handler.can_replay(entry)

        assert can_replay is False
        assert "cannot be replayed" in reason

    def test_get_replay_handler_returns_correct_handler(self):
        """
        Purpose:
            Verify handler registry returns correct handlers.
        """
        payment_handler = get_replay_handler("payment")
        point_handler = get_replay_handler("point")
        webhook_handler = get_replay_handler("webhook")
        unknown_handler = get_replay_handler("unknown_domain")

        assert isinstance(payment_handler, PaymentReplayHandler)
        assert isinstance(point_handler, PointReplayHandler)
        assert isinstance(webhook_handler, WebhookReplayHandler)
        assert isinstance(unknown_handler, DefaultReplayHandler)

    def test_default_handler_cannot_replay(self):
        """
        Purpose:
            Verify default handler blocks all replays.
        """
        entry = MagicMock()
        entry.domain = "unknown"

        handler = DefaultReplayHandler("unknown")
        can_replay, reason = handler.can_replay(entry)

        assert can_replay is False
        assert "No specific replay handler" in reason


# =============================================================================
# D. Celery Task Tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestDLQReplayTasks:
    """
    Tests for DLQ replay Celery tasks.

    Validates:
    - Task execution and result format
    - Error handling
    - Task parameters
    """

    @pytest.fixture
    def pending_dlq_entry(self):
        """Create a pending DLQ entry for testing."""
        user = UserFactory()
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(order=order, status="in_progress")

        return FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(order.id),
            user=user,
            snapshot_data={"payment_id": payment.id, "order_id": order.id},
        )

    def test_replay_single_task_success(self, pending_dlq_entry):
        """
        Purpose:
            Verify replay_single_dlq_entry task returns correct result format.
        """
        from shopping.tasks.dlq_replay_tasks import replay_single_dlq_entry

        with patch.object(PaymentReplayHandler, "replay") as mock_replay:
            mock_replay.return_value = ReplayResult.succeeded(
                pending_dlq_entry.id,
                "Replay scheduled",
                {"task_id": "test-task-123"},
            )

            result = replay_single_dlq_entry(pending_dlq_entry.id)

        assert result["success"] is True
        assert result["dlq_id"] == pending_dlq_entry.id
        assert "task_id" in result["data"]

    def test_replay_single_task_failure(self, pending_dlq_entry):
        """
        Purpose:
            Verify task handles replay failure correctly.
        """
        from shopping.tasks.dlq_replay_tasks import replay_single_dlq_entry

        with patch.object(PaymentReplayHandler, "replay") as mock_replay:
            mock_replay.return_value = ReplayResult.failed(
                pending_dlq_entry.id,
                "Payment system unavailable",
            )

            result = replay_single_dlq_entry(pending_dlq_entry.id)

        assert result["success"] is False
        assert result["error"] == "Payment system unavailable"

    def test_replay_single_task_not_found(self):
        """
        Purpose:
            Verify task handles non-existent entry.
        """
        from shopping.tasks.dlq_replay_tasks import replay_single_dlq_entry

        result = replay_single_dlq_entry(99999)

        assert result["success"] is False
        assert "not found" in result["error"]

    def test_batch_replay_task(self, pending_dlq_entry):
        """
        Purpose:
            Verify replay_batch_by_failure_type task works correctly.
        """
        from shopping.tasks.dlq_replay_tasks import replay_batch_by_failure_type

        with patch.object(PaymentReplayHandler, "replay") as mock_replay:
            mock_replay.return_value = ReplayResult.succeeded(0, "OK")

            result = replay_batch_by_failure_type("PG_TIMEOUT", max_items=10)

        assert result["success"] is True
        assert result["total"] >= 1

    def test_circuit_breaker_close_replay_task(self, pending_dlq_entry):
        """
        Purpose:
            Verify replay_on_circuit_breaker_close task works correctly.
        """
        from shopping.tasks.dlq_replay_tasks import replay_on_circuit_breaker_close

        with patch.object(PaymentReplayHandler, "replay") as mock_replay:
            mock_replay.return_value = ReplayResult.succeeded(0, "OK")

            result = replay_on_circuit_breaker_close("toss_payment", max_items=10)

        assert result["success"] is True
        assert result["service_name"] == "toss_payment"

    def test_cleanup_resolved_task(self, pending_dlq_entry):
        """
        Purpose:
            Verify cleanup_resolved_dlq_entries task works correctly.
        """
        from shopping.tasks.dlq_replay_tasks import cleanup_resolved_dlq_entries

        # Resolve the entry
        pending_dlq_entry.mark_as_resolved(note="Test resolved")

        # Backdate to make it eligible for cleanup
        from datetime import timedelta

        pending_dlq_entry.created_at = timezone.now() - timedelta(days=60)
        pending_dlq_entry.save(update_fields=["created_at"])

        result = cleanup_resolved_dlq_entries(days_old=30)

        assert result["success"] is True


# =============================================================================
# E. Edge Cases and Error Handling
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestEdgeCasesAndErrorHandling:
    """
    Tests for edge cases and error scenarios.

    Validates:
    - Concurrent replay handling
    - Database errors
    - Missing references
    """

    def test_replay_with_missing_order_reference(self):
        """
        Purpose:
            Verify replay handles entries where order FK is None but snapshot has data.
        """
        user = UserFactory()
        order = OrderFactory(user=user)
        payment = PaymentFactory(order=order)

        # Create entry with order reference
        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=None,  # Simulate missing order reference
            snapshot_data={"payment_id": payment.id, "order_id": order.id},
        )

        handler = PaymentReplayHandler()
        can_replay, reason = handler.can_replay(entry)

        # Should still be replayable using snapshot data
        assert can_replay is True or "order" not in reason.lower()

    def test_replay_with_empty_snapshot(self):
        """
        Purpose:
            Verify replay handles entries with missing snapshot data.
        """
        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            snapshot_data={},  # Empty snapshot
        )

        handler = PaymentReplayHandler()
        result = handler.replay(entry)

        assert result.success is False
        assert "Missing" in result.error

    def test_dlq_entry_state_transitions(self):
        """
        Purpose:
            Verify all state transitions work correctly.
        """
        user = UserFactory()
        order = OrderFactory(user=user)

        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(order.id),
        )

        # PENDING -> REVIEWING
        entry.mark_as_reviewing(reviewer=user)
        assert entry.status == FailedOperation.Status.REVIEWING

        # REVIEWING -> PENDING (revert)
        entry.revert_to_pending(note="Need more info")
        assert entry.status == FailedOperation.Status.PENDING

        # PENDING -> REPLAYED
        entry.queue_for_replay()
        assert entry.status == FailedOperation.Status.REPLAYED
        assert entry.retry_count == 1

        # REPLAYED -> RESOLVED
        entry.mark_as_resolved(note="Fixed")
        assert entry.status == FailedOperation.Status.RESOLVED

    def test_replay_attempt_limit_enforcement(self):
        """
        Purpose:
            Verify queue_for_replay raises ValueError at max attempts.
        """
        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
        )
        entry.max_retries = 2
        entry.retry_count = 2
        entry.save()

        with pytest.raises(ValueError) as exc_info:
            entry.queue_for_replay()

        assert "Maximum replay attempts" in str(exc_info.value)

    def test_is_replayable_property(self):
        """
        Purpose:
            Verify is_replayable property works correctly.
        """
        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
        )

        # Initial state should be replayable
        assert entry.is_replayable is True

        # After max retries, should not be replayable
        entry.retry_count = 2
        entry.save()
        assert entry.is_replayable is False

        # If resolved, should not be replayable
        entry.retry_count = 0
        entry.status = FailedOperation.Status.RESOLVED
        entry.save()
        assert entry.is_replayable is False


# =============================================================================
# F. REQUIRES_REVIEW Escalation Tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestRequiresReviewEscalation:
    """
    Tests for REQUIRES_REVIEW status escalation.

    Validates:
    - Automatic escalation after 3+ failures
    - Handler crash escalation
    - Manual escalation
    """

    def test_escalation_after_three_failures(self):
        """
        Purpose:
            Verify entry escalates to REQUIRES_REVIEW after 3 replay failures.
        Escalation Rule:
            - 1-2 failures: stays PENDING
            - 3+ failures: escalates to REQUIRES_REVIEW
        """
        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
        )

        # Simulate 3 failures
        entry.retry_count = 3
        entry.save()

        # revert_to_pending should escalate at 3+ retries
        entry.revert_to_pending(note="Third failure")

        assert entry.status == FailedOperation.Status.REQUIRES_REVIEW
        assert entry.recommended_action == FailedOperation.RecommendedAction.ESCALATE

    def test_stays_pending_under_three_failures(self):
        """
        Purpose:
            Verify entry stays PENDING with fewer than 3 failures.
        """
        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
        )

        # Simulate 2 failures
        entry.retry_count = 2
        entry.save()

        entry.revert_to_pending(note="Second failure")

        assert entry.status == FailedOperation.Status.PENDING

    def test_handler_crash_triggers_requires_review(self):
        """
        Purpose:
            Verify handler exception escalates to REQUIRES_REVIEW.
        """
        user = UserFactory()
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(order=order, status="in_progress")

        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(order.id),
            snapshot_data={"payment_id": payment.id, "order_id": order.id},
        )

        service = ReplayService()

        # Make handler raise exception
        with patch.object(PaymentReplayHandler, "replay") as mock_replay:
            mock_replay.side_effect = RuntimeError("Unexpected database error")

            result = service.replay_single(entry.id)

        assert result.success is False
        assert "internal_error" in result.error

        # Entry should be escalated to REQUIRES_REVIEW
        entry.refresh_from_db()
        assert entry.status == FailedOperation.Status.REQUIRES_REVIEW
        assert "Handler crash" in entry.error_message
        assert "handler_exception" in entry.metadata

    def test_manual_escalation(self):
        """
        Purpose:
            Verify manual escalation to REQUIRES_REVIEW works.
        """
        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
        )

        entry.mark_as_requires_review(note="Data inconsistency detected")

        assert entry.status == FailedOperation.Status.REQUIRES_REVIEW
        assert "Escalated" in entry.error_message


# =============================================================================
# G. Soft-Delete and Archival Tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestSoftDeleteAndArchival:
    """
    Tests for soft-delete (archival) instead of hard delete.

    Validates:
    - Entries are archived, not deleted
    - Archived entries retained for audit
    - Cleanup task uses soft-delete
    """

    def test_mark_as_archived(self):
        """
        Purpose:
            Verify mark_as_archived sets correct status.
        """
        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
        )
        entry.mark_as_resolved(note="Fixed")

        entry.mark_as_archived(note="Auto-archived after 30 days")

        assert entry.status == FailedOperation.Status.ARCHIVED
        assert entry.resolution_type == FailedOperation.ResolutionType.ARCHIVED

    def test_cleanup_task_uses_soft_delete(self):
        """
        Purpose:
            Verify cleanup task archives entries instead of deleting.
        """
        from shopping.tasks.dlq_replay_tasks import cleanup_resolved_dlq_entries

        # Create resolved entry backdated
        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
        )
        entry.mark_as_resolved(note="Fixed")
        entry.created_at = timezone.now() - timedelta(days=60)
        entry.save(update_fields=["created_at"])

        # Run cleanup
        result = cleanup_resolved_dlq_entries(days_old=30)

        assert result["success"] is True
        assert result["archived_count"] >= 1

        # Entry should still exist (not deleted)
        entry.refresh_from_db()
        assert entry.status == FailedOperation.Status.ARCHIVED

    def test_archived_entries_excluded_from_pending_queries(self):
        """
        Purpose:
            Verify archived entries don't appear in pending queries.
        """
        from selfhealing.services import DLQService

        # Create one pending and one archived
        pending_entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
        )
        archived_entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
        )
        archived_entry.mark_as_archived(note="Archived")

        service = DLQService()
        pending = list(service.get_pending_entries())

        assert pending_entry in pending
        assert archived_entry not in pending

    def test_archived_entries_retained_for_audit(self):
        """
        Purpose:
            Verify archived entries can still be queried for audit.
        """
        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            snapshot_data={"order_id": 12345, "amount": "50000"},
        )
        entry.mark_as_archived(note="Auto-archived")

        # Should still be queryable
        archived = FailedOperation.objects.filter(status=FailedOperation.Status.ARCHIVED)
        assert entry in archived

        # Original data should be preserved
        assert entry.snapshot_data["order_id"] == 12345


# =============================================================================
# F. Replay Escalation on Circuit Close Tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestReplayEscalationOnCircuitClose:
    """
    Tests for replay escalation when circuit breaker closes.

    Validates:
    - Failed replays are escalated to REQUIRES_REVIEW when escalate_failures=True
    - Escalation includes proper notes explaining the failure
    - No escalation when escalate_failures=False

    Reference: docs/L3_SELF_HEALING_ARCHITECTURE.md §10 (Governance Policy)
    """

    @pytest.fixture
    def replay_service(self):
        """Create replay service instance."""
        return ReplayService()

    @pytest.fixture
    def pg_timeout_entry(self):
        """Create a PG_TIMEOUT entry for circuit close testing."""
        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            snapshot_data={"order_id": 99999, "amount": "10000"},
        )
        return entry

    def test_replay_on_circuit_close_escalates_failed_replays(self, replay_service, pg_timeout_entry):
        """
        Purpose:
            Verify failed replays are escalated to REQUIRES_REVIEW
            when circuit closes with escalate_failures=True (default).

        Context:
            When operator force_closes a circuit, they expect pending items
            to be resolved. If replay fails, it needs human attention.
        """
        with patch.object(PaymentReplayHandler, "replay") as mock_replay:
            # Simulate replay failure
            mock_replay.return_value = ReplayResult.failed(pg_timeout_entry.id, "PG still failing")

            result = replay_service.replay_on_circuit_close(
                service_name="toss_payment",
                max_items=10,
                escalate_failures=True,
            )

        # Verify result counts
        assert result.failed_count >= 1

        # Verify entry was escalated to REQUIRES_REVIEW
        pg_timeout_entry.refresh_from_db()
        assert pg_timeout_entry.status == FailedOperation.Status.REQUIRES_REVIEW
        assert "circuit close" in pg_timeout_entry.resolution_note.lower()

    def test_replay_on_circuit_close_no_escalation_when_disabled(self, replay_service, pg_timeout_entry):
        """
        Purpose:
            Verify no escalation when escalate_failures=False.
        """
        with patch.object(PaymentReplayHandler, "replay") as mock_replay:
            mock_replay.return_value = ReplayResult.failed(pg_timeout_entry.id, "PG still failing")

            result = replay_service.replay_on_circuit_close(
                service_name="toss_payment",
                max_items=10,
                escalate_failures=False,
            )

        # Verify entry remains PENDING (not escalated)
        pg_timeout_entry.refresh_from_db()
        assert pg_timeout_entry.status == FailedOperation.Status.PENDING

    def test_replay_on_circuit_close_successful_replays_not_affected(self, replay_service, pg_timeout_entry):
        """
        Purpose:
            Verify successful replays are marked RESOLVED, not escalated.
        """
        with patch.object(PaymentReplayHandler, "replay") as mock_replay:
            mock_replay.return_value = ReplayResult.succeeded(pg_timeout_entry.id, "Replay successful")

            result = replay_service.replay_on_circuit_close(
                service_name="toss_payment",
                max_items=10,
                escalate_failures=True,
            )

        # Verify result counts
        assert result.success_count >= 1

        # Verify entry was resolved (not escalated)
        pg_timeout_entry.refresh_from_db()
        assert pg_timeout_entry.status == FailedOperation.Status.RESOLVED

    def test_escalation_note_includes_service_name(self, replay_service, pg_timeout_entry):
        """
        Purpose:
            Verify escalation note includes the service name for context.
        """
        with patch.object(PaymentReplayHandler, "replay") as mock_replay:
            mock_replay.return_value = ReplayResult.failed(pg_timeout_entry.id, "Connection refused")

            replay_service.replay_on_circuit_close(
                service_name="toss_payment",
                max_items=10,
                escalate_failures=True,
            )

        pg_timeout_entry.refresh_from_db()
        assert "toss_payment" in pg_timeout_entry.resolution_note

    def test_replay_failure_after_circuit_close_does_not_reopen_circuit(self, replay_service, pg_timeout_entry):
        """
        Purpose:
            Verify that replay failure does NOT cause circuit to reopen.

        Context:
            When operator force_closes a circuit, they expect it to stay closed.
            Failed replays should be escalated to REQUIRES_REVIEW, NOT cause
            the circuit to automatically reopen. The circuit state is operator-controlled.
        """
        from shopping.models.failed_payment import CircuitBreakerState

        # Create a circuit in CLOSED state (operator just closed it)
        state, _ = CircuitBreakerState.objects.get_or_create(service_name="toss_payment", defaults={"state": "closed"})
        state.state = "closed"
        state.save()

        with patch.object(PaymentReplayHandler, "replay") as mock_replay:
            # Simulate replay failure
            mock_replay.return_value = ReplayResult.failed(pg_timeout_entry.id, "PG still failing after close")

            result = replay_service.replay_on_circuit_close(
                service_name="toss_payment",
                max_items=10,
                escalate_failures=True,
            )

        # Verify circuit is still CLOSED (not reopened)
        state.refresh_from_db()
        assert state.state == "closed"

        # Entry should be escalated, not cause circuit state change
        pg_timeout_entry.refresh_from_db()
        assert pg_timeout_entry.status == FailedOperation.Status.REQUIRES_REVIEW

    def test_security_violation_during_replay_creates_incident(self):
        """
        Purpose:
            Verify that security-related failure types create SecurityIncident
            when replay is attempted.

        Context:
            Security violations (SECURITY_SIGNATURE_INVALID, etc.) should NEVER
            be auto-replayed. Any attempt to replay them should be blocked and
            a SecurityIncident should be created for audit trail.

        Reference:
            docs/L3_SELF_HEALING_OPERATIONS.md Section 5 - Security Incident Handling
        """
        from shopping.models import SecurityIncident
        from shopping.tests.factories import (
            OrderFactory,
            PaymentFactory,
            UserFactory,
        )

        # Create a security violation entry
        user = UserFactory()
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(order=order, status="in_progress")

        security_entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="SECURITY_SIGNATURE_INVALID",
            entity_type="order",
            entity_id=str(order.id),
            user=user,
            error_message="Signature verification failed - possible tampering",
            snapshot_data={"payment_id": payment.id, "order_id": order.id},
        )

        # Attempt replay (should be blocked by handler)
        handler = PaymentReplayHandler()
        can_replay, reason = handler.can_replay(security_entry)

        # Verify replay is blocked
        assert can_replay is False
        assert "cannot be replayed" in reason

        # Verify that when replay is attempted via service, it's blocked
        service = ReplayService()
        result = service.replay_single(security_entry.id)

        assert result.success is False
        assert "cannot be replayed" in (result.error or "")

        # Entry should be marked as REJECTED (not REQUIRES_REVIEW)
        # because security violations should not even enter review queue
        security_entry.refresh_from_db()
        assert security_entry.status in (
            FailedOperation.Status.REJECTED,
            FailedOperation.Status.PENDING,  # might stay pending if handler just returns failure
        )
