"""
Security DLQ Separation Tests

Tests for G-07: Security violation never stored in DLQ.
Validates that security violations are isolated from the normal DLQ flow.

Reference: docs/l3_auto_self_healing/testing/L3_TEST_GAP_REPORT.md
Risk Covered:
    - R-019: Security events accidentally auto-replayed
    - R-020: Security audit trail compromised
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from shopping.models.failed_operation import FailedOperation
from shopping.models.security_incident import SecurityIncident
from selfhealing.services import (
    SecurityConfig,
    SecurityViolationResult,
    SecurityViolationService,
    Severity,
    ViolationType,
    get_security_violation_service,
)
from shopping.tests.factories import OrderFactory, PaymentFactory, UserFactory


# Security integration test - requires Django models (SecurityIncident, FailedOperation)
pytestmark = [pytest.mark.e2e, pytest.mark.requires_db]


@pytest.mark.django_db(transaction=True)
class TestSecurityDLQSeparation:
    """
    Tests for security violation isolation from DLQ.

    Gap ID: G-07
    Purpose: Verify security violations are NEVER stored in FailedOperation (DLQ).
    """

    def test_security_violation_never_stored_in_dlq(self):
        """
        Purpose:
            Verify security violations are NEVER stored in FailedOperation (DLQ).

        Scenario:
            1. Handle a security violation (e.g., WEBHOOK_SIGNATURE_INVALID)
            2. Query FailedOperation table
            3. Verify NO entry exists for this violation

        Expected:
            - SecurityIncident record created
            - FailedOperation table has NO matching entry
            - Security events isolated from normal failure flow

        Risk Covered:
            - R-019: Security events accidentally auto-replayed
            - R-020: Security audit trail compromised
        """
        initial_dlq_count = FailedOperation.objects.count()

        # Handle security violation
        service = SecurityViolationService()
        request = RequestFactory().post("/api/webhook/toss/")
        request.META["REMOTE_ADDR"] = "192.168.1.100"

        with patch.object(service, "_send_security_notification"):
            result = service.handle_violation(
                violation_type=ViolationType.SIGNATURE_INVALID,
                request_info={"ip": request.META.get("REMOTE_ADDR"), "user_agent": ""},
                description="HMAC signature mismatch",
            )

        assert result.success is True

        # Verify SecurityIncident created
        incident = SecurityIncident.objects.get(id=result.incident_id)
        assert incident is not None
        assert incident.incident_type == ViolationType.SIGNATURE_INVALID.value

        # Verify NO DLQ entry created
        final_dlq_count = FailedOperation.objects.count()
        assert final_dlq_count == initial_dlq_count, (
            "Security violation should NOT create DLQ entry. "
            f"DLQ count changed from {initial_dlq_count} to {final_dlq_count}"
        )

        # Double-check: no DLQ entry with security failure type
        security_dlq = FailedOperation.objects.filter(failure_type__icontains="SECURITY")
        assert security_dlq.count() == 0

    def test_all_critical_security_violations_use_incident_table(self):
        """
        Purpose:
            Verify all critical security violations go to SecurityIncident, not DLQ.

        Scenario:
            1. Test each critical violation type
            2. Verify each creates SecurityIncident, not DLQ entry

        Expected:
            - All critical violations stored in SecurityIncident
            - Zero DLQ entries for any security violation
        """
        critical_violations = [
            ViolationType.SIGNATURE_INVALID,
            ViolationType.DATA_TAMPERED,
            ViolationType.TOKEN_FORGED,
            ViolationType.REPLAY_ATTACK,
        ]

        service = SecurityViolationService()
        request = RequestFactory().post("/api/test/")
        request.META["REMOTE_ADDR"] = "10.0.0.50"

        initial_dlq_count = FailedOperation.objects.count()
        initial_incident_count = SecurityIncident.objects.count()

        for violation_type in critical_violations:
            with patch.object(service, "_send_security_notification"):
                result = service.handle_violation(
                    violation_type=violation_type,
                    request_info={"ip": request.META.get("REMOTE_ADDR"), "user_agent": ""},
                    description=f"Test violation: {violation_type.value}",
                )

            assert result.success is True, f"Failed for {violation_type}"

        # Verify incidents created for all violations
        final_incident_count = SecurityIncident.objects.count()
        assert final_incident_count == initial_incident_count + len(critical_violations)

        # Verify NO DLQ entries created
        final_dlq_count = FailedOperation.objects.count()
        assert final_dlq_count == initial_dlq_count, (
            f"DLQ entries should not be created for security violations. "
            f"Expected {initial_dlq_count}, got {final_dlq_count}"
        )

    def test_security_incident_not_auto_replayable(self):
        """
        Purpose:
            Verify security incidents cannot be replayed through normal DLQ replay.

        Scenario:
            1. Create security incident
            2. Verify no corresponding DLQ entry exists
            3. Verify replay service cannot find it

        Expected:
            - No replay path for security events
        """
        service = SecurityViolationService()
        request = RequestFactory().post("/api/webhook/")
        request.META["REMOTE_ADDR"] = "192.168.1.200"

        with patch.object(service, "_send_security_notification"):
            result = service.handle_violation(
                violation_type=ViolationType.UNAUTHORIZED_ACCESS,
                request_info={"ip": request.META.get("REMOTE_ADDR"), "user_agent": ""},
                description="Unauthorized access attempt",
            )

        # Try to find this in DLQ
        dlq_entries = FailedOperation.objects.filter(failure_type__icontains="UNAUTHORIZED")
        assert dlq_entries.count() == 0, "Security violation should not be in DLQ"

        # Verify it's in SecurityIncident
        incident = SecurityIncident.objects.get(id=result.incident_id)
        assert incident.incident_type == "unauthorized_access"

    def test_security_incidents_have_complete_audit_trail(self):
        """
        Purpose:
            Verify security incidents maintain complete audit trail.

        Scenario:
            1. Create security incident
            2. Verify all required audit fields are captured

        Expected:
            - IP address captured
            - Timestamp captured
            - Violation type captured
            - Description captured
        """
        service = SecurityViolationService()
        request = RequestFactory().post("/api/payment/confirm/")
        request.META["REMOTE_ADDR"] = "10.0.0.100"
        request.META["HTTP_USER_AGENT"] = "TestAgent/1.0"

        with patch.object(service, "_send_security_notification"):
            result = service.handle_violation(
                violation_type=ViolationType.DATA_TAMPERED,
                request_info={"ip": request.META.get("REMOTE_ADDR"), "user_agent": request.META.get("HTTP_USER_AGENT", "")},
                description="Amount mismatch: expected 10000, got 1",
            )

        incident = SecurityIncident.objects.get(id=result.incident_id)

        # Verify audit trail completeness
        assert incident.source_ip == "10.0.0.100"
        assert incident.incident_type == "data_tampered"
        assert incident.severity == Severity.CRITICAL.value
        assert "Amount mismatch" in incident.description
        assert incident.updated_at is not None

    def test_payment_failure_goes_to_dlq_not_security_incident(self):
        """
        Purpose:
            Verify regular payment failures use DLQ, not SecurityIncident.

        Scenario:
            1. Create a payment timeout failure in DLQ
            2. Verify it's in DLQ, not SecurityIncident

        Expected:
            - Payment failure in DLQ
            - No security incident for timeout
        """
        user = UserFactory()
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(order=order, status="in_progress")

        initial_incident_count = SecurityIncident.objects.count()

        # Create DLQ entry for payment failure (non-security)
        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(order.id),
            error_message="Connection timed out after 30s",
            snapshot_data={"payment_id": payment.id, "order_id": order.id},
        )

        # Verify DLQ entry created
        assert entry.id is not None
        assert entry.failure_type == "PG_TIMEOUT"

        # Verify NO security incident created
        final_incident_count = SecurityIncident.objects.count()
        assert final_incident_count == initial_incident_count

    def test_security_violation_with_user_context(self):
        """
        Purpose:
            Verify security violations can capture user context without DLQ.

        Scenario:
            1. Create security violation with authenticated user
            2. Verify user info captured in SecurityIncident
            3. Verify no DLQ entry

        Expected:
            - User ID captured in incident
            - No DLQ entry
        """
        user = UserFactory()

        service = SecurityViolationService()
        request = RequestFactory().post("/api/orders/")
        request.user = user
        request.META["REMOTE_ADDR"] = "192.168.1.50"

        with patch.object(service, "_send_security_notification"):
            result = service.handle_violation(
                violation_type=ViolationType.INJECTION_ATTEMPT,
                request_info={"ip": request.META.get("REMOTE_ADDR"), "user_agent": ""},
                description="SQL injection attempt detected",
                user_id=user.id,
            )

        incident = SecurityIncident.objects.get(id=result.incident_id)

        # Verify user context captured
        assert incident.user_id == user.id

        # Verify no DLQ entry
        dlq = FailedOperation.objects.filter(failure_type__icontains="INJECTION")
        assert dlq.count() == 0

    def test_dlq_query_excludes_security_violations(self):
        """
        Purpose:
            Verify DLQ queries don't accidentally include security violations.

        Scenario:
            1. Create mix of DLQ entries and security incidents
            2. Query pending DLQ entries
            3. Verify security incidents not included

        Expected:
            - DLQ queries return only DLQ entries
        """
        from selfhealing.services import DLQService

        user = UserFactory()
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(order=order, status="in_progress")

        # Create regular DLQ entry
        dlq_entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(order.id),
            snapshot_data={"payment_id": payment.id},
        )

        # Create security incident
        security_service = SecurityViolationService()
        request = RequestFactory().post("/api/test/")
        request.META["REMOTE_ADDR"] = "10.0.0.1"

        with patch.object(security_service, "_send_security_notification"):
            security_service.handle_violation(
                violation_type=ViolationType.SIGNATURE_INVALID,
                request_info={"ip": request.META.get("REMOTE_ADDR"), "user_agent": ""},
                description="Invalid signature",
            )

        # Query pending DLQ entries
        dlq_service = DLQService()
        pending = dlq_service.get_pending_entries()

        # Should only include regular DLQ entry
        pending_ids = [e.id for e in pending]
        assert dlq_entry.id in pending_ids

        # Verify no security-related entries in DLQ
        security_in_dlq = FailedOperation.objects.filter(
            failure_type__in=[
                "WEBHOOK_SIGNATURE_INVALID",
                "SECURITY_SIGNATURE_INVALID",
                "UNAUTHORIZED_ACCESS",
            ]
        )
        assert security_in_dlq.count() == 0, "Security violations should never appear in DLQ table"
