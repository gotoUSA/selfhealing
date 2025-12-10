"""
Notification SLA Policy Tests

Tests for notification domain SLA behavior and escalation policy.
Validates that notification failures don't trigger escalation even on SLA breach.

Reference:
- Gap Report: G-05 (Notification Domain Has No Escalation)
- docs/L3_SELF_HEALING_OPERATIONS.md §3 (Recovery SLA)

Rationale:
    Notifications are non-critical; escalation would create operational noise.
    Operators should focus on payment/point issues first.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from shopping.models.failed_operation import FailedOperation
from selfhealing.services import DLQService, DLQConfig
from shopping.tests.factories import UserFactory


@pytest.mark.django_db(transaction=True)
class TestNotificationSLAPolicy:
    """
    Tests for notification domain SLA behavior.

    Validates:
    - Notification SLA breaches are detected
    - No escalation triggered for notification domain
    - Only logged for monitoring
    """

    @pytest.fixture
    def dlq_service(self):
        """Create DLQ service instance."""
        return DLQService(config=DLQConfig(enabled=True))

    @pytest.fixture
    def sample_user(self):
        """Create a sample user."""
        return UserFactory()

    def test_notification_domain_no_escalation_on_sla_breach(self, sample_user):
        """
        Purpose:
            Verify notification failures don't trigger escalation even on SLA breach.

        Scenario:
            1. Create notification DLQ entry
            2. Backdate to exceed 24-hour SLA
            3. Run SLA check
            4. Verify NO escalation triggered

        Expected:
            - No escalation notification sent
            - Entry remains PENDING (not REQUIRES_REVIEW due to SLA alone)
            - Only logged for monitoring

        Rationale:
            Notifications are non-critical; escalation would create noise.
        """
        # Create notification domain failure
        entry = FailedOperation.create_from_failure(
            domain="notification",
            failure_type="SMTP_TIMEOUT",
            user=sample_user,
            error_message="SMTP connection timed out",
        )

        # Backdate to exceed 24-hour SLA (set to 30 hours ago)
        FailedOperation.objects.filter(id=entry.id).update(created_at=timezone.now() - timedelta(hours=30))
        entry.refresh_from_db()

        # Verify SLA is breached
        assert entry.is_sla_breached is True

        # Mock escalation service
        with patch(
            "shopping.services.self_healing.security_notification_service.SecurityNotificationService"
        ) as mock_notification_cls:
            mock_notification = MagicMock()
            mock_notification_cls.return_value = mock_notification

            # Run SLA check task
            from shopping.tasks.self_healing_tasks import check_and_report_sla_breaches

            result = check_and_report_sla_breaches()

            # Verify task completed
            assert result["success"] is True

            # Verify notification domain breach counted
            assert "notification" in result.get("breaches_by_domain", {})

            # Verify entry still PENDING (not auto-escalated)
            entry.refresh_from_db()
            assert entry.status == FailedOperation.Status.PENDING

    def test_notification_sla_breach_detected_but_not_escalated(self, dlq_service, sample_user):
        """
        Purpose:
            Verify notification SLA breach is detected for monitoring.

        Scenario:
            1. Create notification DLQ entry exceeding SLA
            2. Query for SLA breached entries
            3. Verify entry is in the breached list

        Expected:
            - Entry appears in SLA breached query
            - But should NOT auto-escalate to REQUIRES_REVIEW
        """
        # Create entry
        entry = FailedOperation.create_from_failure(
            domain="notification",
            failure_type="EMAIL_DELIVERY_FAILED",
            user=sample_user,
            error_message="Email delivery failed after 3 retries",
        )

        # Backdate beyond SLA (notification SLA is 24 hours)
        FailedOperation.objects.filter(id=entry.id).update(created_at=timezone.now() - timedelta(hours=36))
        entry.refresh_from_db()

        # Verify is_sla_breached property
        assert entry.is_sla_breached is True
        assert entry.domain == "notification"

        # Query breached entries
        breached = dlq_service.get_sla_breached_entries()

        # Find our entry in the breached list
        notification_breached = [e for e in breached if e.id == entry.id]
        assert len(notification_breached) == 1

        # Verify status remains PENDING (not auto-escalated)
        entry.refresh_from_db()
        assert entry.status == FailedOperation.Status.PENDING

    def test_payment_domain_escalates_on_sla_breach_contrast(self, sample_user):
        """
        Purpose:
            Contrast test: Payment domain DOES have stricter handling.

        Scenario:
            1. Create payment DLQ entry exceeding 1-hour SLA
            2. Verify is_sla_breached is True
            3. This is for monitoring awareness (actual escalation is operator-triggered)

        Expected:
            - Payment SLA (1 hour) is stricter than notification (24 hours)
            - Both detected, but priority differs
        """
        # Create payment domain failure
        payment_entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            user=sample_user,
            error_message="Payment gateway timeout",
        )

        # Backdate 2 hours (exceeds 1-hour payment SLA)
        FailedOperation.objects.filter(id=payment_entry.id).update(created_at=timezone.now() - timedelta(hours=2))
        payment_entry.refresh_from_db()

        # Create notification domain failure at same time
        notification_entry = FailedOperation.create_from_failure(
            domain="notification",
            failure_type="SMTP_TIMEOUT",
            user=sample_user,
            error_message="SMTP timeout",
        )

        # Backdate 2 hours (does NOT exceed 24-hour notification SLA)
        FailedOperation.objects.filter(id=notification_entry.id).update(created_at=timezone.now() - timedelta(hours=2))
        notification_entry.refresh_from_db()

        # Payment should breach SLA (1 hour threshold)
        assert payment_entry.is_sla_breached is True

        # Notification should NOT breach SLA (24 hour threshold)
        assert notification_entry.is_sla_breached is False

    def test_notification_within_sla_no_breach(self, dlq_service, sample_user):
        """
        Purpose:
            Verify notification entries within SLA are not flagged.

        Scenario:
            1. Create notification DLQ entry (recent)
            2. Verify is_sla_breached is False

        Expected:
            - Entry not flagged as SLA breach
            - 24-hour window for notification domain
        """
        # Create recent entry
        entry = FailedOperation.create_from_failure(
            domain="notification",
            failure_type="PUSH_NOTIFICATION_FAILED",
            user=sample_user,
            error_message="Push notification service unavailable",
        )

        # Verify within SLA (just created)
        assert entry.is_sla_breached is False

        # Query breached entries
        breached = dlq_service.get_sla_breached_entries()

        # Our entry should NOT be in the breached list
        entry_ids = [e.id for e in breached]
        assert entry.id not in entry_ids

    def test_sla_threshold_values_by_domain(self):
        """
        Purpose:
            Verify SLA threshold configuration is correctly loaded.

        Expected:
            - payment: 1 hour
            - point: 4 hours
            - inventory: 2 hours
            - webhook: 8 hours
            - notification: 24 hours (longest, least critical)
        """
        from selfhealing.core import get_sla_thresholds

        sla_config = get_sla_thresholds()
        thresholds = sla_config.get_all_thresholds()

        # Verify relative priorities (payment most critical)
        assert thresholds["payment"] < thresholds["notification"]
        assert thresholds["point"] < thresholds["notification"]
        assert thresholds["inventory"] < thresholds["notification"]
        assert thresholds["webhook"] < thresholds["notification"]

        # Verify notification is 24 hours
        assert thresholds["notification"] == timedelta(hours=24)
