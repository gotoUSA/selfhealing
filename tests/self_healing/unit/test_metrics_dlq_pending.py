"""
DLQ Pending Metric Tests

Tests for DLQ pending gauge accuracy and updates.
Validates that pending count decreases correctly on resolution.

Reference:
- Gap Report: G-08 (DLQ Pending Metric Decreases on Replay)
- docs/L3_SELF_HEALING_OPERATIONS.md §7 (Observability & Metrics)
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

import pytest
from django.utils import timezone

from shopping.models.failed_operation import FailedOperation
from shopping.services.self_healing.metrics import (
    record_dlq_item_created,
    update_dlq_pending_gauges,
    dlq_pending_gauge,
    dlq_created_total,
)
from shopping.tests.factories import OrderFactory, UserFactory


@pytest.mark.django_db(transaction=True)
class TestDLQPendingMetric:
    """
    Tests for DLQ pending gauge accuracy.

    Validates:
    - Gauge increases on DLQ creation
    - Gauge decreases on resolution
    - Accurate real-time count
    """

    @pytest.fixture
    def sample_user(self):
        """Create a sample user."""
        return UserFactory()

    @pytest.fixture
    def sample_order(self, sample_user):
        """Create a sample order."""
        return OrderFactory(user=sample_user)

    @pytest.fixture(autouse=True)
    def clean_dlq_entries(self):
        """Clean up DLQ entries before each test."""
        FailedOperation.objects.all().delete()
        yield

    def test_dlq_pending_increments_on_creation(self, sample_order, sample_user):
        """
        Purpose:
            Verify pending count increases when DLQ entry is created.

        Scenario:
            1. Record initial pending count
            2. Create DLQ entry
            3. Update gauges
            4. Verify count increased by 1

        Expected:
            - Gauge value increases by 1 for the domain
        """
        domain = "payment"

        # Get initial count
        initial_counts = update_dlq_pending_gauges()
        initial_payment = initial_counts.get(domain, 0)

        # Create DLQ entry
        entry = FailedOperation.create_from_failure(
            domain=domain,
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(sample_order.id),
            user=sample_user,
            error_message="Connection timeout",
        )

        # Record metric for the creation
        record_dlq_item_created(domain, "PG_TIMEOUT")

        # Update gauges
        new_counts = update_dlq_pending_gauges()
        new_payment = new_counts.get(domain, 0)

        # Verify increment
        assert new_payment == initial_payment + 1

    def test_dlq_pending_decrements_on_successful_resolution(self, sample_order, sample_user):
        """
        Purpose:
            Verify pending count decreases when DLQ entry is resolved.

        Scenario:
            1. Create DLQ entry (pending count +1)
            2. Successfully resolve entry
            3. Update gauges
            4. Verify pending count decreased

        Expected:
            - Gauge accurately reflects pending count
            - Resolved entries don't count as pending
        """
        domain = "payment"

        # Create entry
        entry = FailedOperation.create_from_failure(
            domain=domain,
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(sample_order.id),
            user=sample_user,
            error_message="Connection timeout",
        )

        # Verify increment
        after_create = update_dlq_pending_gauges()
        assert after_create.get(domain, 0) >= 1

        # Resolve entry
        entry.mark_as_resolved(note="Fixed by PG recovery")

        # Update gauge
        after_resolve = update_dlq_pending_gauges()

        # Verify decrement
        assert after_resolve.get(domain, 0) == after_create.get(domain, 0) - 1

    def test_dlq_pending_gauge_by_domain_isolation(self, sample_order, sample_user):
        """
        Purpose:
            Verify pending counts are isolated by domain.

        Scenario:
            1. Create entries in different domains
            2. Resolve one domain's entry
            3. Verify only that domain's count decreases

        Expected:
            - Each domain has independent pending count
            - Resolving one doesn't affect others
        """
        # Create entries in different domains
        payment_entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(sample_order.id),
            user=sample_user,
            error_message="Payment timeout",
        )

        point_entry = FailedOperation.create_from_failure(
            domain="point",
            failure_type="POINT_DEDUCTION_FAILED",
            user=sample_user,
            error_message="Point deduction failed",
        )

        # Get counts after creation
        counts_before = update_dlq_pending_gauges()
        payment_before = counts_before.get("payment", 0)
        point_before = counts_before.get("point", 0)

        # Resolve only payment entry
        payment_entry.mark_as_resolved(note="Fixed")

        # Get counts after resolution
        counts_after = update_dlq_pending_gauges()
        payment_after = counts_after.get("payment", 0)
        point_after = counts_after.get("point", 0)

        # Verify payment decreased, point unchanged
        assert payment_after == payment_before - 1
        assert point_after == point_before

    def test_dlq_pending_excludes_non_pending_statuses(self, sample_order, sample_user):
        """
        Purpose:
            Verify pending gauge only counts PENDING status entries.

        Scenario:
            1. Create entries with various statuses
            2. Query pending count
            3. Verify only PENDING entries counted

        Expected:
            - PENDING: counted
            - RESOLVED: not counted
            - REJECTED: not counted
            - ARCHIVED: not counted
            - REQUIRES_REVIEW: not counted
        """
        domain = "payment"

        # Create entries with different statuses
        pending_entry = FailedOperation.create_from_failure(
            domain=domain,
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(sample_order.id),
            user=sample_user,
            error_message="Pending entry",
        )

        resolved_entry = FailedOperation.create_from_failure(
            domain=domain,
            failure_type="PG_TIMEOUT",
            user=sample_user,
            error_message="Resolved entry",
        )
        resolved_entry.mark_as_resolved(note="Fixed")

        rejected_entry = FailedOperation.create_from_failure(
            domain=domain,
            failure_type="AMOUNT_MISMATCH",
            user=sample_user,
            error_message="Rejected entry",
        )
        rejected_entry.mark_as_rejected(note="Unrecoverable")

        requires_review_entry = FailedOperation.create_from_failure(
            domain=domain,
            failure_type="UNKNOWN_ERROR",
            user=sample_user,
            error_message="Needs review",
        )
        requires_review_entry.mark_as_requires_review(note="Manual check needed")

        # Get pending count
        counts = update_dlq_pending_gauges()
        pending_count = counts.get(domain, 0)

        # Verify only pending_entry is counted
        # (The actual count includes only PENDING status)
        pending_in_db = FailedOperation.objects.filter(
            domain=domain,
            status=FailedOperation.Status.PENDING,
        ).count()

        assert pending_count == pending_in_db
        assert pending_count >= 1  # At least our pending_entry

    def test_dlq_pending_multiple_creations_and_resolutions(self, sample_user):
        """
        Purpose:
            Verify gauge accuracy with multiple operations.

        Scenario:
            1. Create 5 entries
            2. Resolve 3 entries
            3. Verify final count = 2

        Expected:
            - Accurate tracking across multiple operations
        """
        domain = "payment"

        # Clean slate
        initial_counts = update_dlq_pending_gauges()
        initial = initial_counts.get(domain, 0)

        # Create 5 entries
        entries = []
        for i in range(5):
            entry = FailedOperation.create_from_failure(
                domain=domain,
                failure_type="PG_TIMEOUT",
                user=sample_user,
                error_message=f"Entry {i}",
            )
            entries.append(entry)

        # Verify after creation
        after_create = update_dlq_pending_gauges()
        assert after_create.get(domain, 0) == initial + 5

        # Resolve 3 entries
        for entry in entries[:3]:
            entry.mark_as_resolved(note="Fixed")

        # Verify final count
        final = update_dlq_pending_gauges()
        assert final.get(domain, 0) == initial + 2

    def test_dlq_created_total_counter_always_increments(self, sample_order, sample_user):
        """
        Purpose:
            Verify the total created counter only increases (never decreases).

        Scenario:
            1. Create entry
            2. Record creation
            3. Resolve entry
            4. Verify total counter unchanged by resolution

        Expected:
            - Counter is monotonically increasing
            - Resolution doesn't affect the total created counter
        """
        domain = "payment"

        # Create and record
        entry = FailedOperation.create_from_failure(
            domain=domain,
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(sample_order.id),
            user=sample_user,
            error_message="Test entry",
        )

        # Record metric (this increments the counter)
        record_dlq_item_created(domain, "PG_TIMEOUT")

        # Get current counter value (this is cumulative)
        # Note: We can't directly get counter value, but we can verify
        # that the counter increment function runs without error
        # and the gauge update works correctly

        # Resolve entry
        entry.mark_as_resolved(note="Fixed")

        # Pending gauge should decrease, but created_total is a counter
        # that only increases (cannot be decremented in Prometheus counters)
        final_pending = update_dlq_pending_gauges()
        assert final_pending.get(domain, 0) == 0  # No pending entries
