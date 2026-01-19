"""
Governance Drift Threshold 구현 테스트.

테스트 대상:
1. DriftThresholdConfig RuntimeConfigManager 통합
2. GovernanceRBACStatusView API
3. GovernanceConfigView API
4. Drift Threshold API 리팩토링
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch


# =============================================================================
# DriftThresholdConfig Tests
# =============================================================================


class TestDriftThresholdConfig:
    """Tests for DriftThresholdConfig dataclass."""

    def test_default_values(self):
        """Test default values are set correctly."""
        from selfhealing.core.config import DriftThresholdConfig

        config = DriftThresholdConfig()

        assert config.warning_threshold == 0.05
        assert config.critical_threshold == 0.20
        assert config.incident_threshold == 0.50
        assert config.alert_enabled is True
        assert config.incident_auto_create is True

    def test_custom_values(self):
        """Test custom values can be set."""
        from selfhealing.core.config import DriftThresholdConfig

        config = DriftThresholdConfig(
            warning_threshold=0.10,
            critical_threshold=0.30,
            incident_threshold=0.60,
            alert_enabled=False,
        )

        assert config.warning_threshold == 0.10
        assert config.critical_threshold == 0.30
        assert config.incident_threshold == 0.60
        assert config.alert_enabled is False

    def test_dataclass_conversion(self):
        """Test config can be converted to dict (uses Pydantic model_dump)."""
        from selfhealing.core.config import DriftThresholdConfig

        config = DriftThresholdConfig()
        config_dict = config.model_dump()

        assert isinstance(config_dict, dict)
        assert "warning_threshold" in config_dict
        assert "critical_threshold" in config_dict
        assert "incident_threshold" in config_dict


# =============================================================================
# RuntimeConfigManager DriftThreshold Integration Tests
# =============================================================================


class TestRuntimeConfigManagerDriftThreshold:
    """Tests for RuntimeConfigManager drift_threshold integration."""

    def test_drift_threshold_in_storage_keys(self):
        """Test drift_threshold is registered in STORAGE_KEYS."""
        from selfhealing.services.runtime_config.constants import STORAGE_KEYS

        assert "drift_threshold" in STORAGE_KEYS
        assert STORAGE_KEYS["drift_threshold"] == "runtime_config:drift_threshold"

    def test_drift_threshold_in_config_classes(self):
        """Test drift_threshold is registered in CONFIG_CLASSES."""
        from selfhealing.services.runtime_config.constants import CONFIG_CLASSES
        from selfhealing.core.config import DriftThresholdConfig

        assert "drift_threshold" in CONFIG_CLASSES
        assert CONFIG_CLASSES["drift_threshold"] == DriftThresholdConfig

    @patch("selfhealing.services.runtime_config.base.get_state_backend")
    def test_get_drift_threshold_config(self, mock_backend):
        """Test getting drift threshold config returns defaults."""
        mock_backend_instance = MagicMock()
        mock_backend_instance.get.return_value = None
        mock_backend.return_value = mock_backend_instance

        from selfhealing.services.runtime_config import RuntimeConfigManager

        manager = RuntimeConfigManager()
        config = manager.get_drift_threshold_config()

        assert config["warning_threshold"] == 0.05
        assert config["critical_threshold"] == 0.20
        assert config["incident_threshold"] == 0.50

    @patch("selfhealing.services.runtime_config.base.get_state_backend")
    def test_update_drift_threshold_config(self, mock_backend):
        """Test updating drift threshold config."""
        mock_backend_instance = MagicMock()
        mock_backend_instance.get.return_value = None
        mock_backend.return_value = mock_backend_instance

        from selfhealing.services.runtime_config import RuntimeConfigManager

        manager = RuntimeConfigManager()
        
        # Update with valid values
        new_config = manager.update_drift_threshold_config(
            warning_threshold=0.10,
            critical_threshold=0.25,
            changed_by="test_user",
        )

        assert new_config["warning_threshold"] == 0.10
        assert new_config["critical_threshold"] == 0.25

    @patch("selfhealing.services.runtime_config.base.get_state_backend")
    def test_update_drift_threshold_validation_fails(self, mock_backend):
        """Test validation fails for invalid threshold order."""
        mock_backend_instance = MagicMock()
        mock_backend_instance.get.return_value = None
        mock_backend.return_value = mock_backend_instance

        from selfhealing.services.runtime_config import RuntimeConfigManager

        manager = RuntimeConfigManager()
        
        # Invalid: warning > critical
        with pytest.raises(ValueError) as exc_info:
            manager.update_drift_threshold_config(
                warning_threshold=0.30,  # warning > current critical (0.20)
                changed_by="test_user",
            )
        
        assert "Thresholds must be" in str(exc_info.value)

    @patch("selfhealing.services.runtime_config.base.get_state_backend")
    def test_reset_drift_threshold_config(self, mock_backend):
        """Test resetting drift threshold config to defaults."""
        mock_backend_instance = MagicMock()
        mock_backend_instance.get.return_value = {
            "warning_threshold": 0.15,  # Non-default
            "critical_threshold": 0.35,
            "incident_threshold": 0.60,
        }
        mock_backend.return_value = mock_backend_instance

        from selfhealing.services.runtime_config import RuntimeConfigManager

        manager = RuntimeConfigManager()
        
        default_config = manager.reset_drift_threshold_config(changed_by="test_user")

        assert default_config["warning_threshold"] == 0.05
        assert default_config["critical_threshold"] == 0.20
        assert default_config["incident_threshold"] == 0.50


# =============================================================================
# EmergencyModeTracker check_expiry_status Tests
# =============================================================================


class TestEmergencyModeTrackerExpiryStatus:
    """Tests for EmergencyModeTracker check_expiry_status method."""

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_expiry_status_inactive(self, mock_backend):
        """Test expiry status when emergency mode is inactive."""
        mock_backend_instance = MagicMock()
        mock_backend_instance.get.return_value = None
        mock_backend.return_value = mock_backend_instance

        from selfhealing.services.governance import EmergencyModeTracker

        tracker = EmergencyModeTracker()
        status = tracker.check_expiry_status()

        assert status["is_active"] is False
        assert status["should_warn"] is False
        assert status["should_auto_restore"] is False
        assert status["expires_at"] is None
        assert status["time_remaining_hours"] is None

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_expiry_status_active_with_expires_at(self, mock_backend):
        """Test expiry status includes expires_at when active."""
        now = datetime.now(timezone.utc)
        activated_at = now - timedelta(hours=2)  # 2 hours ago
        
        mock_backend_instance = MagicMock()
        mock_backend_instance.get.return_value = {
            "is_active": True,
            "mode": "STRICT",
            "activated_at": activated_at.isoformat(),
            "activated_by": "test_operator",
            "reason": "Test emergency",
            "warning_sent_at": None,
            "final_warning_sent_at": None,
        }
        mock_backend.return_value = mock_backend_instance

        from selfhealing.services.governance import EmergencyModeTracker

        tracker = EmergencyModeTracker()
        status = tracker.check_expiry_status()

        assert status["is_active"] is True
        assert status["expires_at"] is not None
        assert status["time_remaining_hours"] is not None
        assert status["time_remaining_hours"] > 0
        assert status["hours_elapsed"] >= 1.9  # About 2 hours


# =============================================================================
# GovernanceRBACStatusView Tests
# =============================================================================


class TestGovernanceRBACStatusView:
    """Tests for GovernanceRBACStatusView API."""

    def test_view_exists(self):
        """Test view class exists."""
        from selfhealing.api.django.views.governance import GovernanceRBACStatusView
        assert GovernanceRBACStatusView is not None

    def test_view_permission_class(self):
        """Test view has correct permission class."""
        from selfhealing.api.django.views.governance import GovernanceRBACStatusView
        from selfhealing.api.django.permissions import IsViewer

        view = GovernanceRBACStatusView()
        assert IsViewer in view.permission_classes


class TestGovernanceConfigView:
    """Tests for GovernanceConfigView API."""

    def test_view_exists(self):
        """Test view class exists."""
        from selfhealing.api.django.views.governance import GovernanceConfigView
        assert GovernanceConfigView is not None


# =============================================================================
# DriftThresholdConfigView Refactoring Tests
# =============================================================================


class TestDriftThresholdConfigViewRefactoring:
    """Tests for refactored DriftThresholdConfigView using RuntimeConfigManager."""

    def test_uses_runtime_config_manager(self):
        """Test that the view now uses RuntimeConfigManager."""
        from selfhealing.api.django.views.drift_threshold import DriftThresholdConfigView
        import inspect
        
        source = inspect.getsource(DriftThresholdConfigView)
        
        # Should import from runtime_config
        assert "get_runtime_config_manager" in source or "RuntimeConfigManager" in source
        
        # Should NOT use old state_backend directly
        assert "DRIFT_THRESHOLD_CONFIG_KEY" not in source

    def test_helper_function_exists(self):
        """Test helper function for percent display exists."""
        from selfhealing.api.django.views.drift_threshold import _get_threshold_percent_display
        
        config = {
            "warning_threshold": 0.05,
            "critical_threshold": 0.20,
            "incident_threshold": 0.50,
        }
        
        result = _get_threshold_percent_display(config)
        
        assert result["warning"] == "5.0%"
        assert result["critical"] == "20.0%"
        assert result["incident"] == "50.0%"


# =============================================================================
# URL Registration Tests
# =============================================================================


class TestURLRegistration:
    """Tests for URL registration."""

    def test_governance_status_url_registered(self):
        """Test governance/status/ URL is registered."""
        from selfhealing.api.django.urls import urlpatterns
        
        url_names = [p.name for p in urlpatterns if hasattr(p, 'name')]
        assert "governance-status" in url_names

    def test_config_governance_url_registered(self):
        """Test config/governance/ URL is registered."""
        from selfhealing.api.django.urls import urlpatterns
        
        url_names = [p.name for p in urlpatterns if hasattr(p, 'name')]
        assert "config-governance" in url_names


# =============================================================================
# Integration Tests (with mock Django request)
# =============================================================================


class TestGovernanceIntegration:
    """Governance 통합 테스트."""

    @patch("selfhealing.services.runtime_config.base.get_state_backend")
    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_full_governance_workflow(self, mock_core_backend, mock_config_backend):
        """Test full governance workflow: config update -> status check."""
        # Setup mocks
        config_backend_instance = MagicMock()
        config_backend_instance.get.return_value = None
        mock_config_backend.return_value = config_backend_instance
        
        gov_backend_instance = MagicMock()
        gov_backend_instance.get.return_value = {
            "is_active": True,
            "mode": "STRICT",
            "activated_at": datetime.now(timezone.utc).isoformat(),
            "activated_by": "test_admin",
            "reason": "Test",
        }
        mock_core_backend.return_value = gov_backend_instance

        from selfhealing.services.runtime_config import RuntimeConfigManager
        from selfhealing.services.governance import EmergencyModeTracker

        # 1. Update governance config
        manager = RuntimeConfigManager()
        new_config = manager.update_governance_config(
            threshold_operator=0.20,
            threshold_admin=0.40,
        )
        assert new_config["threshold_operator"] == 0.20
        assert new_config["threshold_admin"] == 0.40

        # 2. Check emergency status
        tracker = EmergencyModeTracker()
        status = tracker.check_expiry_status()
        assert status["is_active"] is True
        assert status["mode"] == "STRICT"

    @patch("selfhealing.services.runtime_config.base.get_state_backend")
    def test_drift_threshold_and_governance_separate(self, mock_backend):
        """Test drift_threshold and governance configs are separate."""
        mock_backend_instance = MagicMock()
        mock_backend_instance.get.return_value = None
        mock_backend.return_value = mock_backend_instance

        from selfhealing.services.runtime_config import RuntimeConfigManager

        manager = RuntimeConfigManager()

        # Get both configs
        drift_config = manager.get_drift_threshold_config()
        governance_config = manager.get_governance_config()

        # They should have different keys
        assert "warning_threshold" in drift_config
        assert "threshold_operator" in governance_config
        
        # And different values
        assert "threshold_operator" not in drift_config
        assert "warning_threshold" not in governance_config


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
