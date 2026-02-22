"""
Runtime Config API Tests.

Tests for the runtime configuration management API.
"""

import os
import django

# Configure Django settings before importing DRF
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()

import pytest
from unittest.mock import MagicMock
from rest_framework.test import APIRequestFactory
from rest_framework import status

# Import views
from selfhealing.api.django.views.config import (
    AllConfigView,
    ResetConfigView,
    CircuitBreakerConfigView,
    MetricsConfigView,
)

# Import serializers
from selfhealing.api.django.serializers.config import (
    CircuitBreakerConfigSerializer,
    DLQConfigSerializer,
    RetryConfigSerializer,
    MetricsConfigSerializer,
)

# Import RuntimeConfigManager
from selfhealing.services.runtime_config import (
    get_runtime_config_manager,
)


@pytest.fixture
def factory():
    """Create API request factory."""
    return APIRequestFactory()


@pytest.fixture
def admin_user():
    """Create mock admin user."""
    user = MagicMock()
    user.is_staff = True
    user.is_authenticated = True
    return user


class TestRuntimeConfigManager:
    """Tests for RuntimeConfigManager."""

    def test_singleton_pattern(self):
        """Test that get_runtime_config_manager returns singleton."""
        manager1 = get_runtime_config_manager()
        manager2 = get_runtime_config_manager()
        assert manager1 is manager2

    def test_get_all_config(self):
        """Test getting all configuration."""
        manager = get_runtime_config_manager()
        config = manager.get_all_config()

        assert "circuit_breaker" in config
        assert "dlq" in config
        assert "retry" in config
        assert "sla" in config
        assert "rate_limit" in config
        assert "security" in config
        assert "idempotency" in config
        assert "notification" in config
        assert "forensic" in config
        assert "metrics" in config

    def test_get_circuit_breaker_config(self):
        """Test getting circuit breaker config."""
        manager = get_runtime_config_manager()
        config = manager.get_circuit_breaker_config()

        assert "failure_threshold" in config
        assert "recovery_timeout" in config
        assert "half_open_max_calls" in config

    def test_update_circuit_breaker_config(self):
        """Test updating circuit breaker config."""
        manager = get_runtime_config_manager()
        original = manager.get_circuit_breaker_config()

        # Update single field
        updated = manager.update_circuit_breaker_config(failure_threshold=10)

        assert updated["failure_threshold"] == 10
        assert updated["recovery_timeout"] == original["recovery_timeout"]

    def test_reset_to_defaults(self):
        """Test resetting all config to defaults."""
        manager = get_runtime_config_manager()

        # Modify some config
        manager.update_circuit_breaker_config(failure_threshold=999)

        # Reset
        config = manager.reset_to_defaults()

        # Should be back to defaults
        assert config["circuit_breaker"]["failure_threshold"] == 5  # Default


class TestConfigSerializers:
    """Tests for config serializers."""

    def test_circuit_breaker_serializer_valid(self):
        """Test valid circuit breaker serializer data."""
        data = {
            "failure_threshold": 10,
            "recovery_timeout": 60,
        }
        serializer = CircuitBreakerConfigSerializer(data=data)
        assert serializer.is_valid()
        assert serializer.validated_data["failure_threshold"] == 10

    def test_circuit_breaker_serializer_invalid_range(self):
        """Test invalid circuit breaker serializer data."""
        data = {
            "failure_threshold": 0,  # Invalid: min is 1
        }
        serializer = CircuitBreakerConfigSerializer(data=data)
        assert not serializer.is_valid()

    def test_dlq_serializer_valid(self):
        """Test valid DLQ serializer data."""
        data = {
            "max_queue_size": 5000,
            "batch_size": 100,
        }
        serializer = DLQConfigSerializer(data=data)
        assert serializer.is_valid()

    def test_retry_serializer_valid(self):
        """Test valid retry serializer data."""
        data = {
            "max_retries": 5,
            "initial_delay": 2.0,
        }
        serializer = RetryConfigSerializer(data=data)
        assert serializer.is_valid()


class TestConfigViews:
    """Tests for config API views."""

    @pytest.fixture(autouse=True)
    def setup(self, factory, admin_user):
        """Setup test fixtures."""
        self.factory = factory
        self.admin_user = admin_user

    def test_all_config_view_get(self):
        """Test GET /api/self-healing/config/"""
        request = self.factory.get("/api/self-healing/config/")
        request.user = self.admin_user

        view = AllConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "success"
        assert "config" in response.data

    def test_circuit_breaker_config_view_get(self):
        """Test GET /api/self-healing/config/circuit-breaker/"""
        request = self.factory.get("/api/self-healing/config/circuit-breaker/")
        request.user = self.admin_user

        view = CircuitBreakerConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "success"
        assert "config" in response.data

    def test_circuit_breaker_config_view_put(self):
        """Test PUT /api/self-healing/config/circuit-breaker/"""
        request = self.factory.put(
            "/api/self-healing/config/circuit-breaker/",
            {"failure_threshold": 15, "apply_strategy": "immediate"},  # explicit immediate
            format="json",
        )
        request.user = self.admin_user

        view = CircuitBreakerConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] in ("success", "applied")

    def test_circuit_breaker_config_view_put_delayed(self):
        """Test PUT with delayed strategy returns 202."""
        request = self.factory.put(
            "/api/self-healing/config/circuit-breaker/",
            {"failure_threshold": 20},  # default is delayed
            format="json",
        )
        request.user = self.admin_user

        view = CircuitBreakerConfigView.as_view()
        response = view(request)

        # Default strategy is delayed, so expect 202
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.data["status"] in ("scheduled", "waiting")

    def test_reset_config_view(self):
        """Test POST /api/self-healing/config/reset/"""
        request = self.factory.post("/api/self-healing/config/reset/")
        request.user = self.admin_user

        view = ResetConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "success"


class TestConfigValidationAuditLog:
    """Tests for configuration validation audit logging."""

    @pytest.fixture(autouse=True)
    def setup(self, factory, admin_user):
        self.factory = factory
        self.admin_user = admin_user

    def test_serializer_validation_fails_on_invalid_type(self):
        """Test that serializer correctly rejects invalid types."""
        serializer = CircuitBreakerConfigSerializer(data={"failure_threshold": "not_a_number"})
        assert serializer.is_valid() is False
        assert "failure_threshold" in serializer.errors

    def test_serializer_validation_fails_on_out_of_range(self):
        """Test that serializer correctly rejects out-of-range values."""
        serializer = CircuitBreakerConfigSerializer(data={"failure_threshold": 0})
        assert serializer.is_valid() is False
        assert "failure_threshold" in serializer.errors

    def test_serializer_validation_fails_on_negative(self):
        """Test that serializer correctly rejects negative values."""
        serializer = CircuitBreakerConfigSerializer(data={"failure_threshold": -5})
        assert serializer.is_valid() is False
        assert "failure_threshold" in serializer.errors

    def test_get_client_ip_from_remote_addr(self):
        """Test _get_client_ip extracts IP from REMOTE_ADDR."""

        view = CircuitBreakerConfigView()
        request = MagicMock()
        request.META = {"REMOTE_ADDR": "192.168.1.100"}

        ip = view._get_client_ip(request)
        assert ip == "192.168.1.100"

    def test_get_client_ip_from_x_forwarded_for(self):
        """Test _get_client_ip prefers X-Forwarded-For header."""
        view = CircuitBreakerConfigView()
        request = MagicMock()
        request.META = {
            "REMOTE_ADDR": "10.0.0.1",
            "HTTP_X_FORWARDED_FOR": "203.0.113.50, 70.41.3.18",
        }

        ip = view._get_client_ip(request)
        assert ip == "203.0.113.50"

    def test_get_client_ip_handles_missing_addr(self):
        """Test _get_client_ip returns 'unknown' when no IP available."""
        view = CircuitBreakerConfigView()
        request = MagicMock()
        request.META = {}

        ip = view._get_client_ip(request)
        assert ip == "unknown"


class TestJitterConfigAPI:
    """
    Tests for Jitter Configuration API.

    Validates the 'Triangle Defense System':
    - SLA (Business Goals)
    - Error Budget (Operational Budget)
    - Jitter (Infrastructure Protection)
    """

    @pytest.fixture(autouse=True)
    def setup(self, factory, admin_user):
        self.factory = factory
        self.admin_user = admin_user

    def test_jitter_serializer_valid_data(self):
        """Test valid jitter configuration data."""
        serializer = MetricsConfigSerializer(
            data={
                "jitter_enabled": True,
                "jitter_max_delay_seconds": 30.0,
            }
        )
        assert serializer.is_valid()
        assert serializer.validated_data["jitter_enabled"] is True
        assert serializer.validated_data["jitter_max_delay_seconds"] == 30.0

    def test_jitter_serializer_rejects_negative_delay(self):
        """Test that negative jitter delay is rejected (Clamping)."""
        serializer = MetricsConfigSerializer(
            data={"jitter_max_delay_seconds": -10.0}
        )
        assert serializer.is_valid() is False
        assert "jitter_max_delay_seconds" in serializer.errors

    def test_jitter_serializer_rejects_exceeding_max(self):
        """Test that jitter delay exceeding 300 seconds is rejected."""
        serializer = MetricsConfigSerializer(
            data={"jitter_max_delay_seconds": 500.0}  # Exceeds max 300
        )
        assert serializer.is_valid() is False
        assert "jitter_max_delay_seconds" in serializer.errors

    def test_jitter_serializer_accepts_zero(self):
        """Test that zero jitter delay is valid (disables jitter effectively)."""
        serializer = MetricsConfigSerializer(
            data={"jitter_max_delay_seconds": 0.0}
        )
        assert serializer.is_valid()
        assert serializer.validated_data["jitter_max_delay_seconds"] == 0.0

    def test_jitter_serializer_accepts_max_boundary(self):
        """Test that exactly 300 seconds is valid."""
        serializer = MetricsConfigSerializer(
            data={"jitter_max_delay_seconds": 300.0}
        )
        assert serializer.is_valid()
        assert serializer.validated_data["jitter_max_delay_seconds"] == 300.0

    def test_metrics_config_view_get(self):
        """Test GET /api/self-healing/config/metrics/"""
        request = self.factory.get("/api/self-healing/config/metrics/")
        request.user = self.admin_user

        view = MetricsConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "success"
        assert "config" in response.data

    def test_metrics_config_view_put_jitter(self):
        """Test PUT /api/self-healing/config/metrics/ with jitter settings."""
        request = self.factory.put(
            "/api/self-healing/config/metrics/",
            {
                "jitter_enabled": False,
                "jitter_max_delay_seconds": 45.0,
                "apply_strategy": "immediate",
            },
            format="json",
        )
        request.user = self.admin_user

        view = MetricsConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] in ("success", "applied")

    def test_metrics_config_view_put_invalid_jitter_rejected(self):
        """Test PUT with invalid jitter values returns 400."""
        request = self.factory.put(
            "/api/self-healing/config/metrics/",
            {"jitter_max_delay_seconds": -5.0},  # Invalid: negative
            format="json",
        )
        request.user = self.admin_user

        view = MetricsConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "errors" in response.data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
