"""
Runtime Config API Tests.

Tests for the runtime configuration management API.
"""

import pytest
from unittest.mock import patch, MagicMock
from rest_framework.test import APIRequestFactory
from rest_framework import status

# Import views
from selfhealing.api.django.views.config import (
    AllConfigView,
    ResetConfigView,
    CircuitBreakerConfigView,
    DLQConfigView,
    RetryConfigView,
    SLAConfigView,
    RateLimitConfigView,
    SecurityConfigView,
    IdempotencyConfigView,
    NotificationConfigView,
    ForensicConfigView,
    MetricsConfigView,
)

# Import serializers
from selfhealing.api.django.serializers.config import (
    CircuitBreakerConfigSerializer,
    DLQConfigSerializer,
    RetryConfigSerializer,
    SLAConfigSerializer,
    RateLimitConfigSerializer,
    SecurityConfigSerializer,
    IdempotencyConfigSerializer,
    NotificationConfigSerializer,
    ForensicConfigSerializer,
    MetricsConfigSerializer,
)

# Import RuntimeConfigManager
from selfhealing.services.runtime_config import (
    RuntimeConfigManager,
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
            {"failure_threshold": 15},
            format="json",
        )
        request.user = self.admin_user

        view = CircuitBreakerConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "success"
        assert response.data["config"]["failure_threshold"] == 15

    def test_reset_config_view(self):
        """Test POST /api/self-healing/config/reset/"""
        request = self.factory.post("/api/self-healing/config/reset/")
        request.user = self.admin_user

        view = ResetConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "success"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
