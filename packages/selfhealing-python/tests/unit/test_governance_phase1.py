"""
Tests for Governance Phase 1 Implementation.

테스트 대상:
1. GovernanceConfig 데이터클래스
2. RuntimeConfigManager governance 통합
3. ThresholdBasedPermission 런타임 설정 연동
4. EmergencyModeTracker
5. 자동 복귀 태스크

Reference:
- docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_ROADMAP.md
"""

import os
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, PropertyMock


# =============================================================================
# GovernanceConfig Tests
# =============================================================================


class TestGovernanceConfig:
    """Tests for GovernanceConfig dataclass."""

    def test_default_values(self):
        """Test default values are set correctly."""
        from selfhealing.core.config import GovernanceConfig

        config = GovernanceConfig()

        assert config.threshold_operator == 0.15
        assert config.threshold_admin == 0.30
        assert config.emergency_expiry_hours == 8
        assert config.emergency_warning_hours == 4
        assert config.emergency_final_warning_hours == 6
        assert config.default_mode == "NORMAL"
        assert config.notify_on_emergency is True
        assert "slack" in config.notify_channels
        assert "email" in config.notify_channels
        assert config.four_eyes_enabled is False
        assert config.four_eyes_expiry_hours == 24

    def test_custom_values(self):
        """Test custom values can be set."""
        from selfhealing.core.config import GovernanceConfig

        config = GovernanceConfig(
            threshold_operator=0.20,
            threshold_admin=0.40,
            emergency_expiry_hours=12,
            default_mode="STRICT",
        )

        assert config.threshold_operator == 0.20
        assert config.threshold_admin == 0.40
        assert config.emergency_expiry_hours == 12
        assert config.default_mode == "STRICT"

    def test_dataclass_conversion(self):
        """Test dataclass can be converted to dict."""
        from dataclasses import asdict
        from selfhealing.core.config import GovernanceConfig

        config = GovernanceConfig()
        config_dict = asdict(config)

        assert isinstance(config_dict, dict)
        assert "threshold_operator" in config_dict
        assert "threshold_admin" in config_dict
        assert "emergency_expiry_hours" in config_dict


# =============================================================================
# RuntimeConfigManager Governance Integration Tests
# =============================================================================


class TestRuntimeConfigManagerGovernance:
    """Tests for RuntimeConfigManager governance integration."""

    def test_governance_in_storage_keys(self):
        """Test governance is registered in STORAGE_KEYS."""
        from selfhealing.services.runtime_config.constants import STORAGE_KEYS

        assert "governance" in STORAGE_KEYS
        assert STORAGE_KEYS["governance"] == "runtime_config:governance"

    def test_governance_in_config_classes(self):
        """Test governance is registered in CONFIG_CLASSES."""
        from selfhealing.services.runtime_config.constants import CONFIG_CLASSES
        from selfhealing.core.config import GovernanceConfig

        assert "governance" in CONFIG_CLASSES
        assert CONFIG_CLASSES["governance"] == GovernanceConfig

    @patch("selfhealing.services.runtime_config.base.get_state_backend")
    def test_get_governance_config(self, mock_backend):
        """Test getting governance config."""
        mock_backend_instance = MagicMock()
        mock_backend_instance.get.return_value = None
        mock_backend.return_value = mock_backend_instance

        from selfhealing.services.runtime_config import RuntimeConfigManager

        manager = RuntimeConfigManager()
        config = manager.get_governance_config()

        assert isinstance(config, dict)
        assert "threshold_operator" in config
        assert "threshold_admin" in config
        assert config["threshold_operator"] == 0.15
        assert config["threshold_admin"] == 0.30

    @patch("selfhealing.services.runtime_config.base.get_state_backend")
    def test_update_governance_config(self, mock_backend):
        """Test updating governance config."""
        mock_backend_instance = MagicMock()
        mock_backend_instance.get.return_value = None
        mock_backend.return_value = mock_backend_instance

        from selfhealing.services.runtime_config import RuntimeConfigManager

        manager = RuntimeConfigManager()
        result = manager.update_governance_config(
            threshold_operator=0.20,
            threshold_admin=0.40,
        )

        assert result["threshold_operator"] == 0.20
        assert result["threshold_admin"] == 0.40

    @patch("selfhealing.services.runtime_config.base.get_state_backend")
    def test_update_governance_config_validation(self, mock_backend):
        """Test governance config validation."""
        mock_backend_instance = MagicMock()
        mock_backend_instance.get.return_value = None
        mock_backend.return_value = mock_backend_instance

        from selfhealing.services.runtime_config import RuntimeConfigManager

        manager = RuntimeConfigManager()

        # Invalid threshold (> 1.0)
        with pytest.raises(ValueError, match="threshold_operator must be between"):
            manager.update_governance_config(threshold_operator=1.5)

        # Invalid threshold (< 0.0)
        with pytest.raises(ValueError, match="threshold_admin must be between"):
            manager.update_governance_config(threshold_admin=-0.1)

        # Invalid mode
        with pytest.raises(ValueError, match="default_mode must be"):
            manager.update_governance_config(default_mode="INVALID")


# =============================================================================
# ThresholdBasedPermission Runtime Config Tests
# =============================================================================


@pytest.mark.skipif(
    not os.environ.get("DJANGO_SETTINGS_MODULE"),
    reason="Requires Django settings to be configured"
)
class TestThresholdBasedPermissionRuntimeConfig:
    """Tests for ThresholdBasedPermission runtime config integration."""

    def test_get_thresholds_from_runtime_config(self):
        """Test thresholds are fetched from RuntimeConfigManager."""
        from selfhealing.api.django.permissions import ThresholdBasedPermission

        with patch("selfhealing.services.runtime_config.get_runtime_config_manager") as mock_manager:
            mock_manager_instance = MagicMock()
            mock_manager_instance.get_governance_config.return_value = {
                "threshold_operator": 0.25,
                "threshold_admin": 0.50,
            }
            mock_manager.return_value = mock_manager_instance

            permission = ThresholdBasedPermission()
            thresholds = permission._get_thresholds()

            assert thresholds["operator_approve"] == 0.25
            assert thresholds["admin_approve"] == 0.50

    def test_fallback_to_env_variables(self):
        """Test fallback to environment variables when RuntimeConfigManager fails."""
        from selfhealing.api.django.permissions import ThresholdBasedPermission

        with patch("selfhealing.services.runtime_config.get_runtime_config_manager") as mock_manager:
            mock_manager.side_effect = Exception("Connection error")

            with patch.dict(os.environ, {
                "SELFHEALING_THRESHOLD_OPERATOR": "0.18",
                "SELFHEALING_THRESHOLD_ADMIN": "0.35",
            }):
                permission = ThresholdBasedPermission()
                thresholds = permission._get_thresholds()

                assert thresholds["operator_approve"] == 0.18
                assert thresholds["admin_approve"] == 0.35

    def test_thresholds_property_fetches_fresh_values(self):
        """Test thresholds property fetches fresh values each time."""
        from selfhealing.api.django.permissions import ThresholdBasedPermission

        permission = ThresholdBasedPermission()

        # Verify property calls _get_thresholds
        with patch.object(permission, "_get_thresholds") as mock_get:
            mock_get.return_value = {"operator_approve": 0.10, "admin_approve": 0.20}
            _ = permission.thresholds
            mock_get.assert_called_once()


# =============================================================================
# EmergencyModeTracker Tests
# =============================================================================


class TestEmergencyModeTracker:
    """Tests for EmergencyModeTracker."""

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_initial_state_is_normal(self, mock_backend):
        """Test initial state is NORMAL mode."""
        mock_backend_instance = MagicMock()
        mock_backend_instance.get.return_value = None
        mock_backend.return_value = mock_backend_instance

        from selfhealing.services.governance import EmergencyModeTracker

        tracker = EmergencyModeTracker()
        state = tracker.get_current_state()

        assert state.is_active is False
        assert state.mode == "NORMAL"
        assert state.activated_at is None
        assert state.activated_by is None

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_record_emergency_activation(self, mock_backend):
        """Test recording emergency mode activation."""
        mock_backend_instance = MagicMock()
        mock_backend_instance.get.return_value = None
        mock_backend.return_value = mock_backend_instance

        from selfhealing.services.governance import EmergencyModeTracker

        tracker = EmergencyModeTracker()
        result = tracker.record_emergency_activation(
            activated_by="operator_kim",
            reason="High error rate",
        )

        assert result["status"] == "activated"
        assert result["mode"] == "STRICT"
        assert result["activated_by"] == "operator_kim"

        state = tracker.get_current_state()
        assert state.is_active is True
        assert state.mode == "STRICT"
        assert state.activated_by == "operator_kim"
        assert state.reason == "High error rate"

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_record_normal_restoration(self, mock_backend):
        """Test recording normal mode restoration."""
        mock_backend_instance = MagicMock()
        mock_backend_instance.get.return_value = None
        mock_backend.return_value = mock_backend_instance

        from selfhealing.services.governance import EmergencyModeTracker

        tracker = EmergencyModeTracker()

        # Activate first
        tracker.record_emergency_activation(
            activated_by="operator_kim",
            reason="High error rate",
        )

        # Then restore
        result = tracker.record_normal_restoration(
            restored_by="admin_lee",
            reason="Issue resolved",
        )

        assert result["status"] == "restored"
        assert result["mode"] == "NORMAL"
        assert result["restored_by"] == "admin_lee"
        assert result["previous_mode"] == "STRICT"

        state = tracker.get_current_state()
        assert state.is_active is False
        assert state.mode == "NORMAL"

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_check_expiry_status_not_active(self, mock_backend):
        """Test expiry check when not in emergency mode."""
        mock_backend_instance = MagicMock()
        mock_backend_instance.get.return_value = None
        mock_backend.return_value = mock_backend_instance

        from selfhealing.services.governance import EmergencyModeTracker

        tracker = EmergencyModeTracker()
        status = tracker.check_expiry_status()

        assert status["is_active"] is False
        assert status["should_warn"] is False
        assert status["should_final_warn"] is False
        assert status["should_auto_restore"] is False

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_check_expiry_status_should_warn(self, mock_backend):
        """Test expiry check should_warn after 4 hours."""
        mock_backend_instance = MagicMock()
        mock_backend_instance.get.return_value = None
        mock_backend.return_value = mock_backend_instance

        from selfhealing.services.governance import EmergencyModeTracker, EmergencyState

        tracker = EmergencyModeTracker()

        # Set state to 4.5 hours ago
        now = datetime.now(timezone.utc)
        activated_at = now - timedelta(hours=4.5)
        tracker._state = EmergencyState(
            is_active=True,
            mode="STRICT",
            activated_at=activated_at.isoformat(),
            activated_by="operator_kim",
        )

        status = tracker.check_expiry_status()

        assert status["is_active"] is True
        assert status["should_warn"] is True
        assert status["should_final_warn"] is False
        assert status["should_auto_restore"] is False
        assert 4.4 < status["hours_elapsed"] < 4.6

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_check_expiry_status_should_auto_restore(self, mock_backend):
        """Test expiry check should_auto_restore after 8 hours."""
        mock_backend_instance = MagicMock()
        mock_backend_instance.get.return_value = None
        mock_backend.return_value = mock_backend_instance

        from selfhealing.services.governance import EmergencyModeTracker, EmergencyState

        tracker = EmergencyModeTracker()

        # Set state to 8.5 hours ago
        now = datetime.now(timezone.utc)
        activated_at = now - timedelta(hours=8.5)
        tracker._state = EmergencyState(
            is_active=True,
            mode="STRICT",
            activated_at=activated_at.isoformat(),
            activated_by="operator_kim",
        )

        status = tracker.check_expiry_status()

        assert status["is_active"] is True
        assert status["should_auto_restore"] is True
        assert status["hours_remaining"] == 0

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_auto_restore_to_normal(self, mock_backend):
        """Test automatic restoration to normal mode."""
        mock_backend_instance = MagicMock()
        mock_backend_instance.get.return_value = None
        mock_backend.return_value = mock_backend_instance

        from selfhealing.services.governance import EmergencyModeTracker

        tracker = EmergencyModeTracker()

        # Activate first
        tracker.record_emergency_activation(
            activated_by="operator_kim",
            reason="High error rate",
        )

        # Auto restore
        result = tracker.auto_restore_to_normal()

        assert result["status"] == "restored"
        assert result["mode"] == "NORMAL"
        assert result["restored_by"] == "system:auto_expiry"

        state = tracker.get_current_state()
        assert state.is_active is False
        assert state.mode == "NORMAL"

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_acknowledge_warning(self, mock_backend):
        """Test admin acknowledgement of warning."""
        mock_backend_instance = MagicMock()
        mock_backend_instance.get.return_value = None
        mock_backend.return_value = mock_backend_instance

        from selfhealing.services.governance import EmergencyModeTracker

        tracker = EmergencyModeTracker()

        # Activate first
        tracker.record_emergency_activation(
            activated_by="operator_kim",
            reason="High error rate",
        )

        # Acknowledge
        result = tracker.acknowledge_warning(acknowledged_by="admin_lee")

        assert result["status"] == "acknowledged"
        assert result["acknowledged_by"] == "admin_lee"

        state = tracker.get_current_state()
        assert state.acknowledged_by == "admin_lee"
        assert state.acknowledged_at is not None


# =============================================================================
# Governance Task Tests
# =============================================================================


class TestGovernanceTask:
    """Tests for governance Celery task."""

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_check_expiry_when_not_active(self, mock_backend):
        """Test expiry check when emergency mode is not active."""
        mock_backend_instance = MagicMock()
        mock_backend_instance.get.return_value = None
        mock_backend.return_value = mock_backend_instance

        from selfhealing.tasks.governance import check_emergency_mode_expiry

        result = check_emergency_mode_expiry()

        assert result["is_active"] is False
        assert result["actions_taken"] == []

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_check_expiry_triggers_warning(self, mock_backend):
        """Test expiry check triggers warning after 4 hours."""
        mock_backend_instance = MagicMock()
        mock_backend_instance.get.return_value = None
        mock_backend.return_value = mock_backend_instance

        from selfhealing.services.governance import EmergencyModeTracker, EmergencyState
        from selfhealing.tasks.governance import check_emergency_mode_expiry

        # Set up tracker with 4.5 hour old state
        now = datetime.now(timezone.utc)
        activated_at = now - timedelta(hours=4.5)

        with patch("selfhealing.services.governance.get_emergency_tracker") as mock_get_tracker:
            mock_tracker = MagicMock()
            mock_tracker.check_expiry_status.return_value = {
                "is_active": True,
                "mode": "STRICT",
                "activated_at": activated_at.isoformat(),
                "activated_by": "operator_kim",
                "should_warn": True,
                "should_final_warn": False,
                "should_auto_restore": False,
                "hours_elapsed": 4.5,
                "hours_remaining": 3.5,
                "warning_hours": 4,
                "final_warning_hours": 6,
                "expiry_hours": 8,
            }
            mock_get_tracker.return_value = mock_tracker

            result = check_emergency_mode_expiry()

            assert result["is_active"] is True
            assert len(result["actions_taken"]) > 0
            assert result["actions_taken"][0]["action"] == "warning_sent"
            mock_tracker.mark_warning_sent.assert_called_once()

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_check_expiry_triggers_auto_restore(self, mock_backend):
        """Test expiry check triggers auto-restore after 8 hours."""
        mock_backend_instance = MagicMock()
        mock_backend_instance.get.return_value = None
        mock_backend.return_value = mock_backend_instance

        from selfhealing.tasks.governance import check_emergency_mode_expiry

        now = datetime.now(timezone.utc)
        activated_at = now - timedelta(hours=8.5)

        with patch("selfhealing.services.governance.get_emergency_tracker") as mock_get_tracker:
            mock_tracker = MagicMock()
            mock_tracker.check_expiry_status.return_value = {
                "is_active": True,
                "mode": "STRICT",
                "activated_at": activated_at.isoformat(),
                "activated_by": "operator_kim",
                "should_warn": True,
                "should_final_warn": True,
                "should_auto_restore": True,
                "hours_elapsed": 8.5,
                "hours_remaining": 0,
                "warning_hours": 4,
                "final_warning_hours": 6,
                "expiry_hours": 8,
            }
            mock_tracker.auto_restore_to_normal.return_value = {
                "status": "restored",
                "mode": "NORMAL",
            }
            mock_tracker._get_governance_config.return_value = {
                "notify_channels": ["slack"],
            }
            mock_get_tracker.return_value = mock_tracker

            result = check_emergency_mode_expiry()

            assert result["is_active"] is True
            assert result["actions_taken"][0]["action"] == "auto_restore"
            mock_tracker.auto_restore_to_normal.assert_called_once()


# =============================================================================
# Integration Tests
# =============================================================================


class TestGovernanceIntegration:
    """Integration tests for governance Phase 1."""

    @patch("selfhealing.services.runtime_config.base.get_state_backend")
    def test_full_governance_workflow(self, mock_backend):
        """Test complete governance workflow."""
        mock_backend_instance = MagicMock()
        mock_backend_instance.get.return_value = None
        mock_backend.return_value = mock_backend_instance

        from selfhealing.services.runtime_config import RuntimeConfigManager
        from selfhealing.core.config import GovernanceConfig

        # 1. Create and configure governance
        manager = RuntimeConfigManager()

        # 2. Update governance config
        result = manager.update_governance_config(
            threshold_operator=0.20,
            threshold_admin=0.45,
            emergency_expiry_hours=6,
        )

        assert result["threshold_operator"] == 0.20
        assert result["threshold_admin"] == 0.45
        assert result["emergency_expiry_hours"] == 6

        # 3. Verify config is retrievable
        config = manager.get_governance_config()
        assert config["threshold_operator"] == 0.20
        assert config["threshold_admin"] == 0.45

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_emergency_lifecycle(self, mock_backend):
        """Test complete emergency mode lifecycle."""
        mock_backend_instance = MagicMock()
        mock_backend_instance.get.return_value = None
        mock_backend.return_value = mock_backend_instance

        from selfhealing.services.governance import (
            EmergencyModeTracker,
            is_emergency_mode_active,
            get_current_operation_mode,
        )

        tracker = EmergencyModeTracker()

        # 1. Initial state
        assert tracker.get_current_state().is_active is False
        assert tracker.get_current_state().mode == "NORMAL"

        # 2. Activate emergency mode
        tracker.record_emergency_activation(
            activated_by="operator",
            reason="Test emergency",
        )

        state = tracker.get_current_state()
        assert state.is_active is True
        assert state.mode == "STRICT"

        # 3. Check expiry status
        status = tracker.check_expiry_status()
        assert status["is_active"] is True
        assert status["hours_elapsed"] < 0.1  # Just activated

        # 4. Acknowledge warning
        tracker.acknowledge_warning(acknowledged_by="admin")
        assert tracker.get_current_state().acknowledged_by == "admin"

        # 5. Restore normal mode
        tracker.record_normal_restoration(restored_by="admin")
        assert tracker.get_current_state().is_active is False
        assert tracker.get_current_state().mode == "NORMAL"
