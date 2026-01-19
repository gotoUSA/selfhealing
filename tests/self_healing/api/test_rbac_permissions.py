"""
Tests for RBAC Permission Classes.

Tests the role-based access control for Self-Healing API:
- IsViewer: Read-only access
- IsOperator: DLQ operations
- IsSelfHealingAdmin: Full access including CB control
"""

import pytest
from unittest.mock import Mock, MagicMock, patch

# Import permission classes
from selfhealing.api.django.permissions import (
    IsViewer,
    IsOperator,
    IsSelfHealingAdmin,
)


class TestIsViewer:
    """Tests for IsViewer permission class."""

    def test_unauthenticated_user_denied(self):
        """Unauthenticated user should be denied."""
        permission = IsViewer()
        request = Mock()
        request.user = None
        view = Mock()

        assert permission.has_permission(request, view) is False

    def test_unauthenticated_anonymous_user_denied(self):
        """Anonymous user should be denied."""
        permission = IsViewer()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = False
        view = Mock()

        assert permission.has_permission(request, view) is False

    def test_staff_user_allowed(self):
        """Staff user should be allowed (inherits viewer access)."""
        permission = IsViewer()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = True
        view = Mock()

        assert permission.has_permission(request, view) is True

    def test_viewer_group_member_allowed(self):
        """User in selfhealing_viewer group should be allowed."""
        permission = IsViewer()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.groups.filter.return_value.exists.return_value = True
        view = Mock()

        assert permission.has_permission(request, view) is True
        request.user.groups.filter.assert_called_with(
            name__in=["selfhealing_viewer", "selfhealing_operator", "selfhealing_admin"]
        )

    def test_operator_group_member_allowed(self):
        """User in selfhealing_operator group should be allowed (inherits viewer)."""
        permission = IsViewer()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.groups.filter.return_value.exists.return_value = True
        view = Mock()

        assert permission.has_permission(request, view) is True

    def test_admin_group_member_allowed(self):
        """User in selfhealing_admin group should be allowed (inherits viewer)."""
        permission = IsViewer()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.groups.filter.return_value.exists.return_value = True
        view = Mock()

        assert permission.has_permission(request, view) is True

    def test_no_group_member_denied(self):
        """User not in any selfhealing group should be denied."""
        permission = IsViewer()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.groups.filter.return_value.exists.return_value = False
        view = Mock()

        assert permission.has_permission(request, view) is False


class TestIsOperator:
    """Tests for IsOperator permission class."""

    def test_unauthenticated_user_denied(self):
        """Unauthenticated user should be denied."""
        permission = IsOperator()
        request = Mock()
        request.user = None
        view = Mock()

        assert permission.has_permission(request, view) is False

    def test_superuser_allowed(self):
        """Superuser should be allowed."""
        permission = IsOperator()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = True
        request.user.is_superuser = True
        view = Mock()

        assert permission.has_permission(request, view) is True

    def test_staff_non_superuser_denied(self):
        """Staff but not superuser should check group membership."""
        permission = IsOperator()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = True
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = False
        view = Mock()

        assert permission.has_permission(request, view) is False

    def test_operator_group_member_allowed(self):
        """User in selfhealing_operator group should be allowed."""
        permission = IsOperator()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = True
        view = Mock()

        assert permission.has_permission(request, view) is True
        request.user.groups.filter.assert_called_with(
            name__in=["selfhealing_operator", "selfhealing_admin"]
        )

    def test_admin_group_member_allowed(self):
        """User in selfhealing_admin group should be allowed (inherits operator)."""
        permission = IsOperator()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = True
        view = Mock()

        assert permission.has_permission(request, view) is True

    def test_viewer_only_denied(self):
        """User in selfhealing_viewer only should be denied."""
        permission = IsOperator()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        # Simulating viewer-only user (not in operator/admin)
        request.user.groups.filter.return_value.exists.return_value = False
        view = Mock()

        assert permission.has_permission(request, view) is False


class TestIsSelfHealingAdmin:
    """Tests for IsSelfHealingAdmin permission class."""

    def test_unauthenticated_user_denied(self):
        """Unauthenticated user should be denied."""
        permission = IsSelfHealingAdmin()
        request = Mock()
        request.user = None
        view = Mock()

        assert permission.has_permission(request, view) is False

    def test_superuser_allowed(self):
        """Django superuser should always be allowed."""
        permission = IsSelfHealingAdmin()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_superuser = True
        view = Mock()

        assert permission.has_permission(request, view) is True

    def test_admin_group_member_allowed(self):
        """User in selfhealing_admin group should be allowed."""
        permission = IsSelfHealingAdmin()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = True
        view = Mock()

        assert permission.has_permission(request, view) is True
        request.user.groups.filter.assert_called_with(name="selfhealing_admin")

    def test_operator_only_denied(self):
        """User in selfhealing_operator only should be denied."""
        permission = IsSelfHealingAdmin()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = False
        view = Mock()

        assert permission.has_permission(request, view) is False

    def test_viewer_only_denied(self):
        """User in selfhealing_viewer only should be denied."""
        permission = IsSelfHealingAdmin()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = False
        view = Mock()

        assert permission.has_permission(request, view) is False

    def test_fail_secure_on_exception(self):
        """Permission check should fail-secure (deny) on exception."""
        permission = IsSelfHealingAdmin()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_superuser = False
        # Simulate exception during group check
        request.user.groups.filter.side_effect = Exception("Database error")
        view = Mock()

        # Should deny on error (fail-secure)
        assert permission.has_permission(request, view) is False


class TestPermissionHierarchy:
    """Tests for permission hierarchy (admin > operator > viewer)."""

    def test_admin_can_access_viewer_endpoints(self):
        """Admin should be able to access viewer-level endpoints."""
        viewer_perm = IsViewer()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        # Admin group includes viewer access
        request.user.groups.filter.return_value.exists.return_value = True
        view = Mock()

        assert viewer_perm.has_permission(request, view) is True

    def test_admin_can_access_operator_endpoints(self):
        """Admin should be able to access operator-level endpoints."""
        operator_perm = IsOperator()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        # Admin group includes operator access
        request.user.groups.filter.return_value.exists.return_value = True
        view = Mock()

        assert operator_perm.has_permission(request, view) is True

    def test_operator_can_access_viewer_endpoints(self):
        """Operator should be able to access viewer-level endpoints."""
        viewer_perm = IsViewer()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        # Operator group includes viewer access
        request.user.groups.filter.return_value.exists.return_value = True
        view = Mock()

        assert viewer_perm.has_permission(request, view) is True


class TestPermissionMessages:
    """Tests for permission error messages."""

    def test_viewer_permission_message(self):
        """Viewer permission should have descriptive message."""
        permission = IsViewer()
        assert "selfhealing_viewer" in permission.message

    def test_operator_permission_message(self):
        """Operator permission should have descriptive message."""
        permission = IsOperator()
        assert "selfhealing_operator" in permission.message

    def test_admin_permission_message(self):
        """Admin permission should have descriptive message."""
        permission = IsSelfHealingAdmin()
        assert "selfhealing_admin" in permission.message
