"""
DLQ Retention Policy Tests

Tests for Dead Letter Queue retention and archival functionality.
Validates the implementation of automatic archival after retention period.

Reference:
- Gap Report: G-04 (DLQ Auto-Archive After Retention Period)
- docs/L3_SELF_HEALING_OPERATIONS.md §1 (DLQ)

Compliance:
- SOC 2: Audit trail retention
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from shopping.models.failed_operation import FailedOperation
from shopping.tasks.dlq_replay_tasks import cleanup_resolved_dlq_entries
from shopping.tests.factories import OrderFactory, UserFactory


@pytest.mark.django_db(transaction=True)
class TestDLQRetentionPolicy:
    """
    Tests for DLQ retention and archival.

    Validates:
    - Soft-delete pattern (no hard deletes)
    - Archive after retention period
    - Audit trail preservation
    """

    @pytest.fixture
    def sample_user(self):
        """Create a sample user."""
        return UserFactory()

    @pytest.fixture
    def sample_order(self, sample_user):
        """Create a sample order."""
        return OrderFactory(user=sample_user)

    def test_dlq_auto_archive_after_retention_period(self, sample_order, sample_user):
        """
        Purpose:
            Verify DLQ entries are archived (not deleted) after retention.

        Scenario:
            1. Create resolved DLQ entry
            2. Backdate created_at beyond retention period (30 days)
            3. Run cleanup task
            4. Verify entry is ARCHIVED, not deleted

        Expected:
            - Entry status = ARCHIVED
            - Entry still exists in database
            - Entry excluded from pending queries
            - Entry included in audit queries

        Compliance:
            - SOC 2: Audit trail retention

        Risk Covered:
            - Data loss due to premature deletion
            - Compliance violation from missing audit trail
        """
        # Create entry
        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            order=sample_order,
            user=sample_user,
            error_message="Connection timed out during payment processing",
        )

        # Mark as resolved (cleanup only archives resolved entries)
        entry.mark_as_resolved(note="Fixed by PG system recovery")

        # Backdate to simulate 60 days passing
        FailedOperation.objects.filter(id=entry.id).update(created_at=timezone.now() - timedelta(days=60))
        entry.refresh_from_db()

        # Record initial count
        initial_total = FailedOperation.objects.count()

        # Run cleanup (archives entries older than 30 days)
        result = cleanup_resolved_dlq_entries(days_old=30)

        # Verify task completed successfully
        assert result["success"] is True
        assert result["archived_count"] >= 1

        # Verify entry still exists (soft delete, not hard delete)
        entry.refresh_from_db()
        assert entry.status == FailedOperation.Status.ARCHIVED

        # Verify total count unchanged (no hard delete)
        final_total = FailedOperation.objects.count()
        assert final_total == initial_total

        # Verify excluded from pending queries
        pending = FailedOperation.objects.filter(status=FailedOperation.Status.PENDING)
        assert entry not in pending

        # Verify included in all queries (audit trail preserved)
        all_entries = FailedOperation.objects.all()
        assert entry in all_entries

    def test_dlq_expired_entries_marked_correctly(self, sample_order, sample_user):
        """
        Purpose:
            Verify pending entries past expiration are marked as EXPIRED.

        Scenario:
            1. Create pending DLQ entry with past expires_at
            2. Run cleanup task
            3. Verify entry is marked EXPIRED

        Expected:
            - Entry status changes from PENDING to EXPIRED
            - Resolution type is EXPIRED
            - Entry retained for audit
        """
        # Create entry
        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            order=sample_order,
            user=sample_user,
            error_message="Test expired entry",
        )

        # Backdate expires_at to simulate past expiration
        FailedOperation.objects.filter(id=entry.id).update(expires_at=timezone.now() - timedelta(days=1))
        entry.refresh_from_db()

        # Verify entry is still pending
        assert entry.status == FailedOperation.Status.PENDING

        # Run cleanup
        result = cleanup_resolved_dlq_entries(days_old=30)

        # Verify task completed
        assert result["success"] is True
        assert result["expired_count"] >= 1

        # Verify entry marked as expired
        entry.refresh_from_db()
        assert entry.status == FailedOperation.Status.EXPIRED
        assert entry.resolution_type == FailedOperation.ResolutionType.EXPIRED

    def test_dlq_recent_resolved_entries_not_archived(self, sample_order, sample_user):
        """
        Purpose:
            Verify recent resolved entries are NOT archived prematurely.

        Scenario:
            1. Create resolved DLQ entry (today)
            2. Run cleanup task
            3. Verify entry is NOT archived

        Expected:
            - Entry status remains RESOLVED
            - Entry not archived until retention period passes
        """
        # Create entry
        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            order=sample_order,
            user=sample_user,
            error_message="Recently resolved entry",
        )

        # Mark as resolved immediately (recent)
        entry.mark_as_resolved(note="Fixed immediately")

        # Run cleanup with 30-day retention
        result = cleanup_resolved_dlq_entries(days_old=30)

        # Verify task completed
        assert result["success"] is True

        # Verify entry NOT archived (too recent)
        entry.refresh_from_db()
        assert entry.status == FailedOperation.Status.RESOLVED
        assert entry.status != FailedOperation.Status.ARCHIVED

    def test_dlq_archive_preserves_all_fields(self, sample_order, sample_user):
        """
        Purpose:
            Verify archival preserves all original data for audit.

        Scenario:
            1. Create DLQ entry with full forensic context
            2. Resolve and backdate
            3. Archive
            4. Verify all original data preserved

        Expected:
            - snapshot_data intact
            - request_data intact
            - error_message intact
            - All FK references preserved
        """
        snapshot = {
            "order_id": sample_order.id,
            "amount": "50000",
            "payment_key": "test_payment_key_123",
        }
        request_data = {
            "method": "POST",
            "path": "/api/payments/confirm",
            "body": {"payment_key": "test_payment_key_123"},
        }

        # Create entry with full context
        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            order=sample_order,
            user=sample_user,
            error_code="ETIMEDOUT",
            error_message="Connection timed out after 30s",
            snapshot_data=snapshot,
            request_data=request_data,
            metadata={"attempt": 3, "elapsed_ms": 30000},
        )

        # Resolve and backdate
        entry.mark_as_resolved(note="PG recovered")
        FailedOperation.objects.filter(id=entry.id).update(created_at=timezone.now() - timedelta(days=60))

        # Run cleanup
        cleanup_resolved_dlq_entries(days_old=30)

        # Reload and verify all data preserved
        entry.refresh_from_db()
        assert entry.status == FailedOperation.Status.ARCHIVED

        # Verify all fields preserved
        assert entry.snapshot_data == snapshot
        assert entry.request_data == request_data
        assert entry.error_code == "ETIMEDOUT"
        assert "Connection timed out" in entry.error_message
        assert entry.metadata == {"attempt": 3, "elapsed_ms": 30000}

        # Verify FK references preserved
        assert entry.order_id == sample_order.id
        assert entry.user_id == sample_user.id

    def test_dlq_rejected_entries_archived_after_retention(self, sample_order, sample_user):
        """
        Purpose:
            Verify rejected entries are also archived after retention.

        Scenario:
            1. Create rejected DLQ entry
            2. Backdate beyond retention
            3. Run cleanup
            4. Verify archived

        Expected:
            - REJECTED entries archived like RESOLVED
            - Status changes to ARCHIVED
        """
        # Create entry
        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="AMOUNT_MISMATCH",
            order=sample_order,
            user=sample_user,
            error_message="Amount mismatch - unrecoverable",
        )

        # Mark as rejected (unrecoverable)
        entry.mark_as_rejected(note="Amount mismatch cannot be auto-recovered")

        # Backdate
        FailedOperation.objects.filter(id=entry.id).update(created_at=timezone.now() - timedelta(days=60))

        # Run cleanup
        result = cleanup_resolved_dlq_entries(days_old=30)

        # Verify archived
        assert result["success"] is True
        entry.refresh_from_db()
        assert entry.status == FailedOperation.Status.ARCHIVED
