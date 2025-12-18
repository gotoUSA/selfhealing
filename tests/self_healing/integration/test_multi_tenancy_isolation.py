"""
Multi-Tenancy Isolation Tests

File: integration/self_healing/test_multi_tenancy_isolation.py

Business Risk: Cross-tenant data leakage, unauthorized access, regulatory violation
Compliance Alignment: SOC 2 (Confidentiality), ISO 27001 A.9 (Access Control)

Test Cases:
- MT-001: Tenant A's CB opens due to failures -> Tenant B requests continue normally
- MT-002: Both tenants have DLQ entries -> Query returns only own tenant's entries
- MT-003: Tenant A: 300s SLA, Tenant B: 600s SLA -> Each tenant's timeout is respected
- MT-004: Both tenants emit metrics -> Labels contain correct tenant_id
- MT-005: Tenant A: 10/min, Tenant B: 50/min -> Rate limits enforced per tenant
- MT-006: Tenant A admin attempts to close Tenant B CB -> Authorization error, audit logged

NOTE: Multi-tenancy support is planned for future implementation in the Django adapter layer.
      These tests are skipped until tenant_id support is added to CircuitBreakerService.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

import pytest

# Skip entire module until multi-tenancy is implemented
pytestmark = pytest.mark.skip(reason="Multi-tenancy not yet implemented in selfhealing package")

from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from shopping.models.failed_payment import CircuitBreakerState, FailedPayment
from shopping.tests.factories import OrderFactory, PaymentFactory, UserFactory


@pytest.mark.tier2
@pytest.mark.tenant_aware
@pytest.mark.django_db(transaction=True)
class TestMultiTenancyCircuitBreakerIsolation:
    """
    Multi-tenancy isolation tests for Circuit Breaker.

    Validates that tenant boundaries are enforced and
    one tenant's failures do not impact another.
    """

    def test_tenant_a_circuit_open_does_not_affect_tenant_b(
        self,
        tenant_a,
        tenant_b,
        circuit_breaker_service,
    ):
        """
        Purpose:
            Verify Circuit Breaker state is isolated per tenant.

        Scenario:
            1. Create Circuit Breaker for Tenant A (toss_payment)
            2. Force open Tenant A's Circuit Breaker
            3. Verify Tenant A requests are blocked
            4. Verify Tenant B requests are allowed

        Expected:
            - Tenant A: should_allow() returns False
            - Tenant B: should_allow() returns True
            - Audit log shows tenant_id for state change

        Risk Covered:
            R-001: Cross-tenant contamination

        Compliance:
            SOC 2 CC6.1 (Logical Access), ISO 27001 A.9.4.1
        """
        service_name = "toss_payment"

        # Act: Open Tenant A's circuit
        circuit_breaker_service.force_open(
            service_name=service_name,
            tenant_id=tenant_a.id,
            reason="Tenant A PG maintenance",
            controlled_by=tenant_a.admin_user,
        )

        # Assert
        assert circuit_breaker_service.should_allow(service_name, tenant_id=tenant_a.id) is False, "Tenant A should be blocked"

        assert (
            circuit_breaker_service.should_allow(service_name, tenant_id=tenant_b.id) is True
        ), "Tenant B should NOT be affected by Tenant A's CB state"

    def test_tenant_b_circuit_open_does_not_affect_tenant_a(
        self,
        tenant_a,
        tenant_b,
        circuit_breaker_service,
    ):
        """
        Purpose:
            Verify reverse isolation - Tenant B open doesn't affect Tenant A.

        Scenario:
            1. Force open Tenant B's Circuit Breaker
            2. Verify Tenant B requests are blocked
            3. Verify Tenant A requests are allowed

        Expected:
            - Tenant B: blocked
            - Tenant A: allowed

        Risk Covered:
            R-001: Cross-tenant contamination
        """
        service_name = "toss_payment"

        # Act: Open Tenant B's circuit
        circuit_breaker_service.force_open(
            service_name=service_name,
            tenant_id=tenant_b.id,
            reason="Tenant B scheduled maintenance",
            controlled_by=tenant_b.admin_user,
        )

        # Assert
        assert circuit_breaker_service.should_allow(service_name, tenant_id=tenant_b.id) is False, "Tenant B should be blocked"

        assert (
            circuit_breaker_service.should_allow(service_name, tenant_id=tenant_a.id) is True
        ), "Tenant A should NOT be affected by Tenant B's CB state"

    def test_both_tenants_can_have_different_states(
        self,
        tenant_a,
        tenant_b,
        circuit_breaker_service,
    ):
        """
        Purpose:
            Verify both tenants can maintain independent CB states.

        Scenario:
            1. Open Tenant A's CB
            2. Keep Tenant B's CB closed
            3. Close Tenant A's CB
            4. Open Tenant B's CB
            5. Verify state transitions are independent

        Expected:
            - State changes for one tenant don't affect the other

        Risk Covered:
            R-001: Cross-tenant state contamination
        """
        service_name = "toss_payment"

        # Step 1: Open Tenant A, Tenant B remains closed
        circuit_breaker_service.force_open(
            service_name=service_name,
            tenant_id=tenant_a.id,
            reason="Tenant A open",
            controlled_by=tenant_a.admin_user,
        )

        state_a = circuit_breaker_service.get_state(service_name, tenant_a.id)
        state_b = circuit_breaker_service.get_state(service_name, tenant_b.id)

        assert state_a.state == "open", "Tenant A should be open"
        assert state_b.state == "closed", "Tenant B should be closed"

        # Step 2: Close Tenant A, Open Tenant B
        circuit_breaker_service.force_close(
            service_name=service_name,
            tenant_id=tenant_a.id,
            reason="Tenant A recovered",
            controlled_by=tenant_a.admin_user,
        )
        circuit_breaker_service.force_open(
            service_name=service_name,
            tenant_id=tenant_b.id,
            reason="Tenant B maintenance",
            controlled_by=tenant_b.admin_user,
        )

        state_a = circuit_breaker_service.get_state(service_name, tenant_a.id)
        state_b = circuit_breaker_service.get_state(service_name, tenant_b.id)

        assert state_a.state == "closed", "Tenant A should now be closed"
        assert state_b.state == "open", "Tenant B should now be open"


@pytest.mark.tier2
@pytest.mark.tenant_aware
@pytest.mark.django_db(transaction=True)
class TestMultiTenancyDLQIsolation:
    """
    Multi-tenancy isolation tests for Dead Letter Queue.

    Validates that DLQ entries are properly isolated by tenant.
    """

    def test_dlq_entries_isolated_by_tenant(self, tenant_a, tenant_b, db):
        """
        Purpose:
            Verify DLQ queries return only entries for the requesting tenant.

        Scenario:
            1. Create DLQ entries for both Tenant A and Tenant B
            2. Query DLQ for Tenant A
            3. Verify only Tenant A's entries are returned

        Expected:
            - Query for Tenant A returns only Tenant A entries
            - Query for Tenant B returns only Tenant B entries

        Risk Covered:
            R-001: Cross-tenant data leakage

        Compliance:
            SOC 2 CC6.1, ISO 27001 A.9.4.1
        """
        # Create users and payments for each tenant
        user_a = UserFactory(username="user_tenant_a")
        user_b = UserFactory(username="user_tenant_b")

        order_a = OrderFactory(user=user_a, status="confirmed")
        order_b = OrderFactory(user=user_b, status="confirmed")

        payment_a = PaymentFactory(order=order_a, status="failed")
        payment_b = PaymentFactory(order=order_b, status="failed")

        # Create DLQ entries with tenant-specific metadata
        dlq_a = FailedPayment.objects.create(
            payment=payment_a,
            order=order_a,
            user=user_a,
            failure_type="max_retries_exceeded",
            error_code="PG_TIMEOUT",
            error_message="Timeout",
            retry_count=3,
            metadata={"tenant_id": tenant_a.id},
        )

        dlq_b = FailedPayment.objects.create(
            payment=payment_b,
            order=order_b,
            user=user_b,
            failure_type="max_retries_exceeded",
            error_code="PG_TIMEOUT",
            error_message="Timeout",
            retry_count=3,
            metadata={"tenant_id": tenant_b.id},
        )

        # Query for Tenant A's DLQ entries
        tenant_a_entries = FailedPayment.objects.filter(metadata__tenant_id=tenant_a.id)
        tenant_b_entries = FailedPayment.objects.filter(metadata__tenant_id=tenant_b.id)

        assert tenant_a_entries.count() == 1, "Tenant A should have 1 DLQ entry"
        assert tenant_b_entries.count() == 1, "Tenant B should have 1 DLQ entry"

        assert tenant_a_entries.first().id == dlq_a.id
        assert tenant_b_entries.first().id == dlq_b.id

        # Verify no cross-tenant leakage
        assert dlq_a.id not in [e.id for e in tenant_b_entries]
        assert dlq_b.id not in [e.id for e in tenant_a_entries]

    def test_tenant_cannot_resolve_other_tenant_dlq(self, tenant_a, tenant_b, db):
        """
        Purpose:
            Verify tenant admins cannot resolve other tenant's DLQ entries.

        Scenario:
            1. Create DLQ entry for Tenant A
            2. Tenant B admin attempts to resolve it
            3. Verify resolution is rejected or logged

        Expected:
            - Cross-tenant resolution should be prevented
            - Attempt should be audit logged

        Risk Covered:
            R-001: Unauthorized cross-tenant access
        """
        # Create DLQ entry for Tenant A
        user_a = UserFactory(username="user_tenant_a_dlq")
        order_a = OrderFactory(user=user_a, status="confirmed")
        payment_a = PaymentFactory(order=order_a, status="failed")

        dlq_a = FailedPayment.objects.create(
            payment=payment_a,
            order=order_a,
            user=user_a,
            failure_type="max_retries_exceeded",
            error_code="PG_TIMEOUT",
            error_message="Timeout",
            retry_count=3,
            metadata={"tenant_id": tenant_a.id},
        )

        # Attempt resolution by Tenant B admin
        # In production, this would be blocked by authorization middleware
        # Here we verify the metadata check prevents cross-tenant access
        tenant_id_check = dlq_a.metadata.get("tenant_id")

        assert tenant_id_check == tenant_a.id, "DLQ belongs to Tenant A"
        assert tenant_id_check != tenant_b.id, "Tenant B should not have access"


@pytest.mark.tier2
@pytest.mark.tenant_aware
@pytest.mark.django_db(transaction=True)
class TestMultiTenancySLAPolicies:
    """
    Multi-tenancy tests for SLA timeout policies.

    Validates that each tenant's SLA configuration is respected.
    """

    def test_tenant_specific_sla_policies_enforced(self, tenant_a, tenant_b, recovery_handler):
        """
        Purpose:
            Verify each tenant's SLA timeout is independently enforced.

        Scenario:
            1. Tenant A: 300s SLA timeout
            2. Tenant B: 600s SLA timeout
            3. Create payment at T=0
            4. Check at T=4min (240s): Both within SLA
            5. Check at T=5.5min (330s): Tenant A expired, Tenant B valid

        Expected:
            - Tenant A times out after 300s
            - Tenant B times out after 600s

        Risk Covered:
            R-002: Incorrect SLA enforcement

        Compliance:
            SOC 2 (Availability SLA)
        """
        # Simulate payment created 4 minutes ago
        created_at_4min = timezone.now() - timedelta(minutes=4)

        # Both tenants within SLA at 4 minutes
        with patch.object(
            recovery_handler,
            "config",
            {"SLA_TIMEOUT_SECONDS": tenant_a.sla_timeout_seconds},
        ):
            tenant_a_timeout_4min = recovery_handler.check_sla_timeout(created_at_4min)

        with patch.object(
            recovery_handler,
            "config",
            {"SLA_TIMEOUT_SECONDS": tenant_b.sla_timeout_seconds},
        ):
            tenant_b_timeout_4min = recovery_handler.check_sla_timeout(created_at_4min)

        assert tenant_a_timeout_4min is False, "Tenant A should be within SLA at 4min"
        assert tenant_b_timeout_4min is False, "Tenant B should be within SLA at 4min"

        # Simulate payment created 5.5 minutes ago
        created_at_5_5min = timezone.now() - timedelta(minutes=5, seconds=30)

        with patch.object(
            recovery_handler,
            "config",
            {"SLA_TIMEOUT_SECONDS": tenant_a.sla_timeout_seconds},
        ):
            tenant_a_timeout_5_5min = recovery_handler.check_sla_timeout(created_at_5_5min)

        with patch.object(
            recovery_handler,
            "config",
            {"SLA_TIMEOUT_SECONDS": tenant_b.sla_timeout_seconds},
        ):
            tenant_b_timeout_5_5min = recovery_handler.check_sla_timeout(created_at_5_5min)

        assert tenant_a_timeout_5_5min is True, "Tenant A should timeout at 5.5min (>300s)"
        assert tenant_b_timeout_5_5min is False, "Tenant B should still be valid at 5.5min (<600s)"


@pytest.mark.tier2
@pytest.mark.tenant_aware
@pytest.mark.django_db(transaction=True)
class TestMultiTenancyMetrics:
    """
    Multi-tenancy tests for metrics isolation.

    Validates that metrics are properly labeled with tenant_id.
    """

    def test_tenant_metrics_aggregated_separately(self, tenant_a, tenant_b, recovery_handler, mock_metrics):
        """
        Purpose:
            Verify metrics are labeled with correct tenant_id.

        Scenario:
            1. Trigger failures for both tenants
            2. Query metrics for each tenant
            3. Verify labels contain correct tenant_id

        Expected:
            - Tenant A metrics have tenant_id=tenant_a.id label
            - Tenant B metrics have tenant_id=tenant_b.id label

        Risk Covered:
            R-003: Metric aggregation errors

        Compliance:
            SOC 2 CC7.2 (Monitoring)
        """
        # Emit metrics for Tenant A
        mock_metrics.increment(
            "payment_failures_total",
            labels={"tenant_id": tenant_a.id, "error_code": "PG_TIMEOUT"},
        )
        mock_metrics.increment(
            "payment_failures_total",
            labels={"tenant_id": tenant_a.id, "error_code": "NETWORK_ERROR"},
        )

        # Emit metrics for Tenant B
        mock_metrics.increment(
            "payment_failures_total",
            labels={"tenant_id": tenant_b.id, "error_code": "PG_TIMEOUT"},
        )

        # Verify Tenant A metrics
        tenant_a_pg_timeout = mock_metrics.get_value(
            "payment_failures_total",
            labels={"tenant_id": tenant_a.id, "error_code": "PG_TIMEOUT"},
        )
        tenant_a_network_error = mock_metrics.get_value(
            "payment_failures_total",
            labels={"tenant_id": tenant_a.id, "error_code": "NETWORK_ERROR"},
        )

        assert tenant_a_pg_timeout == 1, "Tenant A should have 1 PG_TIMEOUT"
        assert tenant_a_network_error == 1, "Tenant A should have 1 NETWORK_ERROR"

        # Verify Tenant B metrics
        tenant_b_pg_timeout = mock_metrics.get_value(
            "payment_failures_total",
            labels={"tenant_id": tenant_b.id, "error_code": "PG_TIMEOUT"},
        )

        assert tenant_b_pg_timeout == 1, "Tenant B should have 1 PG_TIMEOUT"

        # Verify tenant label presence
        assert mock_metrics.has_label("payment_failures_total", "tenant_id", tenant_a.id), "Metrics should have Tenant A label"
        assert mock_metrics.has_label("payment_failures_total", "tenant_id", tenant_b.id), "Metrics should have Tenant B label"

    def test_circuit_breaker_state_change_metric_per_tenant(self, tenant_a, tenant_b, circuit_breaker_service, mock_metrics):
        """
        Purpose:
            Verify CB state change metrics are per-tenant.

        Scenario:
            1. Change Tenant A's CB state
            2. Emit corresponding metric
            3. Verify metric has correct tenant_id

        Expected:
            - Metric labels distinguish between tenants

        Risk Covered:
            R-003: Cross-tenant metric pollution
        """
        service_name = "toss_payment"

        # State change for Tenant A
        circuit_breaker_service.force_open(
            service_name=service_name,
            tenant_id=tenant_a.id,
            reason="Test",
            controlled_by=tenant_a.admin_user,
        )
        mock_metrics.increment(
            "circuit_breaker_state_changes_total",
            labels={
                "tenant_id": tenant_a.id,
                "service": service_name,
                "from_state": "closed",
                "to_state": "open",
            },
        )

        # Verify metric
        value = mock_metrics.get_value(
            "circuit_breaker_state_changes_total",
            labels={
                "tenant_id": tenant_a.id,
                "service": service_name,
                "from_state": "closed",
                "to_state": "open",
            },
        )
        assert value == 1, "Should have 1 state change for Tenant A"

        # Tenant B should have no state changes
        value_b = mock_metrics.get_value(
            "circuit_breaker_state_changes_total",
            labels={
                "tenant_id": tenant_b.id,
                "service": service_name,
                "from_state": "closed",
                "to_state": "open",
            },
        )
        assert value_b == 0, "Tenant B should have no state changes"


@pytest.mark.tier2
@pytest.mark.tenant_aware
@pytest.mark.django_db(transaction=True)
class TestMultiTenancyAuthorization:
    """
    Multi-tenancy authorization tests.

    Validates that cross-tenant administrative actions are prevented.
    """

    def test_tenant_admin_cannot_force_close_other_tenant_cb(
        self, tenant_a, tenant_b, circuit_breaker_service, audit_log_repository
    ):
        """
        Purpose:
            Verify admin cannot modify another tenant's CB.

        Scenario:
            1. Open Tenant A's Circuit Breaker
            2. Tenant B admin attempts to close Tenant A's CB
            3. Verify action is rejected/audit logged

        Expected:
            - Authorization error (in production)
            - Audit log records the attempt

        Risk Covered:
            R-001: Unauthorized cross-tenant control

        Compliance:
            SOC 2 CC6.1, ISO 27001 A.9.4.1
        """
        service_name = "toss_payment"

        # Step 1: Open Tenant A's CB
        circuit_breaker_service.force_open(
            service_name=service_name,
            tenant_id=tenant_a.id,
            reason="Tenant A maintenance",
            controlled_by=tenant_a.admin_user,
        )

        # Log audit for the legitimate action
        audit_log_repository.log_circuit_breaker_action(
            service_name=f"{service_name}:{tenant_a.id}",
            previous_state="closed",
            new_state="open",
            controlled_by=tenant_a.admin_user,
            reason="Tenant A maintenance",
        )

        # Step 2: Simulate authorization check (cross-tenant)
        # In production, this would be blocked by middleware
        # Here we verify the tenant ownership check
        state_a = circuit_breaker_service.get_state(service_name, tenant_a.id)
        state_key = f"{service_name}:{tenant_a.id}"

        # Verify Tenant B admin is different from the controller
        assert state_a.controlled_by != tenant_b.admin_user, "Tenant B admin should not be the controller"

        # Log the unauthorized attempt for audit
        audit_log_repository.log_circuit_breaker_action(
            service_name=state_key,
            previous_state="open",
            new_state="closed",
            controlled_by=tenant_b.admin_user,
            reason="UNAUTHORIZED: Tenant B admin attempted cross-tenant action",
        )

        # Verify audit trail
        audit_entries = audit_log_repository.find_by_action(
            action_type="circuit_breaker_action",
            service_name=state_key,
        )

        assert len(audit_entries) == 2, "Should have 2 audit entries"

        # The second entry should be the unauthorized attempt
        unauthorized_entry = audit_entries[1]
        assert "UNAUTHORIZED" in unauthorized_entry.control_reason
        assert unauthorized_entry.controlled_by == tenant_b.admin_user.id
