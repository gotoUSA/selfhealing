"""
Tests for Emergency Escalation Permission (Break Glass Pattern).

Tests the one-way emergency escalation for Self-Healing API:
- Operator can escalate to STRICT mode (emergency)
- Only Admin can restore to NORMAL mode
- Proper audit logging for emergency actions

Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1.md
"""
import os
import sys

# Django setup before any Django imports
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django
django.setup()

import pytest
from unittest.mock import Mock, patch

from selfhealing.api.django.permissions import EmergencyEscalationPermission


class TestEmergencyEscalationPermission:
    """Tests for EmergencyEscalationPermission class."""

    def test_unauthenticated_user_denied(self):
        """Unauthenticated user should be denied for any mode change."""
        permission = EmergencyEscalationPermission()
        request = Mock()
        request.user = None
        request.data = {"mode": "STRICT"}
        view = Mock()

        assert permission.has_permission(request, view) is False

    def test_unauthenticated_anonymous_user_denied(self):
        """Anonymous user should be denied."""
        permission = EmergencyEscalationPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = False
        request.data = {"mode": "STRICT"}
        view = Mock()

        assert permission.has_permission(request, view) is False


class TestEmergencyEscalationToStrict:
    """Tests for STRICT mode escalation (Operator allowed)."""

    def test_operator_can_escalate_to_strict(self):
        """Operator should be able to escalate to STRICT mode."""
        permission = EmergencyEscalationPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = True
        request.data = {"mode": "STRICT"}
        view = Mock()

        assert permission.has_permission(request, view) is True

    def test_admin_can_escalate_to_strict(self):
        """Admin should be able to escalate to STRICT mode."""
        permission = EmergencyEscalationPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_superuser = True
        request.data = {"mode": "STRICT"}
        view = Mock()

        assert permission.has_permission(request, view) is True

    def test_viewer_cannot_escalate_to_strict(self):
        """Viewer should NOT be able to escalate to STRICT mode."""
        permission = EmergencyEscalationPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        # Viewer only - not in operator/admin groups
        request.user.groups.filter.return_value.exists.return_value = False
        request.data = {"mode": "STRICT"}
        view = Mock()

        assert permission.has_permission(request, view) is False

    def test_strict_mode_case_insensitive(self):
        """Mode check should be case insensitive."""
        permission = EmergencyEscalationPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = True
        request.user.is_superuser = True
        request.data = {"mode": "strict"}  # lowercase
        view = Mock()

        assert permission.has_permission(request, view) is True

    @patch("selfhealing.api.django.permissions.logger")
    def test_strict_escalation_logs_warning(self, mock_logger):
        """STRICT escalation by operator should log a warning."""
        permission = EmergencyEscalationPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = True
        request.data = {"mode": "STRICT"}
        view = Mock()

        permission.has_permission(request, view)

        mock_logger.warning.assert_called()
        call_args = mock_logger.warning.call_args[0][0]
        assert "Emergency escalation to STRICT" in call_args


class TestEmergencyRestoreToNormal:
    """Tests for NORMAL mode restoration (Admin only)."""

    def test_admin_can_restore_to_normal(self):
        """Admin should be able to restore to NORMAL mode."""
        permission = EmergencyEscalationPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_superuser = True
        request.data = {"mode": "NORMAL"}
        view = Mock()

        assert permission.has_permission(request, view) is True

    def test_admin_group_can_restore_to_normal(self):
        """User in selfhealing_admin group should restore to NORMAL."""
        permission = EmergencyEscalationPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = True
        request.data = {"mode": "NORMAL"}
        view = Mock()

        assert permission.has_permission(request, view) is True

    def test_operator_cannot_restore_to_normal(self):
        """Operator should NOT be able to restore to NORMAL mode."""
        permission = EmergencyEscalationPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        # Operator only - not admin
        request.user.groups.filter.return_value.exists.return_value = False
        request.data = {"mode": "NORMAL"}
        view = Mock()

        assert permission.has_permission(request, view) is False

    def test_viewer_cannot_restore_to_normal(self):
        """Viewer should NOT be able to restore to NORMAL mode."""
        permission = EmergencyEscalationPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = False
        request.data = {"mode": "NORMAL"}
        view = Mock()

        assert permission.has_permission(request, view) is False


class TestEmergencyOtherModes:
    """Tests for other mode changes (Admin only)."""

    def test_admin_can_change_to_other_modes(self):
        """Admin should be able to change to other modes."""
        permission = EmergencyEscalationPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_superuser = True
        request.data = {"mode": "MAINTENANCE"}
        view = Mock()

        assert permission.has_permission(request, view) is True

    def test_operator_cannot_change_to_other_modes(self):
        """Operator should NOT be able to change to other modes."""
        permission = EmergencyEscalationPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = False
        request.data = {"mode": "MAINTENANCE"}
        view = Mock()

        assert permission.has_permission(request, view) is False

    def test_empty_mode_requires_admin(self):
        """Empty mode should require Admin permission."""
        permission = EmergencyEscalationPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = False
        request.data = {"mode": ""}
        view = Mock()

        assert permission.has_permission(request, view) is False

    def test_missing_mode_requires_admin(self):
        """Missing mode field should require Admin permission."""
        permission = EmergencyEscalationPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = False
        request.data = {}
        view = Mock()

        assert permission.has_permission(request, view) is False


class TestEmergencyExpiryHours:
    """Tests for emergency expiry configuration."""

    def test_default_expiry_hours(self):
        """Default expiry should be 4 hours."""
        permission = EmergencyEscalationPermission()
        assert permission.EMERGENCY_EXPIRY_HOURS == 4

    def test_expiry_hours_in_warning_log(self):
        """Expiry hours should be included in warning log."""
        with patch("selfhealing.api.django.permissions.logger") as mock_logger:
            permission = EmergencyEscalationPermission()
            request = Mock()
            request.user = Mock()
            request.user.is_authenticated = True
            request.user.is_staff = False
            request.user.is_superuser = False
            request.user.groups.filter.return_value.exists.return_value = True
            request.data = {"mode": "STRICT"}
            view = Mock()

            permission.has_permission(request, view)

            call_args = mock_logger.warning.call_args[0][0]
            assert "expiry_hours=4" in call_args


class TestEmergencyPermissionMessage:
    """Tests for permission error messages."""

    def test_permission_message_includes_both_modes(self):
        """Permission message should explain both STRICT and NORMAL rules."""
        permission = EmergencyEscalationPermission()
        assert "STRICT" in permission.message
        assert "NORMAL" in permission.message
        assert "Operator" in permission.message
        assert "Admin" in permission.message
