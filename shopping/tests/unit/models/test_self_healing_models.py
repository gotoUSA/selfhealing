"""
FailedOperation and SecurityIncident model tests

Tests for L3 Self-Healing core models.
Reference: docs/L3_SELF_HEALING_OPERATIONS.md
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from shopping.models import FailedOperation, SecurityIncident
from shopping.tests.factories import OrderFactory, PaymentFactory, UserFactory


@pytest.mark.django_db
class TestFailedOperation:
    """FailedOperation (DLQ) model tests"""

    # ==========================================
    # Creation Tests
    # ==========================================

    def test_create_failed_operation_basic(self):
        """Basic DLQ entry creation"""
        user = UserFactory()
        order = OrderFactory(user=user)

        failed_op = FailedOperation.objects.create(
            domain=FailedOperation.Domain.PAYMENT,
            failure_type="PG_TIMEOUT",
            order=order,
            user=user,
            error_code="TIMEOUT",
            error_message="Connection timed out after 30s",
        )

        assert failed_op.id is not None
        assert failed_op.domain == "payment"
        assert failed_op.failure_type == "PG_TIMEOUT"
        assert failed_op.status == FailedOperation.Status.PENDING
        assert failed_op.retry_count == 0
        assert failed_op.max_retries == 2

    def test_create_from_failure_factory_method(self):
        """Factory method creates DLQ entry with defaults"""
        user = UserFactory()
        order = OrderFactory(user=user)

        failed_op = FailedOperation.create_from_failure(
            domain=FailedOperation.Domain.PAYMENT,
            failure_type="AMOUNT_MISMATCH",
            order=order,
            user=user,
            error_code="AMT_ERR",
            error_message="Amount mismatch detected",
            snapshot_data={"amount": 50000, "expected": 49000},
            next_action_hint="Verify payment in PG admin",
            recommended_action=FailedOperation.RecommendedAction.MANUAL_CHECK,
            retention_days=30,
        )

        assert failed_op.id is not None
        assert failed_op.expires_at is not None
        assert failed_op.snapshot_data["amount"] == 50000
        assert failed_op.next_action_hint == "Verify payment in PG admin"
        assert failed_op.recommended_action == "manual_check"

    # ==========================================
    # State Transition Tests
    # ==========================================

    def test_mark_as_resolved(self):
        """mark_as_resolved transitions to RESOLVED status"""
        user = UserFactory()
        failed_op = FailedOperation.objects.create(
            domain=FailedOperation.Domain.PAYMENT,
            failure_type="PG_TIMEOUT",
            user=user,
        )

        failed_op.mark_as_resolved(
            resolved_by=user,
            note="Manually confirmed payment",
            resolution_type=FailedOperation.ResolutionType.MANUAL_FIX,
        )

        failed_op.refresh_from_db()
        assert failed_op.status == FailedOperation.Status.RESOLVED
        assert failed_op.resolved_by == user
        assert failed_op.resolution_note == "Manually confirmed payment"
        assert failed_op.resolution_type == FailedOperation.ResolutionType.MANUAL_FIX
        assert failed_op.resolved_at is not None

    def test_mark_as_rejected(self):
        """mark_as_rejected transitions to REJECTED status"""
        user = UserFactory()
        failed_op = FailedOperation.objects.create(
            domain=FailedOperation.Domain.PAYMENT,
            failure_type="INVALID_CARD",
            user=user,
        )

        failed_op.mark_as_rejected(
            resolved_by=user,
            note="Card permanently declined",
        )

        failed_op.refresh_from_db()
        assert failed_op.status == FailedOperation.Status.REJECTED
        assert failed_op.resolution_type == "rejected"

    def test_queue_for_replay_increments_retry_count(self):
        """queue_for_replay increments retry count and changes status"""
        failed_op = FailedOperation.objects.create(
            domain=FailedOperation.Domain.WEBHOOK,
            failure_type="WEBHOOK_TIMEOUT",
        )

        failed_op.queue_for_replay()

        failed_op.refresh_from_db()
        assert failed_op.status == FailedOperation.Status.REPLAYED
        assert failed_op.retry_count == 1
        assert failed_op.last_retry_at is not None

    def test_queue_for_replay_raises_when_max_exceeded(self):
        """queue_for_replay raises ValueError when max retries exceeded"""
        failed_op = FailedOperation.objects.create(
            domain=FailedOperation.Domain.PAYMENT,
            failure_type="PG_TIMEOUT",
            retry_count=2,
            max_retries=2,
        )

        with pytest.raises(ValueError, match="Maximum replay attempts"):
            failed_op.queue_for_replay()

    def test_mark_as_reviewing(self):
        """mark_as_reviewing transitions to REVIEWING status"""
        user = UserFactory()
        failed_op = FailedOperation.objects.create(
            domain=FailedOperation.Domain.POINT,
            failure_type="DUPLICATE_DEDUCTION",
        )

        failed_op.mark_as_reviewing(reviewer=user)

        failed_op.refresh_from_db()
        assert failed_op.status == FailedOperation.Status.REVIEWING
        assert failed_op.metadata["reviewer_id"] == user.id

    def test_revert_to_pending(self):
        """revert_to_pending changes status back to PENDING"""
        failed_op = FailedOperation.objects.create(
            domain=FailedOperation.Domain.PAYMENT,
            failure_type="PG_TIMEOUT",
            status=FailedOperation.Status.REPLAYED,
        )

        failed_op.revert_to_pending(note="Replay failed again")

        failed_op.refresh_from_db()
        assert failed_op.status == FailedOperation.Status.PENDING
        assert "Replay failed again" in failed_op.error_message

    def test_mark_as_expired(self):
        """mark_as_expired transitions to EXPIRED status"""
        failed_op = FailedOperation.objects.create(
            domain=FailedOperation.Domain.NOTIFICATION,
            failure_type="SMTP_TIMEOUT",
        )

        failed_op.mark_as_expired()

        failed_op.refresh_from_db()
        assert failed_op.status == FailedOperation.Status.EXPIRED
        assert failed_op.resolution_type == "expired"

    # ==========================================
    # Property Tests
    # ==========================================

    def test_is_replayable_true_when_pending_and_under_max(self):
        """is_replayable returns True when conditions met"""
        failed_op = FailedOperation.objects.create(
            domain=FailedOperation.Domain.PAYMENT,
            failure_type="PG_TIMEOUT",
            status=FailedOperation.Status.PENDING,
            retry_count=0,
        )

        assert failed_op.is_replayable is True

    def test_is_replayable_false_when_max_exceeded(self):
        """is_replayable returns False when max retries exceeded"""
        failed_op = FailedOperation.objects.create(
            domain=FailedOperation.Domain.PAYMENT,
            failure_type="PG_TIMEOUT",
            status=FailedOperation.Status.PENDING,
            retry_count=2,
            max_retries=2,
        )

        assert failed_op.is_replayable is False

    def test_is_replayable_false_when_not_pending(self):
        """is_replayable returns False when not in PENDING status"""
        failed_op = FailedOperation.objects.create(
            domain=FailedOperation.Domain.PAYMENT,
            failure_type="PG_TIMEOUT",
            status=FailedOperation.Status.RESOLVED,
        )

        assert failed_op.is_replayable is False

    def test_age_seconds(self):
        """age_seconds returns correct age"""
        failed_op = FailedOperation.objects.create(
            domain=FailedOperation.Domain.PAYMENT,
            failure_type="PG_TIMEOUT",
        )

        assert failed_op.age_seconds >= 0
        assert failed_op.age_seconds < 5  # Should be very recent

    def test_is_sla_breached_false_when_within_threshold(self):
        """is_sla_breached returns False for recent entries"""
        failed_op = FailedOperation.objects.create(
            domain=FailedOperation.Domain.PAYMENT,
            failure_type="PG_TIMEOUT",
            status=FailedOperation.Status.PENDING,
        )

        assert failed_op.is_sla_breached is False

    def test_is_sla_breached_true_when_exceeded(self):
        """is_sla_breached returns True when SLA threshold exceeded"""
        failed_op = FailedOperation.objects.create(
            domain=FailedOperation.Domain.PAYMENT,
            failure_type="PG_TIMEOUT",
            status=FailedOperation.Status.PENDING,
        )
        # Manually set created_at to 2 hours ago (payment SLA is 1 hour)
        FailedOperation.objects.filter(id=failed_op.id).update(
            created_at=timezone.now() - timedelta(hours=2)
        )
        failed_op.refresh_from_db()

        assert failed_op.is_sla_breached is True

    # ==========================================
    # Domain Choice Tests
    # ==========================================

    @pytest.mark.parametrize(
        "domain",
        [
            FailedOperation.Domain.PAYMENT,
            FailedOperation.Domain.POINT,
            FailedOperation.Domain.INVENTORY,
            FailedOperation.Domain.WEBHOOK,
            FailedOperation.Domain.NOTIFICATION,
        ],
    )
    def test_all_domains_can_be_created(self, domain):
        """All domain types can be used"""
        failed_op = FailedOperation.objects.create(
            domain=domain,
            failure_type="TEST_FAILURE",
        )

        assert failed_op.domain == domain

    # ==========================================
    # String Representation Test
    # ==========================================

    def test_str_representation(self):
        """__str__ returns readable format"""
        failed_op = FailedOperation.objects.create(
            domain=FailedOperation.Domain.PAYMENT,
            failure_type="PG_TIMEOUT",
            status=FailedOperation.Status.PENDING,
        )

        assert "[payment]" in str(failed_op)
        assert "PG_TIMEOUT" in str(failed_op)
        assert "pending" in str(failed_op)


@pytest.mark.django_db
class TestSecurityIncident:
    """SecurityIncident model tests"""

    # ==========================================
    # Creation Tests
    # ==========================================

    def test_create_security_incident_basic(self):
        """Basic security incident creation"""
        incident = SecurityIncident.objects.create(
            incident_type=SecurityIncident.IncidentType.WEBHOOK_SIGNATURE_INVALID,
            severity=SecurityIncident.Severity.CRITICAL,
            description="Invalid HMAC signature detected",
            source_ip="192.168.1.100",
        )

        assert incident.id is not None
        assert incident.incident_type == "webhook_signature_invalid"
        assert incident.severity == "critical"
        assert incident.status == SecurityIncident.Status.OPEN
        assert incident.source_ip == "192.168.1.100"

    def test_create_incident_factory_method(self):
        """Factory method creates incident with auto-severity"""
        incident = SecurityIncident.create_incident(
            incident_type=SecurityIncident.IncidentType.PAYMENT_AMOUNT_TAMPERED,
            description="Request amount != response amount",
            source_ip="10.0.0.1",
            user_agent="Mozilla/5.0",
            immediate_action="Order frozen",
        )

        assert incident.id is not None
        assert incident.severity == SecurityIncident.Severity.CRITICAL
        assert "Order frozen" in incident.action_taken
        assert incident.status == SecurityIncident.Status.OPEN

    def test_create_incident_with_user_and_order(self):
        """Incident can reference user and order"""
        user = UserFactory()
        order = OrderFactory(user=user)

        incident = SecurityIncident.create_incident(
            incident_type=SecurityIncident.IncidentType.UNAUTHORIZED_ACCESS,
            description="Access to other user's order",
            user=user,
            order=order,
        )

        assert incident.user == user
        assert incident.order == order
        assert incident.severity == SecurityIncident.Severity.HIGH

    # ==========================================
    # State Transition Tests
    # ==========================================

    def test_start_investigation(self):
        """start_investigation changes status and assigns investigator"""
        investigator = UserFactory()
        incident = SecurityIncident.objects.create(
            incident_type=SecurityIncident.IncidentType.RATE_LIMIT_ABUSE,
            severity=SecurityIncident.Severity.MEDIUM,
            description="Excessive API calls",
        )

        incident.start_investigation(investigator)

        incident.refresh_from_db()
        assert incident.status == SecurityIncident.Status.INVESTIGATING
        assert incident.investigated_by == investigator

    def test_resolve_incident(self):
        """resolve marks incident as resolved"""
        investigator = UserFactory()
        incident = SecurityIncident.objects.create(
            incident_type=SecurityIncident.IncidentType.SUSPICIOUS_ACTIVITY,
            severity=SecurityIncident.Severity.MEDIUM,
            description="Unusual pattern detected",
        )

        incident.resolve(
            investigator=investigator,
            notes="Confirmed as legitimate activity from VPN user",
        )

        incident.refresh_from_db()
        assert incident.status == SecurityIncident.Status.RESOLVED
        assert incident.investigated_by == investigator
        assert "VPN user" in incident.investigation_notes
        assert incident.resolved_at is not None

    def test_resolve_as_false_positive(self):
        """resolve with is_false_positive marks as false positive"""
        investigator = UserFactory()
        incident = SecurityIncident.objects.create(
            incident_type=SecurityIncident.IncidentType.INJECTION_ATTEMPT,
            severity=SecurityIncident.Severity.HIGH,
            description="SQL injection attempt detected",
        )

        incident.resolve(
            investigator=investigator,
            notes="False alarm from monitoring tool",
            is_false_positive=True,
        )

        incident.refresh_from_db()
        assert incident.status == SecurityIncident.Status.FALSE_POSITIVE

    def test_add_action_taken(self):
        """add_action_taken appends to action log"""
        incident = SecurityIncident.objects.create(
            incident_type=SecurityIncident.IncidentType.TOKEN_FORGED,
            severity=SecurityIncident.Severity.CRITICAL,
            description="Invalid token signature",
        )

        incident.add_action_taken("User sessions invalidated")
        incident.add_action_taken("Security team notified")

        incident.refresh_from_db()
        assert "User sessions invalidated" in incident.action_taken
        assert "Security team notified" in incident.action_taken

    # ==========================================
    # Property Tests
    # ==========================================

    def test_is_open_true_for_open_status(self):
        """is_open returns True for OPEN status"""
        incident = SecurityIncident.objects.create(
            incident_type=SecurityIncident.IncidentType.RATE_LIMIT_ABUSE,
            severity=SecurityIncident.Severity.MEDIUM,
            description="Test",
            status=SecurityIncident.Status.OPEN,
        )

        assert incident.is_open is True

    def test_is_open_true_for_investigating_status(self):
        """is_open returns True for INVESTIGATING status"""
        incident = SecurityIncident.objects.create(
            incident_type=SecurityIncident.IncidentType.RATE_LIMIT_ABUSE,
            severity=SecurityIncident.Severity.MEDIUM,
            description="Test",
            status=SecurityIncident.Status.INVESTIGATING,
        )

        assert incident.is_open is True

    def test_is_open_false_for_resolved(self):
        """is_open returns False for RESOLVED status"""
        incident = SecurityIncident.objects.create(
            incident_type=SecurityIncident.IncidentType.RATE_LIMIT_ABUSE,
            severity=SecurityIncident.Severity.MEDIUM,
            description="Test",
            status=SecurityIncident.Status.RESOLVED,
        )

        assert incident.is_open is False

    def test_age_seconds(self):
        """age_seconds returns correct age"""
        incident = SecurityIncident.objects.create(
            incident_type=SecurityIncident.IncidentType.SUSPICIOUS_ACTIVITY,
            severity=SecurityIncident.Severity.MEDIUM,
            description="Test",
        )

        assert incident.age_seconds >= 0
        assert incident.age_seconds < 5

    # ==========================================
    # Query Helper Tests
    # ==========================================

    def test_get_open_by_ip(self):
        """get_open_by_ip returns matching incidents"""
        ip = "192.168.1.50"

        # Create incidents from same IP
        SecurityIncident.objects.create(
            incident_type=SecurityIncident.IncidentType.RATE_LIMIT_ABUSE,
            severity=SecurityIncident.Severity.MEDIUM,
            description="First attempt",
            source_ip=ip,
            status=SecurityIncident.Status.OPEN,
        )
        SecurityIncident.objects.create(
            incident_type=SecurityIncident.IncidentType.RATE_LIMIT_ABUSE,
            severity=SecurityIncident.Severity.MEDIUM,
            description="Second attempt",
            source_ip=ip,
            status=SecurityIncident.Status.OPEN,
        )
        # Different IP
        SecurityIncident.objects.create(
            incident_type=SecurityIncident.IncidentType.RATE_LIMIT_ABUSE,
            severity=SecurityIncident.Severity.MEDIUM,
            description="Other",
            source_ip="10.0.0.1",
            status=SecurityIncident.Status.OPEN,
        )

        incidents = SecurityIncident.get_open_by_ip(ip)

        assert incidents.count() == 2

    def test_get_open_by_user(self):
        """get_open_by_user returns matching incidents"""
        user = UserFactory()
        other_user = UserFactory()

        # Create incidents for user
        SecurityIncident.objects.create(
            incident_type=SecurityIncident.IncidentType.UNAUTHORIZED_ACCESS,
            severity=SecurityIncident.Severity.HIGH,
            description="First",
            user=user,
            status=SecurityIncident.Status.OPEN,
        )
        # Different user
        SecurityIncident.objects.create(
            incident_type=SecurityIncident.IncidentType.UNAUTHORIZED_ACCESS,
            severity=SecurityIncident.Severity.HIGH,
            description="Other",
            user=other_user,
            status=SecurityIncident.Status.OPEN,
        )

        incidents = SecurityIncident.get_open_by_user(user)

        assert incidents.count() == 1

    # ==========================================
    # Severity Mapping Tests
    # ==========================================

    @pytest.mark.parametrize(
        "incident_type,expected_severity",
        [
            (SecurityIncident.IncidentType.WEBHOOK_SIGNATURE_INVALID, "critical"),
            (SecurityIncident.IncidentType.PAYMENT_AMOUNT_TAMPERED, "critical"),
            (SecurityIncident.IncidentType.TOKEN_FORGED, "critical"),
            (SecurityIncident.IncidentType.REPLAY_ATTACK, "critical"),
            (SecurityIncident.IncidentType.UNAUTHORIZED_ACCESS, "high"),
            (SecurityIncident.IncidentType.INJECTION_ATTEMPT, "high"),
            (SecurityIncident.IncidentType.RATE_LIMIT_ABUSE, "medium"),
            (SecurityIncident.IncidentType.SUSPICIOUS_ACTIVITY, "medium"),
        ],
    )
    def test_severity_auto_assigned(self, incident_type, expected_severity):
        """Severity is automatically assigned based on incident type"""
        incident = SecurityIncident.create_incident(
            incident_type=incident_type,
            description="Test incident",
        )

        assert incident.severity == expected_severity

    # ==========================================
    # String Representation Test
    # ==========================================

    def test_str_representation(self):
        """__str__ returns readable format"""
        incident = SecurityIncident.objects.create(
            incident_type=SecurityIncident.IncidentType.TOKEN_FORGED,
            severity=SecurityIncident.Severity.CRITICAL,
            description="Test",
        )

        assert "[critical]" in str(incident)
        assert "token_forged" in str(incident)
        assert "open" in str(incident)
