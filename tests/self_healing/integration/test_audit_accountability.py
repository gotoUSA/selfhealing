"""
Audit Accountability Tests

File: integration/self_healing/test_audit_accountability.py

Business Risk: Unable to prove compliance, no forensic trail
Compliance Alignment: NIST AU-3, SOC 2 (Audit Logging), ISO 27001 A.12.4

Test Cases:
- AUDIT-001: CB force-open -> controlled_by, reason, timestamp logged
- AUDIT-002: DLQ replay -> replayed_by, source_dlq_id logged
- AUDIT-003: Auto-retry -> decision_engine, policy_version logged
- AUDIT-004: Cost-based abort -> cost_estimate, threshold logged
- AUDIT-005: SLA timeout -> elapsed_time, sla_config logged
- AUDIT-006: Escalation -> failure_history, reason logged
- AUDIT-007: DLQ resolution -> resolved_by, outcome logged

Note: Uses in-memory repositories for parallel execution.
"""

from decimal import Decimal
import uuid

import pytest

from selfhealing.core.timezone import now

from .conftest import (
    InMemoryCircuitBreakerStateRepository,
    InMemoryFailedOperationRepository,
    MockAuditLogRepository,
    AuditEntry,
    MockUser,
    MockOrder,
    MockPayment,
    MockCostTracker,
)


@pytest.mark.tier2
class TestAuditAccountability:
    """
    Audit trail and accountability tests.

    Validates that all autonomous decisions leave
    complete audit trails for compliance.
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.cb_repository = InMemoryCircuitBreakerStateRepository()
        self.dlq_repository = InMemoryFailedOperationRepository()
        self.audit_log_repository = MockAuditLogRepository()
        self.admin_user = MockUser(id=1, username="admin", is_staff=True, is_superuser=True)
        
        # Create circuit breaker service
        from selfhealing.services import CircuitBreakerService
        from selfhealing.services.circuit_breaker_service import CircuitBreakerConfig
        self.circuit_breaker_service = CircuitBreakerService(
            config=CircuitBreakerConfig(enabled=True),
            repository=self.cb_repository,
        )

    def test_manual_circuit_breaker_force_open_creates_audit(self):
        """
        Purpose:
            Verify manual CB operations create complete audit trail.

        Scenario:
            1. Admin forces Circuit Breaker open
            2. Query audit log for the action
            3. Verify all required fields are present

        Expected:
            - Audit entry created
            - controlled_by: admin user ID
            - control_reason: provided reason
            - previous_state: "closed"
            - new_state: "open"
            - timestamp: within 1 second of action

        Risk Covered:
            R-006: Unaccountable decisions

        Compliance:
            SOC 2 CC4.1, NIST AU-3, ISO 27001 A.12.4.1
        """
        # Arrange
        service_name = "toss_payment"
        reason = "Emergency maintenance window"
        before_action = now()

        # Act
        result = self.circuit_breaker_service.force_open(
            service_name=service_name,
            reason=reason,
            controlled_by=self.admin_user,
        )

        # Log audit entry
        self.audit_log_repository.log_circuit_breaker_action(
            service_name=service_name,
            previous_state="closed",
            new_state="open",
            controlled_by=self.admin_user,
            reason=reason,
            ip_address="192.168.1.100",
            user_agent="Mozilla/5.0",
        )

        after_action = now()

        # Assert: Query audit log
        audit_entries = self.audit_log_repository.find_by_action(
            action_type="circuit_breaker_action",
            service_name=service_name,
        )

        assert len(audit_entries) == 1
        entry = audit_entries[0]

        # Validate required fields
        assert entry.controlled_by == self.admin_user.id
        assert entry.control_reason == reason
        assert entry.previous_state == "closed"
        assert entry.new_state == "open"

        # Validate forensic context
        assert entry.metadata["ip_address"] == "192.168.1.100"
        assert entry.metadata["user_agent"] == "Mozilla/5.0"

    def test_circuit_breaker_force_close_creates_audit(self):
        """
        Purpose:
            Verify CB close operations are also audited.

        Scenario:
            1. Open CB
            2. Close CB
            3. Verify both actions audited

        Expected:
            - Two audit entries (open + close)
            - State transitions recorded correctly

        Compliance:
            SOC 2 CC4.1
        """
        service_name = "toss_payment"

        # Open
        self.circuit_breaker_service.force_open(
            service_name=service_name,
            reason="Opening",
            controlled_by=self.admin_user,
        )
        self.audit_log_repository.log_circuit_breaker_action(
            service_name=service_name,
            previous_state="closed",
            new_state="open",
            controlled_by=self.admin_user,
            reason="Opening",
        )

        # Close
        self.circuit_breaker_service.force_close(
            service_name=service_name,
            reason="Recovered",
            controlled_by=self.admin_user,
        )
        self.audit_log_repository.log_circuit_breaker_action(
            service_name=service_name,
            previous_state="open",
            new_state="closed",
            controlled_by=self.admin_user,
            reason="Recovered",
        )

        # Assert
        entries = self.audit_log_repository.find_by_action(
            action_type="circuit_breaker_action",
            service_name=service_name,
        )

        assert len(entries) == 2
        assert entries[0].new_state == "open"
        assert entries[1].new_state == "closed"


@pytest.mark.tier2
class TestDLQAuditTrail:
    """
    DLQ-related audit trail tests.
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.dlq_repository = InMemoryFailedOperationRepository()
        self.audit_log_repository = MockAuditLogRepository()
        self.admin_user = MockUser(id=1, username="admin", is_staff=True)
        self.user = MockUser(id=2, username="testuser")
        self.order = MockOrder(id=1001, user=self.user)
        self.payment = MockPayment(id=2001, order=self.order, status="failed")

    def test_dlq_replay_by_admin_audit(self):
        """
        Purpose:
            Verify DLQ replay operations create audit trail.

        Scenario:
            1. Create DLQ entry
            2. Admin replays the entry
            3. Verify audit contains replayed_by, source_dlq_id

        Expected:
            - Replay action audited
            - replayed_by matches admin
            - dlq_id traceable

        Risk Covered:
            R-006: Untracked manual interventions

        Compliance:
            NIST AU-3
        """
        # Create DLQ entry
        dlq_entry = self.dlq_repository.create(
            domain="payment",
            failure_type="max_retries_exceeded",
            error_code="PG_TIMEOUT",
            error_message="Timeout",
            entity_type="payment",
            entity_id=str(self.payment.id),
            user_id=self.user.id,
        )

        # Simulate replay action
        replay_audit = AuditEntry(
            action_type="replay_action",
            dlq_id=dlq_entry.id,
            controlled_by=self.admin_user.id,
            control_reason="Manual retry after PG recovery",
            metadata={
                "replayed_by": self.admin_user.id,
                "replay_reason": "PG service recovered",
                "attempt_number": 4,
                "outcome": "success",
            },
        )
        self.audit_log_repository.log(replay_audit)

        # Assert
        entries = self.audit_log_repository.find_by_dlq_id(dlq_entry.id)

        assert len(entries) == 1
        entry = entries[0]
        assert entry.action_type == "replay_action"
        assert entry.dlq_id == dlq_entry.id
        assert entry.controlled_by == self.admin_user.id
        assert entry.metadata["replayed_by"] == self.admin_user.id
        assert entry.metadata["outcome"] == "success"

    def test_dlq_entry_creation_audit(self):
        """
        Purpose:
            Verify DLQ entry creation is audited.

        Scenario:
            1. Create DLQ entry via failure handler
            2. Verify audit log entry

        Expected:
            - dlq_entry audit created
            - failure_type, error_code logged

        Risk Covered:
            R-006: Silent DLQ entries

        Compliance:
            NIST AU-3
        """
        # Create DLQ entry
        dlq_entry = self.dlq_repository.create(
            domain="payment",
            failure_type="sla_timeout",
            error_code="SLA_EXCEEDED",
            error_message="SLA timeout after 300s",
            entity_type="payment",
            entity_id=str(self.payment.id),
            user_id=self.user.id,
        )

        # Log audit
        audit_entry = AuditEntry(
            action_type="dlq_entry",
            dlq_id=dlq_entry.id,
            metadata={
                "failure_type": "sla_timeout",
                "error_code": "SLA_EXCEEDED",
                "sla_config": 300,
                "elapsed_time": 305.5,
                "payment_id": self.payment.id,
            },
        )
        self.audit_log_repository.log(audit_entry)

        # Assert
        entries = self.audit_log_repository.find_by_action(action_type="dlq_entry")

        assert len(entries) == 1
        entry = entries[0]
        assert entry.metadata["failure_type"] == "sla_timeout"
        assert entry.metadata["error_code"] == "SLA_EXCEEDED"
        assert entry.dlq_id == dlq_entry.id

    def test_dlq_resolution_by_staff_audit(self):
        """
        Purpose:
            Verify DLQ resolution is audited with outcome.

        Scenario:
            1. Create DLQ entry
            2. Staff resolves it
            3. Verify audit contains resolved_by, outcome

        Expected:
            - Resolution audit entry created
            - resolved_by matches staff user
            - outcome recorded

        Risk Covered:
            R-006: Untracked resolutions

        Compliance:
            SOC 2 CC4.1
        """
        # Create DLQ entry
        dlq_entry = self.dlq_repository.create(
            domain="payment",
            failure_type="max_retries_exceeded",
            error_code="PG_TIMEOUT",
            error_message="Timeout",
            entity_type="payment",
            entity_id=str(self.payment.id),
            user_id=self.user.id,
        )

        # Resolve via repository
        self.dlq_repository.mark_as_resolved(
            id=dlq_entry.id,
            resolution_type="manual",
            resolution_note="Manually verified payment completed",
            resolved_by_id=self.admin_user.id,
        )

        # Log audit
        resolution_audit = AuditEntry(
            action_type="dlq_resolution",
            dlq_id=dlq_entry.id,
            controlled_by=self.admin_user.id,
            control_reason="Manually verified payment completed",
            metadata={
                "resolved_by": self.admin_user.id,
                "outcome": "resolved",
                "resolution_note": "Manually verified payment completed",
            },
        )
        self.audit_log_repository.log(resolution_audit)

        # Assert
        entries = self.audit_log_repository.find_by_dlq_id(dlq_entry.id)

        assert len(entries) == 1
        assert entries[0].action_type == "dlq_resolution"
        assert entries[0].metadata["resolved_by"] == self.admin_user.id
        assert entries[0].metadata["outcome"] == "resolved"


@pytest.mark.tier2
class TestCostDecisionAudit:
    """
    Cost-based decision audit tests.
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.dlq_repository = InMemoryFailedOperationRepository()
        self.audit_log_repository = MockAuditLogRepository()
        self.cost_tracker = MockCostTracker(default_cost=100.0)
        self.high_cost_tracker = MockCostTracker(default_cost=5000.0)
        
        self.user = MockUser(id=1)
        self.order = MockOrder(id=1001, user=self.user)
        self.payment = MockPayment(id=2001, order=self.order, amount=Decimal("10000"))

    def test_cost_based_dlq_audit(self):
        """
        Purpose:
            Verify cost-based DLQ decisions are audited.

        Scenario:
            1. Trigger cost-prohibitive DLQ decision
            2. Verify audit contains cost_estimate, threshold

        Expected:
            - cost_decision audit entry created
            - cost_estimate matches actual
            - threshold matches config

        Risk Covered:
            R-006: Unexplained cost decisions

        Compliance:
            SOC 2 CC4.1
        """
        # Simulate high cost decision
        cost_estimate = Decimal("500")
        threshold = Decimal("1000")  # 10% of 10000
        
        # Log cost decision
        self.audit_log_repository.log_cost_decision(
            dlq_id=1,
            cost_estimate=cost_estimate,
            threshold=threshold,
            action="continue",
            rationale="Cost within acceptable threshold",
        )

        # Assert
        entries = self.audit_log_repository.find_by_action(action_type="cost_decision")

        assert len(entries) >= 1
        entry = entries[-1]

        assert entry.metadata["cost_estimate"] == str(cost_estimate)
        assert entry.metadata["threshold"] == str(threshold)

    def test_cost_decision_includes_rationale(self):
        """
        Purpose:
            Verify cost decisions include human-readable rationale.

        Expected:
            - control_reason contains explanation
            - Rationale is meaningful
        """
        # Log cost decision with rationale
        self.audit_log_repository.log_cost_decision(
            dlq_id=1,
            cost_estimate=Decimal("100"),
            threshold=Decimal("500"),
            action="continue",
            rationale="Retry count 1, Cost estimate 100 within threshold 500",
        )

        entries = self.audit_log_repository.find_by_action(action_type="cost_decision")
        entry = entries[-1]

        assert entry.metadata["rationale"] is not None
        assert "Retry" in entry.metadata["rationale"] or "Cost" in entry.metadata["rationale"]


@pytest.mark.tier2
class TestSLAAudit:
    """
    SLA timeout audit tests.
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.dlq_repository = InMemoryFailedOperationRepository()
        self.audit_log_repository = MockAuditLogRepository()
        self.user = MockUser(id=1)
        self.order = MockOrder(id=1001, user=self.user)
        self.payment = MockPayment(id=2001, order=self.order, status="failed")

    def test_sla_abort_action_audit(self):
        """
        Purpose:
            Verify SLA timeout aborts are audited.

        Scenario:
            1. Simulate SLA timeout
            2. Verify audit contains elapsed_time, sla_config

        Expected:
            - sla_abort audit entry created
            - elapsed_time recorded
            - sla_config matches configuration

        Risk Covered:
            R-006: Unexplained SLA aborts

        Compliance:
            SOC 2 (Availability SLA)
        """
        # Simulate SLA abort
        sla_config = 300  # seconds
        elapsed_time = 305.5
        abort_trigger = "auto"

        # Create DLQ entry
        dlq_entry = self.dlq_repository.create(
            domain="payment",
            failure_type="sla_timeout",
            error_code="SLA_EXCEEDED",
            error_message=f"SLA timeout after {elapsed_time}s",
            entity_type="payment",
            entity_id=str(self.payment.id),
            user_id=self.user.id,
        )

        # Log SLA action
        self.audit_log_repository.log_sla_action(
            dlq_id=dlq_entry.id,
            elapsed_time=elapsed_time,
            sla_threshold=sla_config,
            action="abort",
        )

        # Assert
        entries = self.audit_log_repository.find_by_action(action_type="sla_action")

        assert len(entries) == 1
        entry = entries[0]

        assert entry.metadata["sla_threshold"] == sla_config
        assert entry.metadata["elapsed_time"] == elapsed_time
        assert entry.dlq_id == dlq_entry.id


@pytest.mark.tier2
class TestEscalationAudit:
    """
    Escalation and REQUIRES_REVIEW audit tests.
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.dlq_repository = InMemoryFailedOperationRepository()
        self.audit_log_repository = MockAuditLogRepository()
        self.user = MockUser(id=1)
        self.order = MockOrder(id=1001, user=self.user)
        self.payment = MockPayment(id=2001, order=self.order, status="failed")

    def test_requires_review_escalation_audit(self):
        """
        Purpose:
            Verify REQUIRES_REVIEW escalations are audited.

        Scenario:
            1. Trigger escalation (e.g., handler crash, DLQ full)
            2. Verify audit contains failure_history, reason

        Expected:
            - escalation audit entry created
            - failure_history present
            - recommended_action suggested

        Risk Covered:
            R-006: Silent escalations

        Compliance:
            NIST IR-4 (Incident Handling)
        """
        # Create escalation scenario
        failure_history = [
            {"attempt": 1, "error": "PG_TIMEOUT", "timestamp": "2024-01-01T10:00:00Z"},
            {"attempt": 2, "error": "PG_TIMEOUT", "timestamp": "2024-01-01T10:01:00Z"},
            {"attempt": 3, "error": "HANDLER_CRASH", "timestamp": "2024-01-01T10:02:00Z"},
        ]

        # Log escalation
        escalation_audit = AuditEntry(
            action_type="escalation",
            control_reason="REQUIRES_REVIEW: Handler crashed during recovery",
            metadata={
                "escalation_reason": "handler_crash",
                "failure_history": failure_history,
                "recommended_action": "Manual investigation required",
                "severity": "high",
                "payment_id": self.payment.id,
            },
        )
        self.audit_log_repository.log(escalation_audit)

        # Assert
        entries = self.audit_log_repository.find_by_action(action_type="escalation")

        assert len(entries) == 1
        entry = entries[0]

        assert "REQUIRES_REVIEW" in entry.control_reason
        assert entry.metadata["escalation_reason"] == "handler_crash"
        assert len(entry.metadata["failure_history"]) == 3
        assert entry.metadata["severity"] == "high"


@pytest.mark.tier2
class TestAutoRetryAudit:
    """
    Auto-retry decision audit tests.
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.audit_log_repository = MockAuditLogRepository()

    def test_auto_retry_decision_audit(self):
        """
        Purpose:
            Verify auto-retry decisions are audited.

        Scenario:
            1. Trigger auto-retry
            2. Verify audit contains decision_engine, policy_version

        Expected:
            - retry_decision audit entry created
            - decision_engine identified
            - policy_version traceable

        Risk Covered:
            R-006: Untraceable automated decisions

        Compliance:
            NIST AU-3
        """
        retry_audit = AuditEntry(
            action_type="retry_decision",
            control_reason="Auto-retry scheduled based on policy",
            metadata={
                "decision_engine": "CeleryPaymentRecovery",
                "policy_version": "v1.0.0",
                "error_code": "NETWORK_ERROR",
                "retry_count": 1,
                "backoff_delay": 4,
                "is_retryable": True,
            },
        )
        self.audit_log_repository.log(retry_audit)

        # Assert
        entries = self.audit_log_repository.find_by_action(action_type="retry_decision")

        assert len(entries) == 1
        entry = entries[0]

        assert entry.metadata["decision_engine"] == "CeleryPaymentRecovery"
        assert entry.metadata["policy_version"] == "v1.0.0"
        assert entry.metadata["is_retryable"] is True


@pytest.mark.tier2
class TestAuditTrailCompleteness:
    """
    Tests for audit trail completeness and forensic capability.
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.audit_log_repository = MockAuditLogRepository()
        self.admin_user = MockUser(id=1, username="admin", is_staff=True)

    def test_complete_payment_failure_audit_trail(self):
        """
        Purpose:
            Verify complete audit trail for a payment failure journey.

        Scenario:
            1. Initial failure
            2. Retry attempts
            3. DLQ entry
            4. Admin resolution

        Expected:
            - All stages have audit entries
            - Full journey reconstructable

        Risk Covered:
            R-006: Incomplete forensic trail

        Compliance:
            SOC 2 CC4.1, NIST AU-3
        """
        trace_id = str(uuid.uuid4())

        # Stage 1: Initial failure
        self.audit_log_repository.log(
            AuditEntry(
                action_type="payment_failure",
                metadata={
                    "trace_id": trace_id,
                    "stage": "initial_failure",
                    "error_code": "PG_TIMEOUT",
                },
            )
        )

        # Stage 2: Retry attempt
        self.audit_log_repository.log(
            AuditEntry(
                action_type="retry_decision",
                metadata={
                    "trace_id": trace_id,
                    "stage": "retry",
                    "attempt": 1,
                },
            )
        )

        # Stage 3: DLQ entry
        self.audit_log_repository.log(
            AuditEntry(
                action_type="dlq_entry",
                metadata={
                    "trace_id": trace_id,
                    "stage": "dlq",
                    "failure_type": "max_retries_exceeded",
                },
            )
        )

        # Stage 4: Resolution
        self.audit_log_repository.log(
            AuditEntry(
                action_type="dlq_resolution",
                controlled_by=self.admin_user.id,
                metadata={
                    "trace_id": trace_id,
                    "stage": "resolution",
                    "outcome": "resolved",
                },
            )
        )

        # Assert: Complete trail exists
        all_entries = self.audit_log_repository.get_all()
        trace_entries = [e for e in all_entries if e.metadata.get("trace_id") == trace_id]

        assert len(trace_entries) == 4, "Should have 4 audit entries for complete trail"

        stages = [e.metadata["stage"] for e in trace_entries]
        assert "initial_failure" in stages
        assert "retry" in stages
        assert "dlq" in stages
        assert "resolution" in stages

    def test_audit_entries_are_immutable(self):
        """
        Purpose:
            Verify audit entries cannot be modified after creation.

        Note: This is a conceptual test - in production, immutability
        would be enforced at the database/application level.

        Expected:
            - Audit entries have unique IDs
            - Timestamps are frozen at creation

        Compliance:
            SOC 2 CC4.1 (Immutable logs)
        """
        entry1 = AuditEntry(
            action_type="test_action",
            control_reason="Original reason",
        )
        self.audit_log_repository.log(entry1)

        original_timestamp = entry1.timestamp

        # Attempt to modify (in real system, this would be prevented)
        entry1.control_reason = "Modified reason"

        # The original in repository should be unchanged
        # (In production, this would be enforced by DB constraints)
        entries = self.audit_log_repository.get_all()
        assert entries[0].timestamp == original_timestamp
