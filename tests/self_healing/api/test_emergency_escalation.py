"""
Tests for Emergency Escalation Permission (Break Glass Pattern).

Tests the one-way emergency escalation for Self-Healing API:
- Operator can escalate to STRICT mode (emergency) with reason required
- Only Admin can restore to NORMAL mode
- Proper audit logging for emergency actions
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
        request.data = {"mode": "STRICT", "reason": "test"}
        view = Mock()

        assert permission.has_permission(request, view) is False

    def test_unauthenticated_anonymous_user_denied(self):
        """Anonymous user should be denied."""
        permission = EmergencyEscalationPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = False
        request.data = {"mode": "STRICT", "reason": "test"}
        view = Mock()

        assert permission.has_permission(request, view) is False


class TestEmergencyEscalationToStrict:
    """Tests for STRICT mode escalation (Operator allowed + reason required)."""

    def test_operator_can_escalate_to_strict_with_reason(self):
        """Operator should be able to escalate to STRICT mode with reason."""
        permission = EmergencyEscalationPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = True
        request.data = {"mode": "STRICT", "reason": "High error rate detected"}
        view = Mock()

        assert permission.has_permission(request, view) is True

    def test_operator_cannot_escalate_to_strict_without_reason(self):
        """Operator should NOT be able to escalate to STRICT without reason."""
        permission = EmergencyEscalationPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = True
        request.data = {"mode": "STRICT"}  # No reason
        view = Mock()

        assert permission.has_permission(request, view) is False

    def test_strict_denied_with_empty_reason(self):
        """STRICT escalation should be denied with empty reason."""
        permission = EmergencyEscalationPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_superuser = True
        request.data = {"mode": "STRICT", "reason": ""}  # Empty reason
        view = Mock()

        assert permission.has_permission(request, view) is False

    def test_strict_denied_with_whitespace_only_reason(self):
        """STRICT escalation should be denied with whitespace-only reason."""
        permission = EmergencyEscalationPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_superuser = True
        request.data = {"mode": "STRICT", "reason": "   "}  # Whitespace only
        view = Mock()

        assert permission.has_permission(request, view) is False

    def test_admin_can_escalate_to_strict_with_reason(self):
        """Admin should be able to escalate to STRICT mode with reason."""
        permission = EmergencyEscalationPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_superuser = True
        request.data = {"mode": "STRICT", "reason": "Emergency maintenance"}
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
        request.data = {"mode": "STRICT", "reason": "test"}
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
        request.data = {"mode": "strict", "reason": "Test reason"}  # lowercase
        view = Mock()

        assert permission.has_permission(request, view) is True

    @patch("selfhealing.api.django.permissions.logger")
    def test_strict_escalation_logs_warning_with_reason(self, mock_logger):
        """STRICT escalation by operator should log a warning with reason."""
        permission = EmergencyEscalationPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = True
        request.data = {"mode": "STRICT", "reason": "High error rate"}
        view = Mock()

        permission.has_permission(request, view)

        mock_logger.warning.assert_called()
        call_args = mock_logger.warning.call_args[0][0]
        assert "Emergency escalation to STRICT" in call_args
        assert "reason=" in call_args

    @patch("selfhealing.api.django.permissions.logger")
    def test_strict_without_reason_logs_warning(self, mock_logger):
        """STRICT escalation without reason should log a denial warning."""
        permission = EmergencyEscalationPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_superuser = True
        request.data = {"mode": "STRICT"}  # No reason
        view = Mock()

        result = permission.has_permission(request, view)

        assert result is False
        mock_logger.warning.assert_called()
        call_args = mock_logger.warning.call_args[0][0]
        assert "reason required" in call_args


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
            request.data = {"mode": "STRICT", "reason": "Test reason"}
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

    def test_reason_required_message_on_strict_without_reason(self):
        """Permission message should indicate reason is required for STRICT."""
        permission = EmergencyEscalationPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_superuser = True
        request.data = {"mode": "STRICT"}  # No reason
        view = Mock()

        permission.has_permission(request, view)

        # After failed check, message should be updated
        assert "reason" in permission.message.lower()
        assert "필수" in permission.message or "required" in permission.message.lower()


class TestEmergencyTrackerIntegration:
    """Tests for EmergencyModeTracker integration."""

    @patch("selfhealing.services.governance.get_emergency_tracker")
    def test_strict_mode_activates_tracker(self, mock_get_tracker):
        """STRICT mode change should activate EmergencyModeTracker."""
        from selfhealing.services.governance_api_service import GovernanceApiService

        mock_tracker = Mock()
        mock_tracker.record_emergency_activation.return_value = {
            "status": "activated",
            "expiry_hours": 8,
        }
        mock_get_tracker.return_value = mock_tracker

        service = GovernanceApiService()

        with patch.object(service, "_log_mode_change"):
            with patch("selfhealing.metrics.reliability_manager.get_reliability_manager") as mock_manager:
                mock_manager.return_value.get_global_mode.return_value = Mock(value="normal")
                mock_manager.return_value.force_global_mode.return_value = None

                result = service.set_mode("STRICT", actor="test_operator", reason="Test")

        mock_tracker.record_emergency_activation.assert_called_once_with(
            activated_by="test_operator",
            reason="Test",
            mode="STRICT",
        )
        assert "expires_at" in result

    @patch("selfhealing.services.governance.get_emergency_tracker")
    def test_normal_mode_deactivates_tracker_from_strict(self, mock_get_tracker):
        """NORMAL mode from STRICT should deactivate EmergencyModeTracker."""
        from selfhealing.services.governance_api_service import GovernanceApiService

        mock_tracker = Mock()
        mock_tracker.record_normal_restoration.return_value = {
            "status": "restored",
        }
        mock_get_tracker.return_value = mock_tracker

        service = GovernanceApiService()

        with patch.object(service, "_log_mode_change"):
            with patch("selfhealing.metrics.reliability_manager.get_reliability_manager") as mock_manager:
                # Previous mode was STRICT
                mock_manager.return_value.get_global_mode.return_value = Mock(value="strict")
                mock_manager.return_value.force_global_mode.return_value = None

                service.set_mode("NORMAL", actor="test_admin", reason="Recovery")

        mock_tracker.record_normal_restoration.assert_called_once_with(
            restored_by="test_admin",
            reason="Recovery",
        )
