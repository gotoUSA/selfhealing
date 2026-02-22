"""
Integration tests for Chaos API endpoints.

Tests REST API endpoints that require Django DB.
Note: These tests require a running database connection.
"""

import pytest


class TestChaosAPIEndpointsRegistered:
    """Tests that Chaos API endpoints are properly registered (no DB required)."""

    def test_safety_guard_config_url_exists(self):
        """Test chaos/config/safety-guard/ URL is registered."""
        from selfhealing.api.django.urls import urlpatterns
        
        url_names = [pattern.name for pattern in urlpatterns if hasattr(pattern, 'name')]
        assert "chaos-config-safety-guard" in url_names

    def test_blast_radius_config_url_exists(self):
        """Test chaos/config/blast-radius/ URL is registered."""
        from selfhealing.api.django.urls import urlpatterns
        
        url_names = [pattern.name for pattern in urlpatterns if hasattr(pattern, 'name')]
        assert "chaos-config-blast-radius" in url_names

    def test_schedules_list_url_exists(self):
        """Test chaos/schedules/ URL is registered."""
        from selfhealing.api.django.urls import urlpatterns
        
        url_names = [pattern.name for pattern in urlpatterns if hasattr(pattern, 'name')]
        assert "chaos-schedules-list" in url_names

    def test_kill_switch_url_exists(self):
        """Test chaos/kill-switch/ URL is registered."""
        from selfhealing.api.django.urls import urlpatterns
        
        url_names = [pattern.name for pattern in urlpatterns if hasattr(pattern, 'name')]
        assert "chaos-kill-switch" in url_names


@pytest.mark.django_db
class TestChaosAPIEndpoints:
    """Tests for Chaos API endpoints with DB (integration)."""

    @pytest.fixture
    def api_client(self):
        """Create authenticated API client."""
        from rest_framework.test import APIClient
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(
            username="chaos_admin",
            password="testpass123",
            is_staff=True,
        )

        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_safety_guard_config_get(self, api_client):
        """Test GET /api/self-healing/chaos/config/safety-guard/"""
        response = api_client.get("/api/self-healing/chaos/config/safety-guard/")
        # 200 OK or 401/403 if permission required
        assert response.status_code in [200, 401, 403]

    def test_blast_radius_config_get(self, api_client):
        """Test GET /api/self-healing/chaos/config/blast-radius/"""
        response = api_client.get("/api/self-healing/chaos/config/blast-radius/")
        assert response.status_code in [200, 401, 403]

    def test_schedules_list(self, api_client):
        """Test GET /api/self-healing/chaos/schedules/"""
        response = api_client.get("/api/self-healing/chaos/schedules/")
        assert response.status_code in [200, 401, 403]

    def test_kill_switch_get(self, api_client):
        """Test GET /api/self-healing/chaos/kill-switch/"""
        response = api_client.get("/api/self-healing/chaos/kill-switch/")
        assert response.status_code in [200, 401, 403]
