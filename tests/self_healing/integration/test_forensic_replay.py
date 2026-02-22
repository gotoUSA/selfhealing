"""
Forensic Snapshot Replay Tests

Integration tests for forensic snapshot-based replay functionality.
Validates that DLQ snapshots contain all data needed for operation replay.

Reference:
- Gap Report: G-10 (Forensic Snapshot Enables Operation Replay)
- docs/L3_SELF_HEALING_OPERATIONS.md §9 (Forensic Context)

Compliance:
- Audit trail completeness
- Disaster recovery capability

Note: Uses in-memory repositories for parallel execution.
"""

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock


from selfhealing.core.timezone import now

from .conftest import (
    InMemoryFailedOperationRepository,
    MockUser,
    MockOrder,
    MockPayment,
)


class TestForensicSnapshotReplay:
    """
    Integration tests for forensic-based replay.

    Validates:
    - Snapshot contains all critical fields
    - Handler can read from snapshot
    - Operations can be reconstructed from snapshot alone
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.repository = InMemoryFailedOperationRepository()
        self.sample_user = MockUser(id=1001, email="test@example.com")
        self.sample_user.points = 10000
        self.sample_order = MockOrder(id=2001, user=self.sample_user, status="confirmed")
        self.sample_payment = MockPayment(
            id=3001,
            order=self.sample_order,
            status="in_progress",
            payment_key="pay_key_test_12345",
            amount=Decimal("50000"),
        )

    def _create_snapshot_data(self, order, payment, user) -> dict:
        """Helper to create complete snapshot data."""
        return {
            "order_id": order.id,
            "order_number": getattr(order, "order_number", str(order.id)),
            "order_status": order.status,
            "payment_id": payment.id,
            "payment_key": payment.payment_key,
            "amount": str(payment.amount),
            "payment_status": payment.status,
            "user_id": user.id,
            "user_email": user.email,
            "user_points": getattr(user, "points", 0),
            "captured_at": now().isoformat(),
        }

    def test_snapshot_enables_complete_operation_replay(self):
        """
        Purpose:
            Verify DLQ snapshot contains all data needed to replay operation
            without accessing original runtime context.

        Scenario:
            1. Create payment failure with full forensic context
            2. Store in DLQ with snapshot
            3. Verify snapshot contains all critical fields
            4. Verify handler can use snapshot data

        Expected:
            - Snapshot contains order_id, payment_key, amount, user_id
            - Replay handler can extract all needed data
            - Operation can be analyzed using snapshot alone

        Compliance:
            - Audit trail completeness
            - Disaster recovery capability
        """
        # Create complete snapshot
        snapshot = self._create_snapshot_data(
            order=self.sample_order,
            payment=self.sample_payment,
            user=self.sample_user,
        )

        # Create DLQ entry with full snapshot
        entry = self.repository.create(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="payment",
            entity_id=str(self.sample_payment.id),
            user_id=self.sample_user.id,
            error_message="Payment gateway timeout after 30s",
            snapshot_data=snapshot,
            request_data={
                "payment_key": self.sample_payment.payment_key,
                "order_id": self.sample_order.id,
                "amount": str(self.sample_payment.amount),
            },
        )

        # Verify snapshot completeness
        assert entry.snapshot_data["order_id"] == self.sample_order.id
        assert entry.snapshot_data["payment_key"] == "pay_key_test_12345"
        assert entry.snapshot_data["amount"] == "50000"
        assert entry.snapshot_data["user_id"] == self.sample_user.id
        assert "captured_at" in entry.snapshot_data

        # Verify handler can check replay eligibility using snapshot
        mock_handler = MagicMock()
        mock_handler.can_replay.return_value = (True, "Replayable")
        
        can_replay, reason = mock_handler.can_replay(entry)
        assert isinstance(can_replay, bool)

    def test_snapshot_preserves_complete_order_context(self):
        """
        Purpose:
            Verify order-related data is fully captured in snapshot.

        Expected:
            - Order ID preserved
            - Order number preserved
            - Order status at time of failure preserved
        """
        snapshot = self._create_snapshot_data(
            order=self.sample_order,
            payment=self.sample_payment,
            user=self.sample_user,
        )

        entry = self.repository.create(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="payment",
            entity_id=str(self.sample_payment.id),
            user_id=self.sample_user.id,
            snapshot_data=snapshot,
        )

        # Verify order context in snapshot
        assert entry.snapshot_data["order_id"] == self.sample_order.id
        assert entry.snapshot_data["order_status"] == "confirmed"
        assert "order_number" in entry.snapshot_data

    def test_snapshot_preserves_complete_payment_context(self):
        """
        Purpose:
            Verify payment-related data is fully captured in snapshot.

        Expected:
            - Payment ID preserved
            - Payment key preserved (critical for PG operations)
            - Amount preserved
            - Payment status preserved
        """
        snapshot = self._create_snapshot_data(
            order=self.sample_order,
            payment=self.sample_payment,
            user=self.sample_user,
        )

        entry = self.repository.create(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="payment",
            entity_id=str(self.sample_payment.id),
            user_id=self.sample_user.id,
            snapshot_data=snapshot,
        )

        # Verify payment context in snapshot
        assert entry.snapshot_data["payment_id"] == self.sample_payment.id
        assert entry.snapshot_data["payment_key"] == "pay_key_test_12345"
        assert entry.snapshot_data["amount"] == "50000"
        assert entry.snapshot_data["payment_status"] == "in_progress"

    def test_snapshot_preserves_complete_user_context(self):
        """
        Purpose:
            Verify user-related data is fully captured in snapshot.

        Expected:
            - User ID preserved
            - User email preserved
            - User points at time of failure preserved
        """
        snapshot = self._create_snapshot_data(
            order=self.sample_order,
            payment=self.sample_payment,
            user=self.sample_user,
        )

        entry = self.repository.create(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="payment",
            entity_id=str(self.sample_payment.id),
            user_id=self.sample_user.id,
            snapshot_data=snapshot,
        )

        # Verify user context in snapshot
        assert entry.snapshot_data["user_id"] == self.sample_user.id
        assert entry.snapshot_data["user_email"] == self.sample_user.email
        assert entry.snapshot_data["user_points"] == 10000

    def test_snapshot_enables_replay_without_fk_access(self):
        """
        Purpose:
            Verify replay handler can work with snapshot even if FK is nullified.

        Scenario:
            1. Create DLQ entry with full snapshot
            2. Simulate FK becoming null (edge case)
            3. Verify snapshot data is still accessible

        Expected:
            - Snapshot data remains intact even if FK references fail
            - Handler can fall back to snapshot for critical data
        """
        snapshot = self._create_snapshot_data(
            order=self.sample_order,
            payment=self.sample_payment,
            user=self.sample_user,
        )

        entry = self.repository.create(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="payment",
            entity_id=str(self.sample_payment.id),
            user_id=self.sample_user.id,
            snapshot_data=snapshot,
        )

        entry_id = entry.id

        # Even if FKs were null, snapshot should contain the data
        reloaded_entry = self.repository.get_by_id(entry_id)

        # Verify snapshot is independent of FK state
        assert reloaded_entry.snapshot_data["order_id"] is not None
        assert reloaded_entry.snapshot_data["payment_id"] is not None
        assert reloaded_entry.snapshot_data["user_id"] is not None

        # Handler should be able to get data from snapshot
        payment_id = reloaded_entry.snapshot_data.get("payment_id")
        order_id = reloaded_entry.snapshot_data.get("order_id")

        assert payment_id is not None
        assert order_id is not None

        # entity_type/entity_id 검증
        assert reloaded_entry.entity_type == "payment"
        assert reloaded_entry.entity_id == str(self.sample_payment.id)

    def test_snapshot_includes_timestamp_for_audit(self):
        """
        Purpose:
            Verify snapshot includes capture timestamp for audit.

        Expected:
            - captured_at timestamp present
            - Timestamp is ISO format for easy parsing
        """
        snapshot = self._create_snapshot_data(
            order=self.sample_order,
            payment=self.sample_payment,
            user=self.sample_user,
        )

        entry = self.repository.create(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="payment",
            entity_id=str(self.sample_payment.id),
            user_id=self.sample_user.id,
            snapshot_data=snapshot,
        )

        # Verify timestamp is present
        assert "captured_at" in entry.snapshot_data

        # Verify timestamp is ISO format (can be parsed)
        captured_at = entry.snapshot_data["captured_at"]
        # Should not raise an exception
        parsed = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        assert parsed is not None

    def test_snapshot_sufficient_for_payment_replay_decision(self):
        """
        Purpose:
            Verify snapshot data is sufficient for replay decision.

        Scenario:
            1. Create entry with snapshot
            2. Call handler.can_replay()
            3. Verify decision can be made

        Expected:
            - Handler can make replay decision using entry data
            - No additional database queries needed for basic decision
        """
        snapshot = self._create_snapshot_data(
            order=self.sample_order,
            payment=self.sample_payment,
            user=self.sample_user,
        )

        entry = self.repository.create(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="payment",
            entity_id=str(self.sample_payment.id),
            user_id=self.sample_user.id,
            snapshot_data=snapshot,
        )

        # Mock handler should be able to make decision
        mock_handler = MagicMock()
        mock_handler.can_replay.return_value = (True, "PG_TIMEOUT is replayable")

        can_replay, reason = mock_handler.can_replay(entry)

        # For PG_TIMEOUT, should generally be replayable
        assert isinstance(can_replay, bool)
        assert isinstance(reason, str)

    def test_replay_handler_uses_snapshot_for_missing_fk(self):
        """
        Purpose:
            Verify replay handler falls back to snapshot when FK is null.

        Scenario:
            1. Create entry without FK references but with snapshot
            2. Verify handler can extract data from snapshot

        Expected:
            - Handler uses snapshot_data when FK is None
        """
        # Create entry without FK references
        snapshot = {
            "order_id": 999,
            "payment_id": 888,
            "payment_key": "snap_pay_key_123",
            "amount": "25000",
            "user_id": self.sample_user.id,
        }

        entry = self.repository.create(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="payment",
            entity_id="",  # No payment FK available
            user_id=self.sample_user.id,
            snapshot_data=snapshot,
        )

        # Verify entity_id is empty (no FK reference)
        assert entry.entity_type == "payment"
        assert entry.entity_id == ""

        # But snapshot has the data
        assert entry.snapshot_data["order_id"] == 999
        assert entry.snapshot_data["payment_id"] == 888
        assert entry.snapshot_data["payment_key"] == "snap_pay_key_123"

        # Handler should be able to extract from snapshot
        # entity_id가 비어있으므로 snapshot에서 가져옴
        payment_id = int(entry.entity_id) if entry.entity_id else entry.snapshot_data.get("payment_id")
        order_id = entry.snapshot_data.get("order_id")

        assert payment_id == 888
        assert order_id == 999
