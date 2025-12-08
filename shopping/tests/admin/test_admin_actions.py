"""
Tests for Admin actions.

Verifies that:
- Admin actions execute correctly
- Service layer is properly called
- State transitions are valid
"""

import pytest
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch, MagicMock

from shopping.models import CircuitBreakerState, FailedOperation
from shopping.tests.factories import (
    OrderFactory,
    PaymentFactory,
    UserFactory,
)


@pytest.fixture
def admin_user(db):
    """Create a superuser for admin access."""
    return UserFactory.admin(username="action_test_admin")


@pytest.fixture
def admin_client(client, admin_user):
    """Client logged in as admin."""
    client.force_login(admin_user)
    return client


@pytest.fixture
def circuit_breaker_state_closed(db):
    """Create a closed circuit breaker state."""
    return CircuitBreakerState.objects.create(
        service_name="test_service",
        state="closed",
        failure_count=0,
        success_count=0,
    )


@pytest.fixture
def circuit_breaker_state_open(db):
    """Create an open circuit breaker state."""
    return CircuitBreakerState.objects.create(
        service_name="test_service_open",
        state="open",
        failure_count=10,
        success_count=0,
    )


@pytest.fixture
def failed_operation_pending(db):
    """Create a pending failed operation."""
    return FailedOperation.objects.create(
        domain="payment",
        failure_type="test_failure",
        error_message="Test error",
        status="pending",
    )


@pytest.mark.django_db
class TestCircuitBreakerAdminActions:
    """Tests for circuit breaker admin actions."""

    def test_force_open_action(self, admin_client, circuit_breaker_state_closed):
        """Test force open circuit breaker action."""
        cb_state = circuit_breaker_state_closed
        
        url = reverse("admin:shopping_circuitbreakerstate_changelist")
        response = admin_client.post(url, {
            "action": "force_open_selected",
            "_selected_action": [cb_state.pk],
        }, follow=True)
        
        assert response.status_code == 200
        cb_state.refresh_from_db()
        assert cb_state.state == "open"

    def test_force_close_action(self, admin_client, circuit_breaker_state_open):
        """Test force close circuit breaker action."""
        cb_state = circuit_breaker_state_open
        
        url = reverse("admin:shopping_circuitbreakerstate_changelist")
        response = admin_client.post(url, {
            "action": "force_close_selected",
            "_selected_action": [cb_state.pk],
        }, follow=True)
        
        assert response.status_code == 200
        cb_state.refresh_from_db()
        assert cb_state.state == "closed"

    def test_reset_circuit_action(self, admin_client, circuit_breaker_state_open):
        """Test reset circuit breaker action."""
        cb_state = circuit_breaker_state_open
        
        url = reverse("admin:shopping_circuitbreakerstate_changelist")
        response = admin_client.post(url, {
            "action": "reset_selected",
            "_selected_action": [cb_state.pk],
        }, follow=True)
        
        assert response.status_code == 200
        cb_state.refresh_from_db()
        assert cb_state.state == "closed"
        assert cb_state.failure_count == 0


@pytest.mark.django_db
class TestFailedOperationAdminActions:
    """Tests for failed operation (DLQ) admin actions."""

    def test_mark_as_resolved_action(self, admin_client, failed_operation_pending):
        """Test mark failed operation as resolved action."""
        failed_op = failed_operation_pending
        
        url = reverse("admin:shopping_failedoperation_changelist")
        response = admin_client.post(url, {
            "action": "mark_as_resolved",
            "_selected_action": [failed_op.pk],
        }, follow=True)
        
        assert response.status_code == 200
        failed_op.refresh_from_db()
        assert failed_op.status == "resolved"

    def test_mark_as_rejected_action(self, admin_client, failed_operation_pending):
        """Test mark failed operation as rejected action."""
        failed_op = failed_operation_pending
        
        url = reverse("admin:shopping_failedoperation_changelist")
        response = admin_client.post(url, {
            "action": "mark_as_rejected",
            "_selected_action": [failed_op.pk],
        }, follow=True)
        
        assert response.status_code == 200
        failed_op.refresh_from_db()
        assert failed_op.status == "rejected"


@pytest.mark.django_db
class TestAdminActionPermissions:
    """Tests for admin action permission checks."""

    def test_non_admin_cannot_access_changelist(self, client):
        """Verify non-admin users cannot access admin changelist."""
        regular_user = UserFactory(is_staff=False)
        client.force_login(regular_user)
        
        url = reverse("admin:shopping_payment_changelist")
        response = client.get(url)
        
        # Should redirect to admin login
        assert response.status_code == 302
        assert "/admin/login/" in response.url

    def test_staff_without_permission_limited_access(self, client, db):
        """Test staff user without specific permissions has limited access."""
        staff_user = UserFactory(is_staff=True, is_superuser=False)
        client.force_login(staff_user)
        
        url = reverse("admin:index")
        response = client.get(url)
        
        # Should be able to access admin index but with limited options
        assert response.status_code == 200
