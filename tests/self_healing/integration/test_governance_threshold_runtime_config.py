"""
Integration tests for ThresholdBasedPermission runtime config.

These tests require Django settings to be configured.
Moved from packages/selfhealing-python/tests for proper test isolation.
"""

import os
import pytest
from unittest.mock import MagicMock, patch


@pytest.mark.django_db
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
