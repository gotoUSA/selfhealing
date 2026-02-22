"""
Self-Healing Control API Integration Tests

Based on: docs/self_healing/5_CONTROL_API/CONTROL_API_TEST_REQUIREMENTS.md

Test Categories:
1. Action Execution Tests (ACT-001 ~ ACT-007)
2. Environment Constraint Tests (ENV-001 ~ ENV-007)
3. TTL Management Tests
4. Authorization Tests
5. Validation Tests

Note: This module is skipped because REST API tests depend on Django Test Client.
      When selling selfhealing package, buyers should write their own API integration tests.
"""

import pytest

# Django REST API integration tests - require database
pytestmark = [pytest.mark.e2e, pytest.mark.requires_db]
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status

from selfhealing.services import (
    ControlAPIService,
    ControlRequest,
)
from selfhealing.services.control_api_service import assess_risk_level, classify_reason
from shopping.serializers.self_healing_serializers import (
    ControlRequestSerializer,
    ControlAPIActions,
    ControlAPIEnvironments,
    RiskLevels,
)

User = get_user_model()


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def admin_user(db):
    """Create admin user for testing."""
    user = User.objects.create_user(
        username="test_admin",
        email="admin@test.com",
        password="AdminPass123!",
        is_staff=True,
        is_superuser=True,
    )
    return user


@pytest.fixture
def regular_user(db):
    """Create regular user for testing."""
    user = User.objects.create_user(
        username="test_user",
        email="user@test.com",
        password="UserPass123!",
        is_staff=False,
    )
    return user


@pytest.fixture
def api_client():
    """Create API client."""
    return APIClient()


@pytest.fixture
def authenticated_admin_client(api_client, admin_user):
    """Create authenticated admin API client."""
    api_client.force_authenticate(user=admin_user)
    return api_client


@pytest.fixture
def authenticated_user_client(api_client, regular_user):
    """Create authenticated regular user API client."""
    api_client.force_authenticate(user=regular_user)
    return api_client


@pytest.fixture
def control_api_service():
    """Create Control API service instance."""
    return ControlAPIService()


# =============================================================================
# 2. Action Execution Tests
# =============================================================================


class TestActionExecution:
    """
    Tests for Control API action execution.

    Based on: CONTROL_API_TEST_REQUIREMENTS.md §2
    """

    @pytest.mark.django_db
    def test_allow_action_enables_operations(self, control_api_service):
        """
        ACT-001: Verify that allow action enables service operations.

        Scenario:
            1. Service is in blocked state
            2. Execute allow action
            3. Verify operations can proceed

        Expected:
            - Service state changes to allow
            - CircuitBreaker state is CLOSED
        """
        # First block the service
        block_request = ControlRequest(
            service_name="test_payment",
            action=ControlAPIActions.BLOCK,
            reason="Test setup - block first",
            environment=ControlAPIEnvironments.TEST,
        )
        control_api_service.execute(block_request)

        # Now allow
        request = ControlRequest(
            service_name="test_payment",
            action=ControlAPIActions.ALLOW,
            reason="Test recovery",
            environment=ControlAPIEnvironments.TEST,
        )

        response = control_api_service.execute(request)

        assert response.status == "success"
        assert response.action_applied == "allow"
        assert response.system_state == "allow"

    @pytest.mark.django_db
    def test_block_action_disables_operations(self, control_api_service):
        """
        ACT-002: Verify that block action disables service operations.

        Scenario:
            1. Service is in normal state
            2. Execute block action
            3. Verify operations are blocked

        Expected:
            - Service state changes to block
            - CircuitBreaker state is OPEN
        """
        request = ControlRequest(
            service_name="test_payment",
            action=ControlAPIActions.BLOCK,
            reason="PG maintenance",
            environment=ControlAPIEnvironments.TEST,
            ttl_minutes=30,
        )

        response = control_api_service.execute(request)

        assert response.status == "success"
        assert response.action_applied == "block"
        assert response.system_state == "block"
        assert response.effective_until is not None

    @pytest.mark.django_db
    def test_override_action_bypasses_rules(self, control_api_service):
        """
        ACT-003: Verify that override temporarily bypasses policies.

        Scenario:
            1. Execute override with TTL
            2. Verify bypass is active

        Expected:
            - Operation succeeds with TTL
            - Audit records override usage
        """
        request = ControlRequest(
            service_name="test_payment",
            action=ControlAPIActions.OVERRIDE,
            reason="SLA breach mitigation",
            environment=ControlAPIEnvironments.TEST,
            ttl_minutes=15,
        )

        response = control_api_service.execute(request)

        assert response.status == "success"
        assert response.action_applied == "override"
        assert response.system_state == "allow"
        assert response.effective_until is not None

    @pytest.mark.django_db
    def test_reset_action_reverts_to_default(self, control_api_service):
        """
        ACT-004: Verify that reset reverts to default configuration.

        Scenario:
            1. Service has manual override
            2. Execute reset action
            3. Verify default state

        Expected:
            - All overrides cleared
            - Default state restored
        """
        # First apply an override
        override_request = ControlRequest(
            service_name="test_payment",
            action=ControlAPIActions.OVERRIDE,
            reason="Test override",
            environment=ControlAPIEnvironments.TEST,
            ttl_minutes=10,
        )
        control_api_service.execute(override_request)

        # Now reset
        request = ControlRequest(
            service_name="test_payment",
            action=ControlAPIActions.RESET,
            reason="Cleanup after test",
            environment=ControlAPIEnvironments.TEST,
        )

        response = control_api_service.execute(request)

        assert response.status == "success"
        assert response.action_applied == "reset"
        assert response.system_state == "allow"

    @pytest.mark.django_db
    def test_inject_failure_simulates_failures(self, control_api_service):
        """
        ACT-005: Verify inject_failure simulates failures in chaos.

        Scenario:
            1. Execute inject_failure in chaos environment
            2. Verify failure injection is active

        Expected:
            - Failure injection enabled
            - Configuration stored
        """
        request = ControlRequest(
            service_name="test_payment",
            action=ControlAPIActions.INJECT_FAILURE,
            reason="Chaos testing",
            environment=ControlAPIEnvironments.CHAOS,
            ttl_minutes=5,
            metadata={
                "failure_rate": 0.5,
                "failure_type": "timeout",
            },
        )

        response = control_api_service.execute(request)

        assert response.status == "success"
        assert response.action_applied == "inject_failure"
        assert response.evidence.get("failure_rate") == 0.5

        # Verify injection is active
        assert control_api_service.is_failure_injection_active("test_payment")
        config = control_api_service.get_failure_injection_config("test_payment")
        assert config["failure_rate"] == 0.5
        assert config["failure_type"] == "timeout"


# =============================================================================
# 3. Environment Constraint Tests
# =============================================================================


class TestEnvironmentConstraints:
    """
    Tests for environment-specific restrictions.

    Based on: CONTROL_API_TEST_REQUIREMENTS.md §3
    """

    @pytest.mark.django_db
    def test_inject_failure_forbidden_in_ops(self, control_api_service):
        """
        ENV-001: Verify inject_failure is forbidden in ops environment.

        Expected:
            - Status: rejected
            - Error code: ACTION_FORBIDDEN_IN_ENVIRONMENT
        """
        request = ControlRequest(
            service_name="payment",
            action=ControlAPIActions.INJECT_FAILURE,
            reason="Should be rejected",
            environment=ControlAPIEnvironments.OPS,
            ttl_minutes=5,
        )

        response = control_api_service.execute(request)

        assert response.status == "rejected"
        assert response.error_code == "ACTION_FORBIDDEN_IN_ENVIRONMENT"

    @pytest.mark.django_db
    def test_inject_failure_allowed_in_chaos(self, control_api_service):
        """
        ENV-002: Verify inject_failure is allowed in chaos environment.
        """
        request = ControlRequest(
            service_name="payment",
            action=ControlAPIActions.INJECT_FAILURE,
            reason="Chaos testing",
            environment=ControlAPIEnvironments.CHAOS,
            ttl_minutes=5,
        )

        response = control_api_service.execute(request)

        assert response.status == "success"

    @pytest.mark.django_db
    def test_inject_failure_allowed_in_test(self, control_api_service):
        """
        ENV-003: Verify inject_failure is allowed in test environment.
        """
        request = ControlRequest(
            service_name="payment",
            action=ControlAPIActions.INJECT_FAILURE,
            reason="Unit testing",
            environment=ControlAPIEnvironments.TEST,
            ttl_minutes=5,
        )

        response = control_api_service.execute(request)

        assert response.status == "success"

    @pytest.mark.django_db
    def test_override_requires_ttl_in_ops(self, control_api_service):
        """
        ENV-004: Verify override requires TTL in ops environment.

        Expected:
            - Status: rejected
            - Error code: TTL_REQUIRED_FOR_OPS_OVERRIDE
        """
        request = ControlRequest(
            service_name="payment",
            action=ControlAPIActions.OVERRIDE,
            reason="Should be rejected",
            environment=ControlAPIEnvironments.OPS,
            # No ttl_minutes
        )

        response = control_api_service.execute(request)

        assert response.status == "rejected"
        assert response.error_code == "TTL_REQUIRED_FOR_OPS_OVERRIDE"

    @pytest.mark.django_db
    def test_override_ttl_optional_in_test(self, control_api_service):
        """
        ENV-005: Verify override TTL is optional in test environment.
        """
        request = ControlRequest(
            service_name="payment",
            action=ControlAPIActions.OVERRIDE,
            reason="Test override",
            environment=ControlAPIEnvironments.TEST,
            # No ttl_minutes - should be OK in test
        )

        response = control_api_service.execute(request)

        assert response.status == "success"

    @pytest.mark.django_db
    def test_ttl_max_limit_in_ops(self, control_api_service):
        """
        ENV-006: Verify TTL > 60 min is rejected in ops.

        Expected:
            - Status: rejected
            - Error code: TTL_EXCEEDS_OPS_LIMIT
        """
        request = ControlRequest(
            service_name="payment",
            action=ControlAPIActions.OVERRIDE,
            reason="TTL too long",
            environment=ControlAPIEnvironments.OPS,
            ttl_minutes=120,  # Exceeds 60 min limit
        )

        response = control_api_service.execute(request)

        assert response.status == "rejected"
        assert response.error_code == "TTL_EXCEEDS_OPS_LIMIT"

    @pytest.mark.django_db
    def test_all_actions_allowed_in_test(self, control_api_service):
        """
        ENV-007: Verify all actions are allowed in test environment.
        """
        actions = [
            ControlAPIActions.ALLOW,
            ControlAPIActions.BLOCK,
            ControlAPIActions.OVERRIDE,
            ControlAPIActions.RESET,
            ControlAPIActions.INJECT_FAILURE,
        ]

        for action in actions:
            request = ControlRequest(
                service_name=f"test_service_{action}",
                action=action,
                reason=f"Testing {action}",
                environment=ControlAPIEnvironments.TEST,
                ttl_minutes=5,  # Include TTL for override
            )

            response = control_api_service.execute(request)
            assert response.status == "success", f"Action {action} should be allowed in test"


# =============================================================================
# 4. Authorization Tests (API Level)
# =============================================================================


class TestAPIAuthorization:
    """
    Tests for API-level authorization.

    Based on: CONTROL_API_SECURITY_GOVERNANCE.md §3
    """

    @pytest.mark.django_db
    def test_admin_can_execute_control_actions(self, authenticated_admin_client):
        """Verify admin users can execute control actions."""
        response = authenticated_admin_client.post(
            "/api/self-healing/control/",
            data={
                "service_name": "payment",
                "action": "allow",
                "environment": "test",
                "reason": "Admin test",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.django_db
    def test_regular_user_cannot_execute_control_actions(self, authenticated_user_client):
        """Verify regular users cannot execute control actions."""
        response = authenticated_user_client.post(
            "/api/self-healing/control/",
            data={
                "service_name": "payment",
                "action": "allow",
                "environment": "test",
                "reason": "User test",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.django_db
    def test_regular_user_can_read_status(self, authenticated_user_client):
        """Verify regular users can read status."""
        response = authenticated_user_client.get("/api/self-healing/status/")

        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.django_db
    def test_unauthenticated_cannot_access_control(self, api_client):
        """Verify unauthenticated users cannot access control."""
        response = api_client.post(
            "/api/self-healing/control/",
            data={
                "service_name": "payment",
                "action": "allow",
                "environment": "test",
                "reason": "Anonymous test",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.django_db
    def test_health_endpoint_is_public(self, api_client):
        """Verify health endpoint is publicly accessible."""
        response = api_client.get("/api/self-healing/health/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "status" in data


# =============================================================================
# 5. Serializer Validation Tests
# =============================================================================


class TestSerializerValidation:
    """Tests for request serializer validation."""

    def test_valid_allow_request(self):
        """Verify valid allow request passes validation."""
        data = {
            "service_name": "payment",
            "action": "allow",
            "environment": "test",
            "reason": "Test reason",
        }
        serializer = ControlRequestSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_valid_override_with_ttl(self):
        """Verify override with TTL passes validation."""
        data = {
            "service_name": "payment",
            "action": "override",
            "environment": "ops",
            "reason": "SLA breach",
            "ttl_minutes": 30,
        }
        serializer = ControlRequestSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_missing_required_fields(self):
        """Verify missing required fields fail validation."""
        data = {
            "service_name": "payment",
            # Missing action, environment, reason
        }
        serializer = ControlRequestSerializer(data=data)
        assert not serializer.is_valid()
        assert "action" in serializer.errors
        assert "environment" in serializer.errors
        assert "reason" in serializer.errors

    def test_invalid_action(self):
        """Verify invalid action fails validation."""
        data = {
            "service_name": "payment",
            "action": "invalid_action",
            "environment": "test",
            "reason": "Test",
        }
        serializer = ControlRequestSerializer(data=data)
        assert not serializer.is_valid()
        assert "action" in serializer.errors

    def test_inject_failure_rejected_in_ops(self):
        """Verify inject_failure in ops fails validation."""
        data = {
            "service_name": "payment",
            "action": "inject_failure",
            "environment": "ops",
            "reason": "Should fail",
        }
        serializer = ControlRequestSerializer(data=data)
        assert not serializer.is_valid()
        # Check for ACTION_FORBIDDEN error
        assert "action" in serializer.errors

    def test_override_without_ttl_in_ops(self):
        """Verify override without TTL in ops fails validation."""
        data = {
            "service_name": "payment",
            "action": "override",
            "environment": "ops",
            "reason": "No TTL",
            # No ttl_minutes
        }
        serializer = ControlRequestSerializer(data=data)
        assert not serializer.is_valid()
        assert "ttl_minutes" in serializer.errors

    def test_override_ttl_exceeds_limit_in_ops(self):
        """Verify override with TTL > 60 in ops fails validation."""
        data = {
            "service_name": "payment",
            "action": "override",
            "environment": "ops",
            "reason": "TTL too long",
            "ttl_minutes": 120,
        }
        serializer = ControlRequestSerializer(data=data)
        assert not serializer.is_valid()
        assert "ttl_minutes" in serializer.errors


# =============================================================================
# 6. Risk Classification Tests
# =============================================================================


class TestRiskClassification:
    """Tests for risk level assessment."""

    def test_allow_in_test_is_info(self):
        """Verify allow in test is INFO risk."""
        risk = assess_risk_level(ControlAPIActions.ALLOW, ControlAPIEnvironments.TEST)
        assert risk == RiskLevels.INFO

    def test_block_in_ops_is_high(self):
        """Verify block in ops is HIGH risk."""
        risk = assess_risk_level(ControlAPIActions.BLOCK, ControlAPIEnvironments.OPS)
        assert risk == RiskLevels.HIGH

    def test_override_in_ops_is_critical(self):
        """Verify override in ops is CRITICAL risk."""
        risk = assess_risk_level(ControlAPIActions.OVERRIDE, ControlAPIEnvironments.OPS)
        assert risk == RiskLevels.CRITICAL

    def test_inject_failure_in_ops_is_forbidden(self):
        """Verify inject_failure in ops is FORBIDDEN risk."""
        risk = assess_risk_level(ControlAPIActions.INJECT_FAILURE, ControlAPIEnvironments.OPS)
        assert risk == RiskLevels.FORBIDDEN


# =============================================================================
# 7. Reason Classification Tests
# =============================================================================


class TestReasonClassification:
    """Tests for reason classification."""

    def test_external_dependency_classification(self):
        """Verify external dependency reasons are classified correctly."""
        reasons = [
            "External API timeout",
            "PG connection failed",
            "Payment gateway down",
        ]
        for reason in reasons:
            classification = classify_reason(reason)
            assert classification == "external-dependency-failure", f"Failed for: {reason}"

    def test_maintenance_classification(self):
        """Verify maintenance reasons are classified correctly."""
        reasons = [
            "Scheduled maintenance",
            "System upgrade",
            "Deploy new version",
        ]
        for reason in reasons:
            classification = classify_reason(reason)
            assert classification == "maintenance-window"

    def test_sla_breach_classification(self):
        """Verify SLA breach reasons are classified correctly."""
        reasons = [
            "SLA breach detected",
            "Threshold violation",
        ]
        for reason in reasons:
            classification = classify_reason(reason)
            assert classification == "sla-breach-mitigation"

    def test_recovery_classification(self):
        """Verify recovery reasons are classified correctly."""
        reasons = [
            "Service recovered",
            "Issue fixed",
            "System restored",
        ]
        for reason in reasons:
            classification = classify_reason(reason)
            assert classification == "recovery-procedure"
