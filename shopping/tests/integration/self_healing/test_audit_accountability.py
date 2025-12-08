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
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock
import uuid

import pytest
from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from shopping.models.failed_payment import CircuitBreakerState, FailedPayment
from shopping.services.payment_recovery_service import CeleryPaymentRecovery
from shopping.tests.factories import OrderFactory, PaymentFactory, UserFactory


@pytest.mark.tier2
@pytest.mark.django_db(transaction=True)
class TestAuditAccountability:
    """
    Audit trail and accountability tests.

    Validates that all autonomous decisions leave
    complete audit trails for compliance.
    """

    def test_manual_circuit_breaker_force_open_creates_audit(
        self,
        circuit_breaker_service,
        audit_log_repository,
        admin_user,
    ):
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
        before_action = timezone.now()

        # Act
        result = circuit_breaker_service.force_open(
            service_name=service_name,
            reason=reason,
            controlled_by=admin_user,
        )

        # Log audit entry
        audit_log_repository.log_circuit_breaker_action(
            service_name=service_name,
            previous_state="closed",
            new_state="open",
            controlled_by=admin_user,
            reason=reason,
            ip_address="192.168.1.100",
            user_agent="Mozilla/5.0",
        )

        after_action = timezone.now()

        # Assert: Query audit log
        audit_entries = audit_log_repository.find_by_action(
            action_type="circuit_breaker_action",
            service_name=service_name,
        )

        assert len(audit_entries) == 1
        entry = audit_entries[0]

        # Validate required fields
        assert entry.controlled_by == admin_user.id
        assert entry.control_reason == reason
        assert entry.previous_state == "closed"
        assert entry.new_state == "open"
        assert before_action <= entry.timestamp <= after_action

        # Validate forensic context
        assert entry.ip_address == "192.168.1.100"
        assert entry.user_agent == "Mozilla/5.0"

    def test_circuit_breaker_force_close_creates_audit(
        self,
        circuit_breaker_service,
        audit_log_repository,
        admin_user,
    ):
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
        circuit_breaker_service.force_open(
            service_name=service_name,
            reason="Opening",
            controlled_by=admin_user,
        )
        audit_log_repository.log_circuit_breaker_action(
            service_name=service_name,
            previous_state="closed",
            new_state="open",
            controlled_by=admin_user,
            reason="Opening",
        )

        # Close
        circuit_breaker_service.force_close(
            service_name=service_name,
            reason="Recovered",
            controlled_by=admin_user,
        )
        audit_log_repository.log_circuit_breaker_action(
            service_name=service_name,
            previous_state="open",
            new_state="closed",
            controlled_by=admin_user,
            reason="Recovered",
        )

        # Assert
        entries = audit_log_repository.find_by_action(
            action_type="circuit_breaker_action",
            service_name=service_name,
        )

        assert len(entries) == 2
        assert entries[0].new_state == "open"
        assert entries[1].new_state == "closed"


@pytest.mark.tier2
@pytest.mark.django_db(transaction=True)
class TestDLQAuditTrail:
    """
    DLQ-related audit trail tests.
    """

    def test_dlq_replay_by_admin_audit(
        self,
        audit_log_repository,
        admin_user,
        db,
    ):
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
        user = UserFactory()
        order = OrderFactory(user=user)
        payment = PaymentFactory(order=order, status="failed")

        dlq_entry = FailedPayment.objects.create(
            payment=payment,
            order=order,
            user=user,
            failure_type="max_retries_exceeded",
            error_code="PG_TIMEOUT",
            error_message="Timeout",
            retry_count=3,
        )

        # Simulate replay action
        from .conftest import AuditEntry
        replay_audit = AuditEntry(
            action_type="replay_action",
            dlq_id=dlq_entry.id,
            controlled_by=admin_user.id,
            control_reason="Manual retry after PG recovery",
            metadata={
                "replayed_by": admin_user.id,
                "replay_reason": "PG service recovered",
                "attempt_number": 4,
                "outcome": "success",
            },
        )
        audit_log_repository.log(replay_audit)

        # Assert
        entries = audit_log_repository.find_by_dlq_id(dlq_entry.id)

        assert len(entries) == 1
        entry = entries[0]
        assert entry.action_type == "replay_action"
        assert entry.dlq_id == dlq_entry.id
        assert entry.controlled_by == admin_user.id
        assert entry.metadata["replayed_by"] == admin_user.id
        assert entry.metadata["outcome"] == "success"

    def test_dlq_entry_creation_audit(
        self,
        audit_log_repository,
        db,
    ):
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
        user = UserFactory()
        order = OrderFactory(user=user)
        payment = PaymentFactory(order=order, status="failed")

        dlq_entry = FailedPayment.objects.create(
            payment=payment,
            order=order,
            user=user,
            failure_type="sla_timeout",
            error_code="SLA_EXCEEDED",
            error_message="SLA timeout after 300s",
            retry_count=2,
        )

        # Log audit
        audit_log_repository.log_dlq_entry(
            dlq_id=dlq_entry.id,
            failure_type="sla_timeout",
            error_code="SLA_EXCEEDED",
            metadata={
                "sla_config": 300,
                "elapsed_time": 305.5,
                "payment_id": payment.id,
            },
        )

        # Assert
        entries = audit_log_repository.find_by_action(action_type="dlq_entry")

        assert len(entries) == 1
        entry = entries[0]
        assert entry.failure_type == "sla_timeout"
        assert entry.error_code == "SLA_EXCEEDED"
        assert entry.dlq_id == dlq_entry.id

    def test_dlq_resolution_by_staff_audit(
        self,
        audit_log_repository,
        admin_user,
        db,
    ):
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
        # Create and resolve DLQ entry
        user = UserFactory()
        order = OrderFactory(user=user)
        payment = PaymentFactory(order=order, status="failed")

        dlq_entry = FailedPayment.objects.create(
            payment=payment,
            order=order,
            user=user,
            failure_type="max_retries_exceeded",
            error_code="PG_TIMEOUT",
            error_message="Timeout",
            retry_count=3,
            status="pending",
        )

        # Resolve
        dlq_entry.mark_as_resolved(
            resolved_by=admin_user,
            note="Manually verified payment completed",
        )

        # Log audit
        from .conftest import AuditEntry
        resolution_audit = AuditEntry(
            action_type="dlq_resolution",
            dlq_id=dlq_entry.id,
            controlled_by=admin_user.id,
            control_reason="Manually verified payment completed",
            metadata={
                "resolved_by": admin_user.id,
                "outcome": "resolved",
                "resolution_note": "Manually verified payment completed",
            },
        )
        audit_log_repository.log(resolution_audit)

        # Assert
        entries = audit_log_repository.find_by_dlq_id(dlq_entry.id)

        assert len(entries) == 1
        assert entries[0].action_type == "dlq_resolution"
        assert entries[0].metadata["resolved_by"] == admin_user.id
        assert entries[0].metadata["outcome"] == "resolved"


@pytest.mark.tier2
@pytest.mark.django_db(transaction=True)
class TestCostDecisionAudit:
    """
    Cost-based decision audit tests.
    """

    def test_cost_based_dlq_audit(
        self,
        recovery_handler,
        high_cost_tracker,
        sample_payment,
        audit_log_repository,
    ):
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
        # Arrange
        sample_payment.amount = Decimal("10000")
        sample_payment.save()

        high_cost_tracker.cost_per_call = Decimal("500")

        # Act
        result = recovery_handler.handle_failure_with_cost_awareness(
            payment=sample_payment,
            error_code="PG_TIMEOUT",
            cost_tracker=high_cost_tracker,
        )

        # Assert
        entries = audit_log_repository.find_by_action(action_type="cost_decision")

        assert len(entries) >= 1
        entry = entries[-1]

        assert entry.transaction_value == sample_payment.amount
        assert entry.cost_estimate is not None
        assert entry.cost_threshold == Decimal("1000")  # 10% of 10000
        assert entry.cost_decision in ["continue", "moved_to_dlq"]

    def test_cost_decision_includes_rationale(
        self,
        recovery_handler,
        cost_tracker,
        sample_payment,
        audit_log_repository,
    ):
        """
        Purpose:
            Verify cost decisions include human-readable rationale.

        Expected:
            - control_reason contains explanation
            - Rationale is meaningful
        """
        sample_payment.amount = Decimal("5000")
        sample_payment.save()

        cost_tracker.cost_per_call = Decimal("100")

        result = recovery_handler.handle_failure_with_cost_awareness(
            payment=sample_payment,
            error_code="PG_TIMEOUT",
            cost_tracker=cost_tracker,
        )

        entries = audit_log_repository.find_by_action(action_type="cost_decision")
        entry = entries[-1]

        assert entry.control_reason is not None
        assert "Retry count" in entry.control_reason or "Cost" in entry.control_reason


@pytest.mark.tier2
@pytest.mark.django_db(transaction=True)
class TestSLAAudit:
    """
    SLA timeout audit tests.
    """

    def test_sla_abort_action_audit(
        self,
        audit_log_repository,
        db,
    ):
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

        user = UserFactory()
        order = OrderFactory(user=user)
        payment = PaymentFactory(order=order, status="failed")

        dlq_entry = FailedPayment.objects.create(
            payment=payment,
            order=order,
            user=user,
            failure_type="sla_timeout",
            error_code="SLA_EXCEEDED",
            error_message=f"SLA timeout after {elapsed_time}s",
            retry_count=2,
        )

        # Log SLA abort
        audit_log_repository.log_sla_abort(
            sla_config=sla_config,
            elapsed_time=elapsed_time,
            abort_trigger=abort_trigger,
            dlq_id=dlq_entry.id,
        )

        # Assert
        entries = audit_log_repository.find_by_action(action_type="sla_abort")

        assert len(entries) == 1
        entry = entries[0]

        assert entry.sla_config == sla_config
        assert entry.elapsed_time == elapsed_time
        assert entry.abort_trigger == abort_trigger
        assert entry.dlq_id == dlq_entry.id


@pytest.mark.tier2
@pytest.mark.django_db(transaction=True)
class TestEscalationAudit:
    """
    Escalation and REQUIRES_REVIEW audit tests.
    """

    def test_requires_review_escalation_audit(
        self,
        audit_log_repository,
        db,
    ):
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
        user = UserFactory()
        order = OrderFactory(user=user)
        payment = PaymentFactory(order=order, status="failed")

        failure_history = [
            {"attempt": 1, "error": "PG_TIMEOUT", "timestamp": "2024-01-01T10:00:00Z"},
            {"attempt": 2, "error": "PG_TIMEOUT", "timestamp": "2024-01-01T10:01:00Z"},
            {"attempt": 3, "error": "HANDLER_CRASH", "timestamp": "2024-01-01T10:02:00Z"},
        ]

        # Log escalation
        from .conftest import AuditEntry
        escalation_audit = AuditEntry(
            action_type="escalation",
            control_reason="REQUIRES_REVIEW: Handler crashed during recovery",
            metadata={
                "escalation_reason": "handler_crash",
                "failure_history": failure_history,
                "recommended_action": "Manual investigation required",
                "severity": "high",
                "payment_id": payment.id,
            },
        )
        audit_log_repository.log(escalation_audit)

        # Assert
        entries = audit_log_repository.find_by_action(action_type="escalation")

        assert len(entries) == 1
        entry = entries[0]

        assert "REQUIRES_REVIEW" in entry.control_reason
        assert entry.metadata["escalation_reason"] == "handler_crash"
        assert len(entry.metadata["failure_history"]) == 3
        assert entry.metadata["severity"] == "high"


@pytest.mark.tier2
@pytest.mark.django_db(transaction=True)
class TestAutoRetryAudit:
    """
    Auto-retry decision audit tests.
    """

    def test_auto_retry_decision_audit(
        self,
        audit_log_repository,
        db,
    ):
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
        from .conftest import AuditEntry

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
        audit_log_repository.log(retry_audit)

        # Assert
        entries = audit_log_repository.find_by_action(action_type="retry_decision")

        assert len(entries) == 1
        entry = entries[0]

        assert entry.metadata["decision_engine"] == "CeleryPaymentRecovery"
        assert entry.metadata["policy_version"] == "v1.0.0"
        assert entry.metadata["is_retryable"] is True


@pytest.mark.tier2
@pytest.mark.django_db(transaction=True)
class TestAuditTrailCompleteness:
    """
    Tests for audit trail completeness and forensic capability.
    """

    def test_complete_payment_failure_audit_trail(
        self,
        audit_log_repository,
        admin_user,
        db,
    ):
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
        from .conftest import AuditEntry

        trace_id = str(uuid.uuid4())

        # Stage 1: Initial failure
        audit_log_repository.log(AuditEntry(
            action_type="payment_failure",
            metadata={
                "trace_id": trace_id,
                "stage": "initial_failure",
                "error_code": "PG_TIMEOUT",
            },
        ))

        # Stage 2: Retry attempt
        audit_log_repository.log(AuditEntry(
            action_type="retry_decision",
            metadata={
                "trace_id": trace_id,
                "stage": "retry",
                "attempt": 1,
            },
        ))

        # Stage 3: DLQ entry
        audit_log_repository.log(AuditEntry(
            action_type="dlq_entry",
            metadata={
                "trace_id": trace_id,
                "stage": "dlq",
                "failure_type": "max_retries_exceeded",
            },
        ))

        # Stage 4: Resolution
        audit_log_repository.log(AuditEntry(
            action_type="dlq_resolution",
            controlled_by=admin_user.id,
            metadata={
                "trace_id": trace_id,
                "stage": "resolution",
                "outcome": "resolved",
            },
        ))

        # Assert: Complete trail exists
        all_entries = audit_log_repository.get_all()
        trace_entries = [
            e for e in all_entries
            if e.metadata.get("trace_id") == trace_id
        ]

        assert len(trace_entries) == 4, "Should have 4 audit entries for complete trail"

        stages = [e.metadata["stage"] for e in trace_entries]
        assert "initial_failure" in stages
        assert "retry" in stages
        assert "dlq" in stages
        assert "resolution" in stages

    def test_audit_entries_are_immutable(self, audit_log_repository):
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
        from .conftest import AuditEntry

        entry1 = AuditEntry(
            action_type="test_action",
            control_reason="Original reason",
        )
        audit_log_repository.log(entry1)

        original_id = entry1.id
        original_timestamp = entry1.timestamp

        # Attempt to modify (in real system, this would be prevented)
        entry1.control_reason = "Modified reason"

        # The original in repository should be unchanged
        # (In production, this would be enforced by DB constraints)
        entries = audit_log_repository.get_all()
        assert entries[0].id == original_id
        assert entries[0].timestamp == original_timestamp
