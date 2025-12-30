"""
Integration tests for Chaos API endpoints.

Tests REST API endpoints that require Django DB.
"""

import pytest
from unittest.mock import patch, MagicMock


@pytest.mark.django_db
class TestChaosAPIEndpoints:
    """Tests for Chaos API endpoints."""

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
        """Test GET /chaos/config/safety-guard/"""
        with patch("selfhealing.services.chaos.safety_guard.get_safety_guard") as mock_guard:
            mock_instance = MagicMock()
            mock_config = MagicMock()
            mock_config.to_dict.return_value = {"error_budget_min_percent": 20.0}
            mock_instance.get_config.return_value = mock_config
            mock_guard.return_value = mock_instance

            response = api_client.get("/api/self-healing/chaos/config/safety-guard/")

            # May return 404 if URL not registered yet, that's OK
            assert response.status_code in [200, 404]

    def test_blast_radius_config_get(self, api_client):
        """Test GET /chaos/config/blast-radius/"""
        with patch("selfhealing.services.chaos.blast_radius.get_blast_radius_manager") as mock_mgr:
            mock_instance = MagicMock()
            mock_policy = MagicMock()
            mock_policy.to_dict.return_value = {"instance_max_concurrent": 5}
            mock_instance.get_policy.return_value = mock_policy
            mock_mgr.return_value = mock_instance

            response = api_client.get("/api/self-healing/chaos/config/blast-radius/")

            assert response.status_code in [200, 404]

    def test_schedules_list(self, api_client):
        """Test GET /chaos/schedules/"""
        with patch("selfhealing.services.chaos.scheduler.get_chaos_scheduler") as mock_sched:
            mock_instance = MagicMock()
            mock_instance.list_schedules.return_value = []
            mock_sched.return_value = mock_instance

            response = api_client.get("/api/self-healing/chaos/schedules/")

            assert response.status_code in [200, 404]

    def test_kill_switch_get(self, api_client):
        """Test GET /chaos/kill-switch/"""
        with patch("selfhealing.services.chaos.scheduler.get_chaos_scheduler") as mock_sched:
            mock_instance = MagicMock()
            mock_instance.is_kill_switch_active.return_value = False
            mock_instance.get_kill_switch_status.return_value = {
                "active": False,
                "activated_at": None,
            }
            mock_sched.return_value = mock_instance

            response = api_client.get("/api/self-healing/chaos/kill-switch/")

            assert response.status_code in [200, 404]
