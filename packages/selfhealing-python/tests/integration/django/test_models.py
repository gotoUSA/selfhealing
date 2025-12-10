"""
Django Model Tests for Self-Healing System.

Tests the Django ORM models:
- FailedOperation
- CircuitBreakerState
- SecurityIncident
"""

import pytest
from django.utils import timezone
from datetime import timedelta


pytestmark = pytest.mark.django_db


class TestFailedOperationModel:
    """Tests for FailedOperation model."""

    def test_create_failed_operation(self):
        """Test creating a failed operation."""
        from selfhealing.adapters.django.models import FailedOperation

        op = FailedOperation.objects.create(
            domain=FailedOperation.Domain.PAYMENT,
            failure_type="PG_TIMEOUT",
            error_message="Connection timed out",
            error_code="TIMEOUT_001",
        )

        assert op.id is not None
        assert op.domain == "payment"
        assert op.failure_type == "PG_TIMEOUT"
        assert op.status == FailedOperation.Status.PENDING
        assert op.retry_count == 0

    def test_mark_as_resolved(self):
        """Test marking operation as resolved."""
        from selfhealing.adapters.django.models import FailedOperation

        op = FailedOperation.objects.create(
            domain=FailedOperation.Domain.PAYMENT,
            failure_type="PG_TIMEOUT",
        )

        op.mark_as_resolved(
            resolution_type=FailedOperation.ResolutionType.MANUAL_FIX,
            note="Fixed by admin",
            resolved_by_id=1,
        )

        op.refresh_from_db()
        assert op.status == FailedOperation.Status.RESOLVED
        assert op.resolution_type == FailedOperation.ResolutionType.MANUAL_FIX
        assert op.resolution_note == "Fixed by admin"
        assert op.resolved_by_id == 1
        assert op.resolved_at is not None

    def test_increment_retry(self):
        """Test incrementing retry count."""
        from selfhealing.adapters.django.models import FailedOperation

        op = FailedOperation.objects.create(
            domain=FailedOperation.Domain.PAYMENT,
            failure_type="PG_TIMEOUT",
        )

        assert op.retry_count == 0
        assert op.last_retry_at is None

        op.increment_retry()
        op.refresh_from_db()

        assert op.retry_count == 1
        assert op.last_retry_at is not None


class TestCircuitBreakerStateModel:
    """Tests for CircuitBreakerState model."""

    def test_create_circuit_breaker(self):
        """Test creating a circuit breaker state."""
        from selfhealing.adapters.django.models import CircuitBreakerState

        cb = CircuitBreakerState.objects.create(
            service_name="payment",
            state=CircuitBreakerState.State.CLOSED,
        )

        assert cb.id is not None
        assert cb.service_name == "payment"
        assert cb.state == "closed"
        assert cb.failure_count == 0
        assert cb.success_count == 0

    def test_record_failure_opens_circuit(self):
        """Test that failures can open the circuit."""
        from selfhealing.adapters.django.models import CircuitBreakerState

        cb = CircuitBreakerState.objects.create(
            service_name="payment",
            state=CircuitBreakerState.State.CLOSED,
            failure_threshold=3,
        )

        # Record failures
        for i in range(3):
            cb.record_failure()
            cb.refresh_from_db()

        # Circuit should be open
        assert cb.state == CircuitBreakerState.State.OPEN
        assert cb.failure_count == 3
        assert cb.opened_at is not None

    def test_record_success_closes_half_open_circuit(self):
        """Test that successes close a half-open circuit."""
        from selfhealing.adapters.django.models import CircuitBreakerState

        cb = CircuitBreakerState.objects.create(
            service_name="payment",
            state=CircuitBreakerState.State.HALF_OPEN,
            half_open_max_calls=2,
        )

        # Record successes
        for i in range(2):
            cb.record_success()
            cb.refresh_from_db()

        # Circuit should be closed
        assert cb.state == CircuitBreakerState.State.CLOSED
        assert cb.failure_count == 0

    def test_reset_circuit(self):
        """Test resetting a circuit breaker."""
        from selfhealing.adapters.django.models import CircuitBreakerState

        cb = CircuitBreakerState.objects.create(
            service_name="payment",
            state=CircuitBreakerState.State.OPEN,
            failure_count=10,
            manually_controlled=True,
            control_reason="Maintenance",
        )

        cb.reset()
        cb.refresh_from_db()

        assert cb.state == CircuitBreakerState.State.CLOSED
        assert cb.failure_count == 0
        assert cb.manually_controlled is False
        assert cb.control_reason == ""

    def test_is_expired_manual_override(self):
        """Test checking if manual override has expired."""
        from selfhealing.adapters.django.models import CircuitBreakerState

        # Not expired
        cb = CircuitBreakerState.objects.create(
            service_name="payment",
            state=CircuitBreakerState.State.OPEN,
            manually_controlled=True,
            manual_override_expires_at=timezone.now() + timedelta(hours=1),
        )
        assert cb.is_expired_manual_override() is False

        # Expired
        cb.manual_override_expires_at = timezone.now() - timedelta(hours=1)
        cb.save()
        assert cb.is_expired_manual_override() is True


class TestSecurityIncidentModel:
    """Tests for SecurityIncident model."""

    def test_create_security_incident(self):
        """Test creating a security incident."""
        from selfhealing.adapters.django.models import SecurityIncident

        incident = SecurityIncident.objects.create(
            incident_type=SecurityIncident.IncidentType.WEBHOOK_SIGNATURE_INVALID,
            severity=SecurityIncident.Severity.HIGH,
            description="Invalid webhook signature detected",
            source_ip="192.168.1.100",
        )

        assert incident.id is not None
        assert incident.incident_type == "webhook_signature_invalid"
        assert incident.severity == "high"
        assert incident.status == SecurityIncident.Status.OPEN
        assert incident.source_ip == "192.168.1.100"

    def test_resolve_incident(self):
        """Test resolving a security incident."""
        from selfhealing.adapters.django.models import SecurityIncident

        incident = SecurityIncident.objects.create(
            incident_type=SecurityIncident.IncidentType.SUSPICIOUS_ACTIVITY,
            severity=SecurityIncident.Severity.MEDIUM,
            description="Suspicious login attempt",
        )

        incident.resolve(notes="False alarm", investigated_by_id=1)
        incident.refresh_from_db()

        assert incident.status == SecurityIncident.Status.RESOLVED
        assert incident.investigation_notes == "False alarm"
        assert incident.investigated_by_id == 1
        assert incident.resolved_at is not None
