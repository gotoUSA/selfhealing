"""
Tests for Threshold-Based Permission (Risk-Based Access Control).

Tests the dynamic permission levels based on discrepancy rate:
- <= 15%: Operator approval
- <= 30%: Admin approval
- 30% ~ 50%: Admin + warning log
- > 50%: 4-Eyes Dual Approval Required

Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1.md
"""

import os
import sys

# Django setup before any Django imports
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django

django.setup()

import pytest
from unittest.mock import Mock, patch, MagicMock

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
    """Tests for high-risk operations (30% < discrepancy <= 50%)."""

    def test_admin_can_approve_high_risk_with_warning(self):
        """Admin can approve 30-50% with warning log (no dual approval needed)."""
        with patch("selfhealing.api.django.permissions.logger") as mock_logger:
            permission = ThresholdBasedPermission()
            request = Mock()
            request.user = Mock()
            request.user.is_authenticated = True
            request.user.is_superuser = True
            request.data = {"discrepancy_rate": 0.40}  # 40% (within 30-50% range)
            view = Mock()

            result = permission.has_permission(request, view)

            assert result is True
            mock_logger.warning.assert_called()
            call_args = mock_logger.warning.call_args[0][0]
            assert "High-risk operation" in call_args
            assert "40.0%" in call_args

    def test_admin_can_approve_at_50_percent(self):
        """Admin can approve exactly 50% with warning log."""
        with patch("selfhealing.api.django.permissions.logger") as mock_logger:
            permission = ThresholdBasedPermission()
            request = Mock()
            request.user = Mock()
            request.user.is_authenticated = True
            request.user.is_superuser = True
            request.data = {"discrepancy_rate": 0.50}  # exactly 50%
            view = Mock()

            result = permission.has_permission(request, view)

            assert result is True
            mock_logger.warning.assert_called()

    def test_operator_cannot_approve_high_risk(self):
        """Operator should NOT be able to approve > 30% discrepancy."""
        permission = ThresholdBasedPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = False
        request.data = {"discrepancy_rate": 0.40}  # 40%
        view = Mock()

        assert permission.has_permission(request, view) is False


class TestDualApprovalRequired:
    """Tests for dual approval requirement (> 50% discrepancy)."""

    def test_admin_denied_without_approval_id(self):
        """Admin should be denied for > 50% without approval_id."""
        with patch("selfhealing.api.django.permissions.logger"):
            permission = ThresholdBasedPermission()
            request = Mock()
            request.user = Mock()
            request.user.is_authenticated = True
            request.user.is_superuser = True
            request.user.username = "admin1"
            request.data = {"discrepancy_rate": 0.60}  # 60% - requires dual approval
            request.path = "/api/test/"
            request.META = {}
            view = Mock()

            with patch.object(permission, "_notify_dual_approval_needed"):
                result = permission.has_permission(request, view)

            assert result is False
            assert "듀얼 승인" in permission.message or "4-Eyes" in permission.message

    def test_admin_approved_with_valid_approval_id(self):
        """Admin should be approved for > 50% with valid approved approval_id."""
        with patch("selfhealing.api.django.permissions.logger") as mock_logger:
            with patch("selfhealing.services.runtime_config.get_runtime_config_manager") as mock_manager:
                manager_instance = MagicMock()
                mock_manager.return_value = manager_instance
                manager_instance.get_governance_config.return_value = {
                    "threshold_operator": 0.15,
                    "threshold_admin": 0.30,
                    "threshold_dual_approval": 0.50,
                }
                manager_instance.get_approval_requests.return_value = [
                    {
                        "id": "approval-123",
                        "status": "APPROVED",
                        "requested_by": "admin1",
                        "approved_by": "admin2",
                    }
                ]

                permission = ThresholdBasedPermission()
                request = Mock()
                request.user = Mock()
                request.user.is_authenticated = True
                request.user.is_superuser = True
                request.user.username = "admin1"
                request.data = {
                    "discrepancy_rate": 0.60,
                    "approval_id": "approval-123",
                }
                view = Mock()

                result = permission.has_permission(request, view)

                assert result is True
                mock_logger.info.assert_called()
                call_args = mock_logger.info.call_args[0][0]
                assert "Dual approval verified" in call_args

    def test_admin_denied_with_pending_approval_id(self):
        """Admin should be denied for > 50% with PENDING approval_id."""
        with patch("selfhealing.services.runtime_config.get_runtime_config_manager") as mock_manager:
            manager_instance = MagicMock()
            mock_manager.return_value = manager_instance
            manager_instance.get_governance_config.return_value = {
                "threshold_operator": 0.15,
                "threshold_admin": 0.30,
                "threshold_dual_approval": 0.50,
            }
            manager_instance.get_approval_requests.return_value = [
                {
                    "id": "approval-123",
                    "status": "PENDING",
                    "requested_by": "admin1",
                    "approved_by": "",
                }
            ]

            permission = ThresholdBasedPermission()
            request = Mock()
            request.user = Mock()
            request.user.is_authenticated = True
            request.user.is_superuser = True
            request.user.username = "admin1"
            request.data = {
                "discrepancy_rate": 0.60,
                "approval_id": "approval-123",
            }
            view = Mock()

            result = permission.has_permission(request, view)

            assert result is False
            assert "PENDING" in permission.message

    def test_admin_denied_with_nonexistent_approval_id(self):
        """Admin should be denied for > 50% with nonexistent approval_id."""
        with patch("selfhealing.services.runtime_config.get_runtime_config_manager") as mock_manager:
            manager_instance = MagicMock()
            mock_manager.return_value = manager_instance
            manager_instance.get_governance_config.return_value = {
                "threshold_operator": 0.15,
                "threshold_admin": 0.30,
                "threshold_dual_approval": 0.50,
            }
            manager_instance.get_approval_requests.return_value = []

            permission = ThresholdBasedPermission()
            request = Mock()
            request.user = Mock()
            request.user.is_authenticated = True
            request.user.is_superuser = True
            request.user.username = "admin1"
            request.data = {
                "discrepancy_rate": 0.60,
                "approval_id": "nonexistent",
            }
            view = Mock()

            result = permission.has_permission(request, view)

            assert result is False
            assert "찾을 수 없습니다" in permission.message

    def test_operator_cannot_approve_very_high_risk(self):
        """Operator should NOT be able to approve > 50% discrepancy."""
        permission = ThresholdBasedPermission()
        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = True
        request.user.is_staff = False
        request.user.is_superuser = False
        request.user.groups.filter.return_value.exists.return_value = False
        request.data = {"discrepancy_rate": 0.70}  # 70%
        view = Mock()

        assert permission.has_permission(request, view) is False

    def test_very_high_risk_100_percent_requires_dual_approval(self):
        """100% discrepancy should require dual approval."""
        with patch("selfhealing.api.django.permissions.logger"):
            permission = ThresholdBasedPermission()
            request = Mock()
            request.user = Mock()
            request.user.is_authenticated = True
            request.user.is_superuser = True
            request.user.username = "admin1"
            request.data = {"discrepancy_rate": 1.0}  # 100%
            request.path = "/api/test/"
            request.META = {}
            view = Mock()

            with patch.object(permission, "_notify_dual_approval_needed"):
                result = permission.has_permission(request, view)

            assert result is False  # 이제 듀얼 승인 없이는 거부

    def test_notification_sent_when_dual_approval_required(self):
        """Notification should be sent when dual approval is required."""
        with patch("selfhealing.api.django.permissions.logger"):
            with patch("selfhealing.services.security_notification_service.SecurityNotificationService") as mock_service_class:
                mock_service = MagicMock()
                mock_service.config.enabled = True
                mock_service_class.return_value = mock_service

                permission = ThresholdBasedPermission()
                request = Mock()
                request.user = Mock()
                request.user.is_authenticated = True
                request.user.is_superuser = True
                request.user.username = "admin1"
                request.data = {"discrepancy_rate": 0.60}
                request.path = "/api/test/"
                request.META = {"REMOTE_ADDR": "192.168.1.1"}
                view = Mock()

                permission.has_permission(request, view)

                mock_service.notify_security_incident_by_id.assert_called_once()
                call_kwargs = mock_service.notify_security_incident_by_id.call_args[1]
                assert call_kwargs["incident_type"] == "dual_approval_required"
                assert call_kwargs["severity"] == "high"
                assert "admin1" in call_kwargs["description"]


class TestDualApprovalThresholdConfig:
    """Tests for dual approval threshold configuration."""

    @patch("selfhealing.services.runtime_config.get_runtime_config_manager")
    def test_default_dual_approval_threshold(self, mock_manager):
        """Default dual approval threshold should be 0.50."""
        mock_manager.side_effect = Exception("Not configured")
        permission = ThresholdBasedPermission()
        assert permission.thresholds["dual_approval"] == 0.50

    @patch("selfhealing.services.runtime_config.get_runtime_config_manager")
    @patch.dict(os.environ, {"SELFHEALING_THRESHOLD_DUAL_APPROVAL": "0.30"})
    def test_dual_approval_threshold_override(self, mock_manager):
        """Dual approval threshold should be overridable via env var."""
        mock_manager.side_effect = Exception("Not configured")
        permission = ThresholdBasedPermission()
        assert permission.thresholds["dual_approval"] == 0.30

    def test_runtime_config_dual_approval_threshold(self):
        """Dual approval threshold should be configurable via RuntimeConfigManager."""
        with patch("selfhealing.services.runtime_config.get_runtime_config_manager") as mock_manager:
            manager_instance = MagicMock()
            mock_manager.return_value = manager_instance
            manager_instance.get_governance_config.return_value = {
                "threshold_operator": 0.10,
                "threshold_admin": 0.25,
                "threshold_dual_approval": 0.40,
            }

            permission = ThresholdBasedPermission()
            thresholds = permission.thresholds

            assert thresholds["operator_approve"] == 0.10
            assert thresholds["admin_approve"] == 0.25
            assert thresholds["dual_approval"] == 0.40


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

    @patch("selfhealing.services.runtime_config.get_runtime_config_manager")
    def test_default_thresholds(self, mock_manager):
        """Default thresholds should be 0.15, 0.30, and 0.50."""
        # RuntimeConfigManager가 예외를 발생시켜 환경변수/기본값 fallback 사용
        mock_manager.side_effect = Exception("Not configured")
        permission = ThresholdBasedPermission()
        assert permission.thresholds["operator_approve"] == 0.15
        assert permission.thresholds["admin_approve"] == 0.30
        assert permission.thresholds["dual_approval"] == 0.50

    @patch("selfhealing.services.runtime_config.get_runtime_config_manager")
    @patch.dict(os.environ, {"SELFHEALING_THRESHOLD_OPERATOR": "0.10"})
    def test_operator_threshold_override(self, mock_manager):
        """Operator threshold should be overridable via env var."""
        mock_manager.side_effect = Exception("Not configured")
        permission = ThresholdBasedPermission()
        assert permission.thresholds["operator_approve"] == 0.10

    @patch("selfhealing.services.runtime_config.get_runtime_config_manager")
    @patch.dict(os.environ, {"SELFHEALING_THRESHOLD_ADMIN": "0.50"})
    def test_admin_threshold_override(self, mock_manager):
        """Admin threshold should be overridable via env var."""
        mock_manager.side_effect = Exception("Not configured")
        permission = ThresholdBasedPermission()
        assert permission.thresholds["admin_approve"] == 0.50

    @patch("selfhealing.services.runtime_config.get_runtime_config_manager")
    @patch.dict(
        os.environ,
        {
            "SELFHEALING_THRESHOLD_OPERATOR": "0.05",
            "SELFHEALING_THRESHOLD_ADMIN": "0.20",
            "SELFHEALING_THRESHOLD_DUAL_APPROVAL": "0.40",
        },
    )
    def test_all_thresholds_override(self, mock_manager):
        """All thresholds should be overridable."""
        mock_manager.side_effect = Exception("Not configured")
        permission = ThresholdBasedPermission()
        assert permission.thresholds["operator_approve"] == 0.05
        assert permission.thresholds["admin_approve"] == 0.20
        assert permission.thresholds["dual_approval"] == 0.40


class TestThresholdPermissionMessage:
    """Tests for permission error messages."""

    def test_permission_message_exists(self):
        """Permission should have a descriptive message."""
        permission = ThresholdBasedPermission()
        assert "임계값" in permission.message or "threshold" in permission.message.lower()
