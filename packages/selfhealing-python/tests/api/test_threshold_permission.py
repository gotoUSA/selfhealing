"""
Tests for Threshold-Based Permission (Risk-Based Access Control).

Tests the dynamic permission levels based on discrepancy rate:
- <= 15%: Operator approval
- <= 30%: Admin approval
- > 30%: Admin + warning log (4-Eyes recommended)

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

from selfhealing.api.django.permissions import ThresholdBasedPermission


class TestThresholdBasedPermission:
    """Tests for ThresholdBasedPermission class."""

    def test_unauthenticated_user_denied(self):
        """Unauthenticated user should be denied."""
        permission = ThresholdBasedPermission()
        request = Mock()
        request.user = None
        request.data = {"discrepancy_rate": 0.05}
        view = Mock()

        assert permission.has_permission(request, view) is False

    def test_unauthenticated_anonymous_user_denied(self):
        """Anonymous user should be denied."""
        permission = ThresholdBasedPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = False
        request.data = {"discrepancy_rate": 0.05}
        view = Mock()

        assert permission.has_permission(request, view) is False


class TestLowRiskThreshold:
    """Tests for low-risk operations (<= 15% discrepancy)."""

    def test_operator_can_approve_low_risk(self):
        """Operator should be able to approve <= 15% discrepancy."""
        permission = ThresholdBasedPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = True
        request.data = {"discrepancy_rate": 0.10}  # 10%
        view = Mock()

        assert permission.has_permission(request, view) is True

    def test_operator_can_approve_at_threshold(self):
        """Operator should be able to approve exactly at 15%."""
        permission = ThresholdBasedPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = True
        request.data = {"discrepancy_rate": 0.15}  # exactly 15%
        view = Mock()

        assert permission.has_permission(request, view) is True

    def test_admin_can_approve_low_risk(self):
        """Admin should be able to approve low-risk operations."""
        permission = ThresholdBasedPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_superuser = True
        request.data = {"discrepancy_rate": 0.05}  # 5%
        view = Mock()

        assert permission.has_permission(request, view) is True

    def test_viewer_cannot_approve_low_risk(self):
        """Viewer should NOT be able to approve even low-risk operations."""
        permission = ThresholdBasedPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = False
        request.data = {"discrepancy_rate": 0.05}
        view = Mock()

        assert permission.has_permission(request, view) is False


class TestMediumRiskThreshold:
    """Tests for medium-risk operations (15% < discrepancy <= 30%)."""

    def test_admin_can_approve_medium_risk(self):
        """Admin should be able to approve 15-30% discrepancy."""
        permission = ThresholdBasedPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_superuser = True
        request.data = {"discrepancy_rate": 0.25}  # 25%
        view = Mock()

        assert permission.has_permission(request, view) is True

    def test_admin_group_can_approve_medium_risk(self):
        """User in selfhealing_admin group can approve medium-risk."""
        permission = ThresholdBasedPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = True
        request.data = {"discrepancy_rate": 0.20}
        view = Mock()

        assert permission.has_permission(request, view) is True

    def test_operator_cannot_approve_medium_risk(self):
        """Operator should NOT be able to approve > 15% discrepancy."""
        permission = ThresholdBasedPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        # First call returns True (operator check), 
        # Second call returns False (admin check)
        request.user.groups.filter.return_value.exists.side_effect = [False, False]
        request.data = {"discrepancy_rate": 0.20}  # 20%
        view = Mock()

        assert permission.has_permission(request, view) is False


class TestHighRiskThreshold:
    """Tests for high-risk operations (> 30% discrepancy)."""

    def test_admin_can_approve_high_risk_with_warning(self):
        """Admin can approve > 30% but with warning log."""
        with patch("selfhealing.api.django.permissions.logger") as mock_logger:
            permission = ThresholdBasedPermission()
            request = Mock()
            request.user = Mock()
            request.user.is_authenticated = True
            request.user.is_superuser = True
            request.data = {"discrepancy_rate": 0.50}  # 50%
            view = Mock()

            result = permission.has_permission(request, view)

            assert result is True
            mock_logger.warning.assert_called()
            call_args = mock_logger.warning.call_args[0][0]
            assert "High-risk operation" in call_args
            assert "50.0%" in call_args

    def test_operator_cannot_approve_high_risk(self):
        """Operator should NOT be able to approve > 30% discrepancy."""
        permission = ThresholdBasedPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = False
        request.data = {"discrepancy_rate": 0.50}  # 50%
        view = Mock()

        assert permission.has_permission(request, view) is False

    def test_very_high_risk_requires_admin(self):
        """Very high discrepancy (e.g., 100%) still requires Admin."""
        permission = ThresholdBasedPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_superuser = True
        request.data = {"discrepancy_rate": 1.0}  # 100%
        view = Mock()

        assert permission.has_permission(request, view) is True


class TestThresholdEdgeCases:
    """Tests for edge cases in threshold checking."""

    def test_zero_discrepancy(self):
        """Zero discrepancy should be approved by Operator."""
        permission = ThresholdBasedPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = True
        request.data = {"discrepancy_rate": 0}
        view = Mock()

        assert permission.has_permission(request, view) is True

    def test_missing_discrepancy_defaults_to_zero(self):
        """Missing discrepancy_rate should default to 0."""
        permission = ThresholdBasedPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = True
        request.data = {}  # No discrepancy_rate
        view = Mock()

        assert permission.has_permission(request, view) is True

    def test_invalid_discrepancy_string(self):
        """Invalid discrepancy string should default to 0."""
        permission = ThresholdBasedPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = True
        request.data = {"discrepancy_rate": "invalid"}
        view = Mock()

        assert permission.has_permission(request, view) is True

    def test_none_discrepancy(self):
        """None discrepancy should default to 0."""
        permission = ThresholdBasedPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = True
        request.data = {"discrepancy_rate": None}
        view = Mock()

        assert permission.has_permission(request, view) is True

    def test_negative_discrepancy(self):
        """Negative discrepancy should be treated as low-risk."""
        permission = ThresholdBasedPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = True
        request.data = {"discrepancy_rate": -0.1}
        view = Mock()

        assert permission.has_permission(request, view) is True

    def test_string_number_discrepancy(self):
        """String number discrepancy should be parsed correctly."""
        permission = ThresholdBasedPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_superuser = True
        request.data = {"discrepancy_rate": "0.25"}  # string "0.25"
        view = Mock()

        assert permission.has_permission(request, view) is True


class TestThresholdEnvironmentOverride:
    """Tests for environment variable threshold override."""

    def test_default_thresholds(self):
        """Default thresholds should be 0.15 and 0.30."""
        permission = ThresholdBasedPermission()
        assert permission.thresholds["operator_approve"] == 0.15
        assert permission.thresholds["admin_approve"] == 0.30

    @patch.dict(os.environ, {"SELFHEALING_THRESHOLD_OPERATOR": "0.10"})
    def test_operator_threshold_override(self):
        """Operator threshold should be overridable via env var."""
        permission = ThresholdBasedPermission()
        assert permission.thresholds["operator_approve"] == 0.10

    @patch.dict(os.environ, {"SELFHEALING_THRESHOLD_ADMIN": "0.50"})
    def test_admin_threshold_override(self):
        """Admin threshold should be overridable via env var."""
        permission = ThresholdBasedPermission()
        assert permission.thresholds["admin_approve"] == 0.50

    @patch.dict(
        os.environ,
        {
            "SELFHEALING_THRESHOLD_OPERATOR": "0.05",
            "SELFHEALING_THRESHOLD_ADMIN": "0.20",
        },
    )
    def test_both_thresholds_override(self):
        """Both thresholds should be overridable."""
        permission = ThresholdBasedPermission()
        assert permission.thresholds["operator_approve"] == 0.05
        assert permission.thresholds["admin_approve"] == 0.20


class TestThresholdPermissionMessage:
    """Tests for permission error messages."""

    def test_permission_message_exists(self):
        """Permission should have a descriptive message."""
        permission = ThresholdBasedPermission()
        assert "임계값" in permission.message or "threshold" in permission.message.lower()
