"""
Tests for Admin access permissions.

Verifies that non-admin and limited-permission staff users are handled correctly.
"""

import pytest
from django.urls import reverse

from shopping.tests.factories import (
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
