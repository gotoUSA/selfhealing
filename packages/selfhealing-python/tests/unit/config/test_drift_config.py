"""
Tests for Drift Threshold Configuration.
"""

import pytest
from unittest.mock import patch

from selfhealing.models.drift_config import DriftThresholdConfig


class TestDriftThresholdConfig:
    """Tests for DriftThresholdConfig dataclass."""

    def test_default_values(self):
        """Default values should match documented thresholds."""
        config = DriftThresholdConfig()

        assert config.warning_threshold == 0.05    # 5%
        assert config.critical_threshold == 0.20   # 20%
        assert config.incident_threshold == 0.50   # 50%
        assert config.alert_enabled is True
        assert config.incident_auto_create is True

    def test_validation_passes_for_valid_thresholds(self):
        """Valid thresholds should pass validation."""
        config = DriftThresholdConfig(
            warning_threshold=0.10,
            critical_threshold=0.30,
            incident_threshold=0.60,
        )
        assert config.warning_threshold == 0.10

    def test_validation_fails_for_invalid_order(self):
        """Invalid threshold order should raise ValueError."""
        with pytest.raises(ValueError, match="Thresholds must be"):
            DriftThresholdConfig(
                warning_threshold=0.30,
                critical_threshold=0.20,  # Less than warning
                incident_threshold=0.50,
            )

    def test_validation_fails_for_zero_warning(self):
        """Zero warning threshold should raise ValueError."""
        with pytest.raises(ValueError):
            DriftThresholdConfig(warning_threshold=0.0)

    def test_validation_fails_for_incident_over_one(self):
        """Incident threshold > 1.0 should raise ValueError."""
        with pytest.raises(ValueError):
            DriftThresholdConfig(incident_threshold=1.5)

    def test_to_dict(self):
        """to_dict should return all fields."""
        config = DriftThresholdConfig(
            warning_threshold=0.10,
            critical_threshold=0.30,
            incident_threshold=0.60,
            updated_by="admin",
        )

        data = config.to_dict()

        assert data["warning_threshold"] == 0.10
        assert data["critical_threshold"] == 0.30
        assert data["incident_threshold"] == 0.60
        assert data["updated_by"] == "admin"

    def test_from_dict(self):
        """from_dict should create config from dictionary."""
        data = {
            "warning_threshold": 0.10,
            "critical_threshold": 0.30,
            "incident_threshold": 0.60,
            "alert_enabled": False,
        }

        config = DriftThresholdConfig.from_dict(data)

        assert config.warning_threshold == 0.10
        assert config.alert_enabled is False

    def test_from_dict_ignores_unknown_fields(self):
        """from_dict should ignore unknown fields."""
        data = {
            "warning_threshold": 0.10,
            "critical_threshold": 0.30,
            "incident_threshold": 0.60,
            "unknown_field": "value",
        }

        config = DriftThresholdConfig.from_dict(data)
        assert not hasattr(config, "unknown_field")

    @patch.dict(
        "os.environ",
        {
            "SELFHEALING_DRIFT_WARNING_THRESHOLD": "0.10",
            "SELFHEALING_DRIFT_CRITICAL_THRESHOLD": "0.25",
            "SELFHEALING_DRIFT_INCIDENT_THRESHOLD": "0.60",
            "SELFHEALING_DRIFT_ALERT_ENABLED": "false",
        },
    )
    def test_from_env(self):
        """from_env should load from environment variables."""
        config = DriftThresholdConfig.from_env()

        assert config.warning_threshold == 0.10
        assert config.critical_threshold == 0.25
        assert config.incident_threshold == 0.60
        assert config.alert_enabled is False

    def test_update_creates_new_instance(self):
        """update should create new instance with updated values."""
        original = DriftThresholdConfig()

        updated = original.update(
            actor_id="admin",
            warning_threshold=0.10,
        )

        # Original unchanged
        assert original.warning_threshold == 0.05

        # Updated has new values
        assert updated.warning_threshold == 0.10
        assert updated.updated_by == "admin"
        assert updated.updated_at is not None

    def test_get_threshold_percent_display(self):
        """get_threshold_percent_display should return readable percentages."""
        config = DriftThresholdConfig()

        display = config.get_threshold_percent_display()

        assert display["warning"] == "5.0%"
        assert display["critical"] == "20.0%"
        assert display["incident"] == "50.0%"
