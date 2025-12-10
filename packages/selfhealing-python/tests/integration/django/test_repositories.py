"""
Django Repository Tests for Self-Healing System.

Tests the Django ORM repository implementations:
- DjangoFailedOperationRepository
- DjangoCircuitBreakerStateRepository
- DjangoSecurityIncidentRepository
"""

import pytest
from django.utils import timezone
from datetime import timedelta


pytestmark = pytest.mark.django_db


class TestDjangoFailedOperationRepository:
    """Tests for DjangoFailedOperationRepository."""

    def test_create(self):
        """Test creating a failed operation via repository."""
        from selfhealing.adapters.django.repositories import DjangoFailedOperationRepository

        repo = DjangoFailedOperationRepository()

        result = repo.create(
            domain="payment",
            failure_type="PG_TIMEOUT",
            context={"order_id": 123},
            error_message="Connection timed out",
            max_retries=3,
        )

        assert result.id is not None
        assert result.domain == "payment"
        assert result.failure_type == "PG_TIMEOUT"
        assert result.status == "pending"
        assert result.max_retries == 3

    def test_get_by_id(self):
        """Test getting operation by ID."""
        from selfhealing.adapters.django.repositories import DjangoFailedOperationRepository
        from selfhealing.adapters.django.models import FailedOperation

        repo = DjangoFailedOperationRepository()

        # Create via model
        op = FailedOperation.objects.create(
            domain="payment",
            failure_type="PG_TIMEOUT",
        )

        # Get via repository
        result = repo.get_by_id(op.id)

        assert result is not None
        assert result.id == op.id
        assert result.domain == "payment"

    def test_get_by_id_not_found(self):
        """Test getting non-existent operation."""
        from selfhealing.adapters.django.repositories import DjangoFailedOperationRepository

        repo = DjangoFailedOperationRepository()
        result = repo.get_by_id(99999)

        assert result is None

    def test_get_pending(self):
        """Test getting pending operations."""
        from selfhealing.adapters.django.repositories import DjangoFailedOperationRepository
        from selfhealing.adapters.django.models import FailedOperation

        repo = DjangoFailedOperationRepository()

        # Create some operations
        FailedOperation.objects.create(domain="payment", failure_type="A", status="pending")
        FailedOperation.objects.create(domain="payment", failure_type="B", status="pending")
        FailedOperation.objects.create(domain="payment", failure_type="C", status="resolved")
        FailedOperation.objects.create(domain="inventory", failure_type="D", status="pending")

        # Get pending for payment
        result = repo.get_pending(domain="payment", limit=10)

        assert len(result) == 2
        for op in result:
            assert op.domain == "payment"
            assert op.status == "pending"

    def test_mark_completed(self):
        """Test marking operation as completed."""
        from selfhealing.adapters.django.repositories import DjangoFailedOperationRepository
        from selfhealing.adapters.django.models import FailedOperation

        repo = DjangoFailedOperationRepository()

        op = FailedOperation.objects.create(
            domain="payment",
            failure_type="PG_TIMEOUT",
        )

        result = repo.mark_completed(op.id)

        assert result is not None
        assert result.status == "completed"
        assert result.resolved_at is not None

    def test_count_by_status(self):
        """Test counting operations by status."""
        from selfhealing.adapters.django.repositories import DjangoFailedOperationRepository
        from selfhealing.adapters.django.models import FailedOperation
        from selfhealing.core.types import OperationStatus

        repo = DjangoFailedOperationRepository()

        # Create operations
        FailedOperation.objects.create(domain="payment", failure_type="A", status="pending")
        FailedOperation.objects.create(domain="payment", failure_type="B", status="pending")
        FailedOperation.objects.create(domain="payment", failure_type="C", status="completed")

        count = repo.count_by_status(status=OperationStatus.PENDING)

        assert count == 2


class TestDjangoCircuitBreakerStateRepository:
    """Tests for DjangoCircuitBreakerStateRepository."""

    def test_get_or_create(self):
        """Test get or create circuit breaker state."""
        from selfhealing.adapters.django.repositories import DjangoCircuitBreakerStateRepository

        repo = DjangoCircuitBreakerStateRepository()

        # First call creates
        result1 = repo.get_or_create("payment")
        assert result1.service_name == "payment"
        assert result1.state == "closed"

        # Second call gets existing
        result2 = repo.get_or_create("payment")
        assert result2.service_name == "payment"

    def test_record_failure(self):
        """Test recording failures."""
        from selfhealing.adapters.django.repositories import DjangoCircuitBreakerStateRepository

        repo = DjangoCircuitBreakerStateRepository()

        result = repo.record_failure("payment")
        assert result.failure_count == 1
        assert result.last_failure_at is not None

    def test_record_success(self):
        """Test recording successes."""
        from selfhealing.adapters.django.repositories import DjangoCircuitBreakerStateRepository

        repo = DjangoCircuitBreakerStateRepository()

        # Create initial state
        repo.get_or_create("payment")

        result = repo.record_success("payment")
        assert result.last_success_at is not None

    def test_reset(self):
        """Test resetting circuit breaker."""
        from selfhealing.adapters.django.repositories import DjangoCircuitBreakerStateRepository
        from selfhealing.adapters.django.models import CircuitBreakerState

        repo = DjangoCircuitBreakerStateRepository()

        # Create and open circuit
        CircuitBreakerState.objects.create(
            service_name="payment",
            state="open",
            failure_count=10,
            manually_controlled=True,
        )

        result = repo.reset("payment")

        assert result is not None
        assert result.state == "closed"
        assert result.failure_count == 0

    def test_list_all(self):
        """Test listing all circuit breakers."""
        from selfhealing.adapters.django.repositories import DjangoCircuitBreakerStateRepository
        from selfhealing.adapters.django.models import CircuitBreakerState

        repo = DjangoCircuitBreakerStateRepository()

        CircuitBreakerState.objects.create(service_name="payment", state="closed")
        CircuitBreakerState.objects.create(service_name="inventory", state="open")

        result = repo.list_all()

        assert len(result) == 2

    def test_list_open(self):
        """Test listing open circuit breakers."""
        from selfhealing.adapters.django.repositories import DjangoCircuitBreakerStateRepository
        from selfhealing.adapters.django.models import CircuitBreakerState

        repo = DjangoCircuitBreakerStateRepository()

        CircuitBreakerState.objects.create(service_name="payment", state="closed")
        CircuitBreakerState.objects.create(service_name="inventory", state="open")

        result = repo.list_open()

        assert len(result) == 1
        assert result[0].service_name == "inventory"


class TestDjangoSecurityIncidentRepository:
    """Tests for DjangoSecurityIncidentRepository."""

    def test_create(self):
        """Test creating security incident."""
        from selfhealing.adapters.django.repositories import DjangoSecurityIncidentRepository

        repo = DjangoSecurityIncidentRepository()

        result = repo.create(
            incident_type="webhook_signature_invalid",
            severity="high",
            description="Invalid signature",
            source_ip="192.168.1.100",
            user_id=123,
        )

        assert result.id is not None
        assert result.incident_type == "webhook_signature_invalid"
        assert result.severity == "high"
        assert result.source_ip == "192.168.1.100"

    def test_get_recent(self):
        """Test getting recent incidents."""
        from selfhealing.adapters.django.repositories import DjangoSecurityIncidentRepository
        from selfhealing.adapters.django.models import SecurityIncident

        repo = DjangoSecurityIncidentRepository()

        # Create incidents
        SecurityIncident.objects.create(
            incident_type="suspicious_activity",
            severity="medium",
            description="Test 1",
        )
        SecurityIncident.objects.create(
            incident_type="suspicious_activity",
            severity="high",
            description="Test 2",
        )

        result = repo.get_recent(hours=24, severity="high")

        assert len(result) == 1
        assert result[0].severity == "high"

    def test_resolve(self):
        """Test resolving incident."""
        from selfhealing.adapters.django.repositories import DjangoSecurityIncidentRepository
        from selfhealing.adapters.django.models import SecurityIncident

        repo = DjangoSecurityIncidentRepository()

        incident = SecurityIncident.objects.create(
            incident_type="suspicious_activity",
            severity="medium",
            description="Test",
        )

        result = repo.resolve(incident.id)

        assert result is not None
        assert result.is_resolved is True

    def test_count_by_type(self):
        """Test counting incidents by type."""
        from selfhealing.adapters.django.repositories import DjangoSecurityIncidentRepository
        from selfhealing.adapters.django.models import SecurityIncident

        repo = DjangoSecurityIncidentRepository()

        SecurityIncident.objects.create(
            incident_type="suspicious_activity",
            severity="medium",
            description="Test 1",
        )
        SecurityIncident.objects.create(
            incident_type="suspicious_activity",
            severity="high",
            description="Test 2",
        )
        SecurityIncident.objects.create(
            incident_type="rate_limit_abuse",
            severity="low",
            description="Test 3",
        )

        result = repo.count_by_type(hours=24)

        assert result["suspicious_activity"] == 2
        assert result["rate_limit_abuse"] == 1
