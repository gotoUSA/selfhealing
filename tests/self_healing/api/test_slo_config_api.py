"""
SLO Config API Tests.

Tests for the SLO (Service Level Objectives) runtime configuration API.

Coverage:
- GET /api/self-healing/config/slo/ - Get all SLO configurations
- PUT /api/self-healing/config/slo/ - Add/Update SLO definitions
- DELETE /api/self-healing/config/slo/?name=<name> - Delete a specific SLO
- Validation tests (negative values, invalid ranges, etc.)
"""

import os
import sys

# Configure Django settings before importing DRF components
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "")

from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        USE_TZ=True,
        TIME_ZONE="UTC",
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "rest_framework",
        ],
        REST_FRAMEWORK={
            "DEFAULT_PERMISSION_CLASSES": [
                "rest_framework.permissions.AllowAny",
            ],
        },
        SECRET_KEY="test-secret-key-for-slo-config-api-tests",
    )

import django
django.setup()

import pytest

from selfhealing.api.django.serializers.config import (
    SLOConfigSerializer,
    SLODefinitionSerializer,
)
from selfhealing.services.runtime_config import (
    RuntimeConfigManager,
    reset_runtime_config_manager,
    get_runtime_config_manager,
)


class TestSLODefinitionSerializer:
    """Test SLODefinitionSerializer validation."""

    def test_valid_slo_definition(self):
        """Valid SLO definition should pass validation."""
        data = {
            "name": "api_availability",
            "sli_type": "availability",
            "target": 0.999,
            "window_days": 30,
            "description": "API availability SLO",
            "fast_burn_rate": 14.4,
            "slow_burn_rate": 3.0,
        }
        serializer = SLODefinitionSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["name"] == "api_availability"
        assert serializer.validated_data["target"] == 0.999

    def test_invalid_name_starts_with_number(self):
        """SLO name starting with number should fail."""
        data = {"name": "99availability"}
        serializer = SLODefinitionSerializer(data=data)
        assert not serializer.is_valid()
        assert "name" in serializer.errors

    def test_invalid_name_special_characters(self):
        """SLO name with special characters should fail."""
        data = {"name": "api-availability"}  # hyphen not allowed
        serializer = SLODefinitionSerializer(data=data)
        assert not serializer.is_valid()
        assert "name" in serializer.errors

    def test_valid_name_with_underscore(self):
        """SLO name with underscore should pass."""
        data = {"name": "api_availability_v2"}
        serializer = SLODefinitionSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_negative_target_rejected(self):
        """Negative target value should be rejected."""
        data = {"name": "test_slo", "target": -0.5}
        serializer = SLODefinitionSerializer(data=data)
        assert not serializer.is_valid()
        assert "target" in serializer.errors

    def test_target_over_one_rejected(self):
        """Target value over 1.0 should be rejected."""
        data = {"name": "test_slo", "target": 1.5}
        serializer = SLODefinitionSerializer(data=data)
        assert not serializer.is_valid()
        assert "target" in serializer.errors

    def test_negative_window_days_rejected(self):
        """Negative window_days should be rejected."""
        data = {"name": "test_slo", "window_days": -7}
        serializer = SLODefinitionSerializer(data=data)
        assert not serializer.is_valid()
        assert "window_days" in serializer.errors

    def test_zero_window_days_rejected(self):
        """Zero window_days should be rejected."""
        data = {"name": "test_slo", "window_days": 0}
        serializer = SLODefinitionSerializer(data=data)
        assert not serializer.is_valid()
        assert "window_days" in serializer.errors

    def test_warning_threshold_must_be_greater_than_critical(self):
        """warning_threshold must be greater than critical_threshold."""
        data = {
            "name": "test_slo",
            "target": 0.99,
            "warning_threshold": 0.95,
            "critical_threshold": 0.98,  # critical > warning = invalid
        }
        serializer = SLODefinitionSerializer(data=data)
        assert not serializer.is_valid()
        assert "non_field_errors" in serializer.errors

    def test_warning_threshold_must_be_greater_than_target(self):
        """warning_threshold must be greater than target."""
        data = {
            "name": "test_slo",
            "target": 0.999,
            "warning_threshold": 0.99,  # warning < target = invalid
        }
        serializer = SLODefinitionSerializer(data=data)
        assert not serializer.is_valid()
        assert "non_field_errors" in serializer.errors

    def test_fast_burn_rate_must_be_greater_than_slow(self):
        """fast_burn_rate must be greater than slow_burn_rate."""
        data = {
            "name": "test_slo",
            "fast_burn_rate": 2.0,
            "slow_burn_rate": 5.0,  # slow > fast = invalid
        }
        serializer = SLODefinitionSerializer(data=data)
        assert not serializer.is_valid()
        assert "non_field_errors" in serializer.errors

    def test_negative_burn_rate_rejected(self):
        """Negative burn rate should be rejected."""
        data = {"name": "test_slo", "fast_burn_rate": -5.0}
        serializer = SLODefinitionSerializer(data=data)
        assert not serializer.is_valid()
        assert "fast_burn_rate" in serializer.errors

    def test_invalid_sli_type_rejected(self):
        """Invalid SLI type should be rejected."""
        data = {"name": "test_slo", "sli_type": "invalid_type"}
        serializer = SLODefinitionSerializer(data=data)
        assert not serializer.is_valid()
        assert "sli_type" in serializer.errors

    def test_all_valid_sli_types(self):
        """All valid SLI types should be accepted."""
        valid_types = ["availability", "latency_p99", "latency_p95", "latency_p50", "error_rate", "throughput"]
        for sli_type in valid_types:
            data = {"name": "test_slo", "sli_type": sli_type}
            serializer = SLODefinitionSerializer(data=data)
            assert serializer.is_valid(), f"SLI type '{sli_type}' should be valid: {serializer.errors}"


class TestSLOConfigSerializer:
    """Test SLOConfigSerializer validation."""

    def test_valid_config_with_defaults(self):
        """Valid SLO config with defaults should pass."""
        data = {
            "default_window_days": 14,
            "default_target": 0.995,
        }
        serializer = SLOConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_valid_config_with_single_slo(self):
        """Config with single SLO should pass."""
        data = {
            "slo": {
                "name": "checkout_latency",
                "sli_type": "latency_p99",
                "target": 0.500,
                "window_days": 7,
            }
        }
        serializer = SLOConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_valid_config_with_multiple_slos(self):
        """Config with multiple SLOs should pass."""
        data = {
            "slos": [
                {"name": "api_availability", "target": 0.999},
                {"name": "payment_latency", "sli_type": "latency_p99", "target": 0.300},
            ]
        }
        serializer = SLOConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_negative_default_window_days_rejected(self):
        """Negative default_window_days should be rejected."""
        data = {"default_window_days": -10}
        serializer = SLOConfigSerializer(data=data)
        assert not serializer.is_valid()
        assert "default_window_days" in serializer.errors

    def test_default_target_below_90_percent_rejected(self):
        """default_target below 0.9 should be rejected."""
        data = {"default_target": 0.5}  # 50% is too low for SLO default
        serializer = SLOConfigSerializer(data=data)
        assert not serializer.is_valid()
        assert "default_target" in serializer.errors

    def test_default_burn_rates_order_validation(self):
        """default_fast_burn_rate must be greater than default_slow_burn_rate."""
        data = {
            "default_fast_burn_rate": 2.0,
            "default_slow_burn_rate": 5.0,  # slow > fast = invalid
        }
        serializer = SLOConfigSerializer(data=data)
        assert not serializer.is_valid()
        assert "non_field_errors" in serializer.errors


class TestSLORuntimeConfigManager:
    """Test RuntimeConfigManager SLO operations."""

    @pytest.fixture(autouse=True)
    def setup_manager(self):
        """Reset manager before each test."""
        reset_runtime_config_manager()
        yield
        reset_runtime_config_manager()

    def test_get_slo_config_returns_defaults(self):
        """get_slo_config should return default SLOs."""
        manager = get_runtime_config_manager()
        config = manager.get_slo_config()

        assert "default_window_days" in config
        # default_window_days는 기본값 30 또는 이전 테스트에서 설정된 14
        assert config["default_window_days"] in [14, 30]
        assert config["default_target"] in [0.995, 0.999]
        assert "slos" in config
        assert len(config["slos"]) >= 3  # availability, latency_p99, error_rate

    def test_update_slo_config_defaults(self):
        """update_slo_config should update default values."""
        manager = get_runtime_config_manager()
        result = manager.update_slo_config(
            default_window_days=14,
            default_target=0.995,
        )

        assert result["default_window_days"] == 14
        assert result["default_target"] == 0.995

    def test_add_new_slo(self):
        """Adding a new SLO should work."""
        manager = get_runtime_config_manager()
        # Get initial state and check if checkout_latency already exists
        initial_config = manager.get_slo_config()
        existing_names = [s["name"] for s in initial_config["slos"]]
        
        test_slo_name = "checkout_latency_test_new"
        
        # Remove if already exists (from previous test run)
        if test_slo_name in existing_names:
            manager.delete_slo(test_slo_name)
            
        original_count = len(manager.get_slo_config()["slos"])

        result = manager.update_slo_config(
            slo={
                "name": test_slo_name,
                "sli_type": "latency_p99",
                "target": 0.300,
                "window_days": 7,
            }
        )

        assert len(result["slos"]) == original_count + 1
        new_slo = next((s for s in result["slos"] if s["name"] == test_slo_name), None)
        assert new_slo is not None
        assert new_slo["target"] == 0.300
        assert new_slo["window_days"] == 7
        
        # Cleanup
        manager.delete_slo(test_slo_name)

    def test_update_existing_slo(self):
        """Updating an existing SLO should modify it."""
        manager = get_runtime_config_manager()

        # First, add a new SLO
        manager.update_slo_config(slo={"name": "test_slo", "target": 0.99})

        # Then update it
        result = manager.update_slo_config(slo={"name": "test_slo", "target": 0.999})

        # Should not add a duplicate
        test_slos = [s for s in result["slos"] if s["name"] == "test_slo"]
        assert len(test_slos) == 1
        assert test_slos[0]["target"] == 0.999

    def test_add_multiple_slos(self):
        """Adding multiple SLOs at once should work."""
        manager = get_runtime_config_manager()
        
        test_slo_names = ["slo_one_test", "slo_two_test"]
        
        # Clean up if they exist from previous run
        for name in test_slo_names:
            try:
                manager.delete_slo(name)
            except Exception:
                pass
        
        original_count = len(manager.get_slo_config()["slos"])

        result = manager.update_slo_config(
            slos=[
                {"name": test_slo_names[0], "target": 0.99},
                {"name": test_slo_names[1], "target": 0.995},
            ]
        )

        assert len(result["slos"]) == original_count + 2
        
        # Cleanup
        for name in test_slo_names:
            try:
                manager.delete_slo(name)
            except Exception:
                pass

    def test_delete_slo(self):
        """Deleting an SLO should work."""
        manager = get_runtime_config_manager()

        # Add a new SLO first
        manager.update_slo_config(slo={"name": "to_delete", "target": 0.99})
        count_before = len(manager.get_slo_config()["slos"])

        # Delete it
        result = manager.delete_slo("to_delete")

        assert result["status"] == "deleted"
        assert result["deleted_slo"]["name"] == "to_delete"
        assert result["remaining_count"] == count_before - 1

    def test_delete_nonexistent_slo(self):
        """Deleting a non-existent SLO should return not_found."""
        manager = get_runtime_config_manager()
        result = manager.delete_slo("nonexistent_slo")

        assert result["status"] == "not_found"
        assert "error" in result

    def test_get_slo_by_name(self):
        """get_slo_by_name should return the correct SLO."""
        manager = get_runtime_config_manager()

        # Default SLOs include 'availability'
        slo = manager.get_slo_by_name("availability")
        assert slo is not None
        assert slo["name"] == "availability"
        assert slo["sli_type"] == "availability"

    def test_get_nonexistent_slo_by_name(self):
        """get_slo_by_name should return None for non-existent SLO."""
        manager = get_runtime_config_manager()
        slo = manager.get_slo_by_name("nonexistent")
        assert slo is None

    def test_new_slo_uses_defaults(self):
        """New SLO without explicit values should use defaults."""
        manager = get_runtime_config_manager()

        # Set custom defaults
        manager.update_slo_config(
            default_window_days=14,
            default_target=0.995,
            default_fast_burn_rate=10.0,
            default_slow_burn_rate=2.0,
        )

        # Add SLO with only name
        result = manager.update_slo_config(slo={"name": "new_minimal_slo"})

        new_slo = next((s for s in result["slos"] if s["name"] == "new_minimal_slo"), None)
        assert new_slo is not None
        assert new_slo["window_days"] == 14
        assert new_slo["target"] == 0.995
        assert new_slo["fast_burn_rate"] == 10.0
        assert new_slo["slow_burn_rate"] == 2.0


class TestNegativeValueRejection:
    """Test that all numeric fields reject negative values."""

    def test_slo_target_negative_rejected(self):
        """Negative target should be rejected."""
        serializer = SLODefinitionSerializer(data={"name": "test", "target": -0.1})
        assert not serializer.is_valid()

    def test_slo_window_days_negative_rejected(self):
        """Negative window_days should be rejected."""
        serializer = SLODefinitionSerializer(data={"name": "test", "window_days": -1})
        assert not serializer.is_valid()

    def test_slo_fast_burn_rate_below_min_rejected(self):
        """fast_burn_rate below minimum should be rejected."""
        serializer = SLODefinitionSerializer(data={"name": "test", "fast_burn_rate": 0.5})
        assert not serializer.is_valid()

    def test_slo_slow_burn_rate_below_min_rejected(self):
        """slow_burn_rate below minimum should be rejected."""
        serializer = SLODefinitionSerializer(data={"name": "test", "slow_burn_rate": 0.1})
        assert not serializer.is_valid()

    def test_slo_warning_threshold_negative_rejected(self):
        """Negative warning_threshold should be rejected."""
        serializer = SLODefinitionSerializer(data={"name": "test", "warning_threshold": -0.5})
        assert not serializer.is_valid()

    def test_slo_critical_threshold_negative_rejected(self):
        """Negative critical_threshold should be rejected."""
        serializer = SLODefinitionSerializer(data={"name": "test", "critical_threshold": -0.5})
        assert not serializer.is_valid()


class TestBoundaryValues:
    """Test boundary value validation."""

    def test_target_at_zero_valid(self):
        """Target at 0.0 should be valid (edge case)."""
        serializer = SLODefinitionSerializer(data={"name": "test", "target": 0.0})
        assert serializer.is_valid(), serializer.errors

    def test_target_at_one_valid(self):
        """Target at 1.0 should be valid."""
        serializer = SLODefinitionSerializer(data={"name": "test", "target": 1.0})
        assert serializer.is_valid(), serializer.errors

    def test_window_days_at_one_valid(self):
        """window_days at 1 should be valid."""
        serializer = SLODefinitionSerializer(data={"name": "test", "window_days": 1})
        assert serializer.is_valid(), serializer.errors

    def test_window_days_at_max_valid(self):
        """window_days at max (365) should be valid."""
        serializer = SLODefinitionSerializer(data={"name": "test", "window_days": 365})
        assert serializer.is_valid(), serializer.errors

    def test_window_days_over_max_rejected(self):
        """window_days over max should be rejected."""
        serializer = SLODefinitionSerializer(data={"name": "test", "window_days": 400})
        assert not serializer.is_valid()
