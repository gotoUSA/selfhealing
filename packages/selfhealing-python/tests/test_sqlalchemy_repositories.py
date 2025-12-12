"""
Unit Tests for SQLAlchemy Repository Implementations

Tests the SQLAlchemy adapters using SQLite in-memory database.
This ensures the implementations work correctly without requiring
a full PostgreSQL setup.
"""

import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from selfhealing.adapters.sqlalchemy.models import (
    Base,
    FailedOperationModel,
    CircuitBreakerStateModel,
    SecurityIncidentModel,
)
from selfhealing.adapters.sqlalchemy import (
    SQLAlchemyFailedOperationRepository,
    SQLAlchemyCircuitBreakerStateRepository,
    SQLAlchemySecurityIncidentRepository,
    create_session_factory,
)
from selfhealing.interfaces.repositories import (
    FailedOperationStatus,
    CircuitBreakerStateEnum,
    SecurityIncidentStatus,
    SecuritySeverity,
    SecurityIncidentType,
)


@pytest.fixture
def engine():
    """Create an in-memory SQLite engine for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def session_factory(engine):
    """Create a session factory for testing."""
    return create_session_factory(engine)


class TestSQLAlchemyFailedOperationRepository:
    """Tests for SQLAlchemyFailedOperationRepository."""

    def test_create_operation(self, session_factory):
        """Test creating a failed operation."""
        repo = SQLAlchemyFailedOperationRepository(session_factory)

        operation = repo.create(
            domain="payment",
            failure_type="timeout",
            error_message="Connection timed out",
            error_code="TIMEOUT_001",
            order_id=123,
            user_id=456,
            snapshot_data={"amount": 10000},
        )

        assert operation.id is not None
        assert operation.domain == "payment"
        assert operation.failure_type == "timeout"
        assert operation.error_message == "Connection timed out"
        assert operation.error_code == "TIMEOUT_001"
        assert operation.order_id == 123
        assert operation.user_id == 456
        assert operation.snapshot_data == {"amount": 10000}
        assert operation.status == FailedOperationStatus.PENDING.value
        assert operation.retry_count == 0

    def test_get_by_id(self, session_factory):
        """Test retrieving a failed operation by ID."""
        repo = SQLAlchemyFailedOperationRepository(session_factory)

        created = repo.create(
            domain="webhook",
            failure_type="signature_invalid",
            error_message="Invalid signature",
        )

        retrieved = repo.get_by_id(created.id)

        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.domain == "webhook"

    def test_get_by_id_not_found(self, session_factory):
        """Test retrieving non-existent operation."""
        repo = SQLAlchemyFailedOperationRepository(session_factory)

        retrieved = repo.get_by_id(99999)

        assert retrieved is None

    def test_get_pending_by_domain(self, session_factory):
        """Test getting pending operations by domain."""
        repo = SQLAlchemyFailedOperationRepository(session_factory)

        # Create operations in different domains
        repo.create(domain="payment", failure_type="timeout", error_message="Timeout")
        repo.create(domain="payment", failure_type="error", error_message="Error")
        repo.create(domain="webhook", failure_type="invalid", error_message="Invalid")

        pending = repo.get_pending_by_domain("payment")

        assert len(pending) == 2
        for op in pending:
            assert op.domain == "payment"
            assert op.status == FailedOperationStatus.PENDING.value

    def test_update_status(self, session_factory):
        """Test updating operation status."""
        repo = SQLAlchemyFailedOperationRepository(session_factory)

        operation = repo.create(
            domain="payment",
            failure_type="timeout",
            error_message="Timeout",
        )

        success = repo.update_status(
            id=operation.id,
            status=FailedOperationStatus.RESOLVED.value,
            resolution_type="auto_retry",
            resolution_note="Resolved automatically",
        )

        assert success is True

        updated = repo.get_by_id(operation.id)
        assert updated.status == FailedOperationStatus.RESOLVED.value
        assert updated.resolution_type == "auto_retry"
        assert updated.resolution_note == "Resolved automatically"
        assert updated.resolved_at is not None

    def test_increment_retry_count(self, session_factory):
        """Test incrementing retry count."""
        repo = SQLAlchemyFailedOperationRepository(session_factory)

        operation = repo.create(
            domain="payment",
            failure_type="timeout",
            error_message="Timeout",
        )

        assert operation.retry_count == 0

        success = repo.increment_retry_count(operation.id)
        assert success is True

        updated = repo.get_by_id(operation.id)
        assert updated.retry_count == 1
        assert updated.last_retry_at is not None

    def test_find_replayable(self, session_factory):
        """Test finding replayable operations."""
        repo = SQLAlchemyFailedOperationRepository(session_factory)

        # Create operations with different retry counts
        op1 = repo.create(domain="payment", failure_type="t1", error_message="E1")
        op2 = repo.create(domain="payment", failure_type="t2", error_message="E2", retry_count=3)

        replayable = repo.find_replayable(max_retries=3, domain="payment")

        assert len(replayable) == 1
        assert replayable[0].id == op1.id

    def test_try_acquire_for_replay(self, session_factory):
        """Test atomic acquire for replay."""
        repo = SQLAlchemyFailedOperationRepository(session_factory)

        operation = repo.create(
            domain="payment",
            failure_type="timeout",
            error_message="Timeout",
        )

        acquired = repo.try_acquire_for_replay(operation.id, max_retries=3)

        assert acquired is not None
        assert acquired.status == "replaying"
        assert acquired.retry_count == 1

        # Second acquire should fail
        second = repo.try_acquire_for_replay(operation.id, max_retries=3)
        assert second is None

    def test_complete_replay_success(self, session_factory):
        """Test completing replay successfully."""
        repo = SQLAlchemyFailedOperationRepository(session_factory)

        operation = repo.create(
            domain="payment",
            failure_type="timeout",
            error_message="Timeout",
        )
        repo.try_acquire_for_replay(operation.id, max_retries=3)

        success = repo.complete_replay(
            id=operation.id,
            success=True,
            resolution_type="auto_retry",
            note="Resolved successfully",
        )

        assert success is True

        updated = repo.get_by_id(operation.id)
        assert updated.status == FailedOperationStatus.RESOLVED.value
        assert updated.resolution_type == "auto_retry"

    def test_complete_replay_failure(self, session_factory):
        """Test completing replay with failure."""
        repo = SQLAlchemyFailedOperationRepository(session_factory)

        operation = repo.create(
            domain="payment",
            failure_type="timeout",
            error_message="Timeout",
            max_retries=3,
        )
        repo.try_acquire_for_replay(operation.id, max_retries=3)

        success = repo.complete_replay(
            id=operation.id,
            success=False,
            note="Still failing",
        )

        assert success is True

        updated = repo.get_by_id(operation.id)
        # Should revert to pending since retry_count < max_retries
        assert updated.status == FailedOperationStatus.PENDING.value

    def test_bulk_update_status(self, session_factory):
        """Test bulk status update."""
        repo = SQLAlchemyFailedOperationRepository(session_factory)

        op1 = repo.create(domain="payment", failure_type="t1", error_message="E1")
        op2 = repo.create(domain="payment", failure_type="t2", error_message="E2")
        op3 = repo.create(domain="webhook", failure_type="t3", error_message="E3")

        count = repo.bulk_update_status(
            ids=[op1.id, op2.id],
            status=FailedOperationStatus.ARCHIVED.value,
        )

        assert count == 2

        updated1 = repo.get_by_id(op1.id)
        updated2 = repo.get_by_id(op2.id)
        updated3 = repo.get_by_id(op3.id)

        assert updated1.status == FailedOperationStatus.ARCHIVED.value
        assert updated2.status == FailedOperationStatus.ARCHIVED.value
        assert updated3.status == FailedOperationStatus.PENDING.value

    def test_get_statistics(self, session_factory):
        """Test getting statistics."""
        repo = SQLAlchemyFailedOperationRepository(session_factory)

        repo.create(domain="payment", failure_type="t1", error_message="E1")
        repo.create(domain="payment", failure_type="t2", error_message="E2")
        op3 = repo.create(domain="webhook", failure_type="t3", error_message="E3")

        repo.mark_as_resolved(op3.id, "manual", "Fixed")

        stats = repo.get_statistics()

        assert stats["total"] == 3
        assert stats["pending"] == 2
        assert stats["resolved"] == 1


class TestSQLAlchemyCircuitBreakerStateRepository:
    """Tests for SQLAlchemyCircuitBreakerStateRepository."""

    def test_get_or_create_new(self, session_factory):
        """Test creating a new circuit breaker state."""
        repo = SQLAlchemyCircuitBreakerStateRepository(session_factory)

        state = repo.get_or_create("test-service")

        assert state.service_name == "test-service"
        assert state.state == CircuitBreakerStateEnum.CLOSED.value
        assert state.failure_count == 0
        assert state.success_count == 0

    def test_get_or_create_existing(self, session_factory):
        """Test getting an existing circuit breaker state."""
        repo = SQLAlchemyCircuitBreakerStateRepository(session_factory)

        # Create first
        state1 = repo.get_or_create("test-service")
        repo.record_failure("test-service")

        # Get again
        state2 = repo.get_or_create("test-service")

        assert state2.service_name == "test-service"
        assert state2.failure_count == 1

    def test_record_failure(self, session_factory):
        """Test recording a failure."""
        repo = SQLAlchemyCircuitBreakerStateRepository(session_factory)

        repo.get_or_create("test-service")

        state = repo.record_failure("test-service")

        assert state.failure_count == 1
        assert state.last_failure_at is not None

        state = repo.record_failure("test-service")
        assert state.failure_count == 2

    def test_record_success(self, session_factory):
        """Test recording a success."""
        repo = SQLAlchemyCircuitBreakerStateRepository(session_factory)

        repo.get_or_create("test-service")

        state = repo.record_success("test-service")

        assert state.success_count == 1
        # Note: last_success_at is stored in DB but not in CircuitBreakerStateData
        # The update should have been successful

    def test_update_state(self, session_factory):
        """Test updating circuit breaker state."""
        repo = SQLAlchemyCircuitBreakerStateRepository(session_factory)

        repo.get_or_create("test-service")

        success = repo.update_state(
            service_name="test-service",
            state=CircuitBreakerStateEnum.OPEN.value,
            failure_count=5,
        )

        assert success is True

        state = repo.get_by_service_name("test-service")
        assert state.state == CircuitBreakerStateEnum.OPEN.value
        assert state.failure_count == 5

    def test_set_manual_control(self, session_factory):
        """Test setting manual control."""
        repo = SQLAlchemyCircuitBreakerStateRepository(session_factory)

        repo.get_or_create("test-service")

        success = repo.set_manual_control(
            service_name="test-service",
            state=CircuitBreakerStateEnum.OPEN.value,
            controlled_by_id=1,
            reason="Maintenance",
        )

        assert success is True

        state = repo.get_by_service_name("test-service")
        assert state.state == CircuitBreakerStateEnum.OPEN.value
        assert state.manually_controlled is True
        assert state.controlled_by_id == 1
        assert state.control_reason == "Maintenance"

    def test_clear_manual_control(self, session_factory):
        """Test clearing manual control."""
        repo = SQLAlchemyCircuitBreakerStateRepository(session_factory)

        repo.get_or_create("test-service")
        repo.set_manual_control("test-service", CircuitBreakerStateEnum.OPEN.value, 1, "Test")

        success = repo.clear_manual_control("test-service")

        assert success is True

        state = repo.get_by_service_name("test-service")
        assert state.manually_controlled is False
        assert state.controlled_by_id is None

    def test_atomic_force_open(self, session_factory):
        """Test atomic force open."""
        repo = SQLAlchemyCircuitBreakerStateRepository(session_factory)

        repo.get_or_create("test-service")

        success, previous, new = repo.atomic_force_open(
            service_name="test-service",
            reason="Emergency",
            controlled_by_id=1,
        )

        assert success is True
        assert previous == CircuitBreakerStateEnum.CLOSED.value
        assert new == CircuitBreakerStateEnum.OPEN.value

        state = repo.get_by_service_name("test-service")
        assert state.state == CircuitBreakerStateEnum.OPEN.value
        assert state.manually_controlled is True

    def test_atomic_force_close(self, session_factory):
        """Test atomic force close."""
        repo = SQLAlchemyCircuitBreakerStateRepository(session_factory)

        repo.get_or_create("test-service")
        repo.atomic_force_open("test-service", "Test")

        success, previous, new = repo.atomic_force_close(
            service_name="test-service",
            reason="Fixed",
        )

        assert success is True
        assert previous == CircuitBreakerStateEnum.OPEN.value
        assert new == CircuitBreakerStateEnum.CLOSED.value

    def test_atomic_reset(self, session_factory):
        """Test atomic reset."""
        repo = SQLAlchemyCircuitBreakerStateRepository(session_factory)

        repo.get_or_create("test-service")
        repo.record_failure("test-service")
        repo.record_failure("test-service")
        repo.atomic_force_open("test-service", "Test")

        success, previous, new = repo.atomic_reset("test-service", "Reset")

        assert success is True
        assert previous == CircuitBreakerStateEnum.OPEN.value
        assert new == CircuitBreakerStateEnum.CLOSED.value

        state = repo.get_by_service_name("test-service")
        assert state.failure_count == 0
        assert state.success_count == 0
        assert state.manually_controlled is False

    def test_get_all(self, session_factory):
        """Test getting all circuit breaker states."""
        repo = SQLAlchemyCircuitBreakerStateRepository(session_factory)

        repo.get_or_create("service-1")
        repo.get_or_create("service-2")
        repo.get_or_create("service-3")

        all_states = repo.get_all()

        assert len(all_states) == 3


class TestSQLAlchemySecurityIncidentRepository:
    """Tests for SQLAlchemySecurityIncidentRepository."""

    def test_create_incident(self, session_factory):
        """Test creating a security incident."""
        repo = SQLAlchemySecurityIncidentRepository(session_factory)

        incident = repo.create(
            incident_type=SecurityIncidentType.WEBHOOK_SIGNATURE_INVALID.value,
            severity=SecuritySeverity.HIGH.value,
            description="Invalid signature detected",
            source_ip="192.168.1.100",
            user_id=123,
        )

        assert incident.id is not None
        assert incident.incident_type == SecurityIncidentType.WEBHOOK_SIGNATURE_INVALID.value
        assert incident.severity == SecuritySeverity.HIGH.value
        assert incident.status == SecurityIncidentStatus.OPEN.value
        assert incident.source_ip == "192.168.1.100"

    def test_get_by_id(self, session_factory):
        """Test retrieving an incident by ID."""
        repo = SQLAlchemySecurityIncidentRepository(session_factory)

        created = repo.create(
            incident_type=SecurityIncidentType.RATE_LIMIT_ABUSE.value,
            severity=SecuritySeverity.MEDIUM.value,
            description="Rate limit exceeded",
        )

        retrieved = repo.get_by_id(created.id)

        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.incident_type == SecurityIncidentType.RATE_LIMIT_ABUSE.value

    def test_get_open_incidents(self, session_factory):
        """Test getting open incidents."""
        repo = SQLAlchemySecurityIncidentRepository(session_factory)

        repo.create(
            incident_type=SecurityIncidentType.REPLAY_ATTACK.value,
            severity=SecuritySeverity.CRITICAL.value,
        )
        inc2 = repo.create(
            incident_type=SecurityIncidentType.TOKEN_FORGED.value,
            severity=SecuritySeverity.HIGH.value,
        )
        repo.mark_as_resolved(inc2.id, "False positive")

        open_incidents = repo.get_open_incidents()

        assert len(open_incidents) == 1
        assert open_incidents[0].incident_type == SecurityIncidentType.REPLAY_ATTACK.value

    def test_get_by_severity(self, session_factory):
        """Test getting incidents by severity."""
        repo = SQLAlchemySecurityIncidentRepository(session_factory)

        repo.create(
            incident_type=SecurityIncidentType.REPLAY_ATTACK.value,
            severity=SecuritySeverity.CRITICAL.value,
        )
        repo.create(
            incident_type=SecurityIncidentType.RATE_LIMIT_ABUSE.value,
            severity=SecuritySeverity.MEDIUM.value,
        )
        repo.create(
            incident_type=SecurityIncidentType.TOKEN_FORGED.value,
            severity=SecuritySeverity.CRITICAL.value,
        )

        critical = repo.get_by_severity(SecuritySeverity.CRITICAL.value)

        assert len(critical) == 2
        for inc in critical:
            assert inc.severity == SecuritySeverity.CRITICAL.value

    def test_update_status(self, session_factory):
        """Test updating incident status."""
        repo = SQLAlchemySecurityIncidentRepository(session_factory)

        incident = repo.create(
            incident_type=SecurityIncidentType.UNAUTHORIZED_ACCESS.value,
            severity=SecuritySeverity.HIGH.value,
        )

        success = repo.update_status(
            id=incident.id,
            status=SecurityIncidentStatus.INVESTIGATING.value,
            investigation_notes="Under investigation",
            assigned_to_id=1,
        )

        assert success is True

        updated = repo.get_by_id(incident.id)
        assert updated.status == SecurityIncidentStatus.INVESTIGATING.value
        assert updated.investigation_notes == "Under investigation"
        assert updated.assigned_to_id == 1

    def test_mark_as_resolved(self, session_factory):
        """Test marking incident as resolved."""
        repo = SQLAlchemySecurityIncidentRepository(session_factory)

        incident = repo.create(
            incident_type=SecurityIncidentType.SUSPICIOUS_ACTIVITY.value,
            severity=SecuritySeverity.MEDIUM.value,
        )

        success = repo.mark_as_resolved(incident.id, "Verified as legitimate")

        assert success is True

        updated = repo.get_by_id(incident.id)
        assert updated.status == SecurityIncidentStatus.RESOLVED.value
        assert updated.investigation_notes == "Verified as legitimate"
        assert updated.resolved_at is not None

    def test_get_recent_by_ip(self, session_factory):
        """Test getting recent incidents by IP."""
        repo = SQLAlchemySecurityIncidentRepository(session_factory)

        repo.create(
            incident_type=SecurityIncidentType.RATE_LIMIT_ABUSE.value,
            severity=SecuritySeverity.MEDIUM.value,
            source_ip="192.168.1.100",
        )
        repo.create(
            incident_type=SecurityIncidentType.SUSPICIOUS_ACTIVITY.value,
            severity=SecuritySeverity.MEDIUM.value,
            source_ip="192.168.1.100",
        )
        repo.create(
            incident_type=SecurityIncidentType.TOKEN_FORGED.value,
            severity=SecuritySeverity.HIGH.value,
            source_ip="192.168.1.200",
        )

        incidents = repo.get_recent_by_ip("192.168.1.100", hours=24)

        assert len(incidents) == 2
        for inc in incidents:
            assert inc.source_ip == "192.168.1.100"

    def test_count_by_type_since(self, session_factory):
        """Test counting incidents by type since a time."""
        repo = SQLAlchemySecurityIncidentRepository(session_factory)

        repo.create(
            incident_type=SecurityIncidentType.RATE_LIMIT_ABUSE.value,
            severity=SecuritySeverity.MEDIUM.value,
        )
        repo.create(
            incident_type=SecurityIncidentType.RATE_LIMIT_ABUSE.value,
            severity=SecuritySeverity.MEDIUM.value,
        )
        repo.create(
            incident_type=SecurityIncidentType.TOKEN_FORGED.value,
            severity=SecuritySeverity.HIGH.value,
        )

        since = datetime.now(timezone.utc) - timedelta(hours=1)
        count = repo.count_by_type_since(
            SecurityIncidentType.RATE_LIMIT_ABUSE.value,
            since,
        )

        assert count == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
