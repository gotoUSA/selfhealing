"""
Tests for Drift Threshold Configuration API.

Reference: docs/self_healing/13_METRIC_COLLECTION_STRATEGY.md
"""

import os
import django

# Configure Django settings before importing DRF
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from rest_framework.test import APIRequestFactory
from rest_framework import status

from selfhealing.api.django.views.drift_threshold import (
    DriftThresholdConfigView,
    DriftThresholdResetView,
    DRIFT_THRESHOLD_CONFIG_KEY,
)
from selfhealing.models.drift_config import DriftThresholdConfig


class TestDriftThresholdConfigView:
    """Tests for DriftThresholdConfigView."""

    @pytest.fixture
    def factory(self):
        return APIRequestFactory()

    @pytest.fixture
    def admin_user(self):
        user = MagicMock()
        user.is_authenticated = True
        user.is_staff = True
        user.__str__ = lambda self: "admin"
        return user

    @pytest.fixture
    def viewer_user(self):
        user = MagicMock()
        user.is_authenticated = True
        user.is_staff = False
        user.__str__ = lambda self: "viewer"
        return user

    @pytest.fixture
    def mock_backend(self):
        """Mock state backend."""
        with patch("selfhealing.api.django.views.drift_threshold.get_state_backend") as mock:
            backend = MagicMock()
            mock.return_value = backend
            yield backend

    def test_get_returns_default_config(self, factory, admin_user, mock_backend):
        """GET returns default config when none is stored."""
        mock_backend.get.return_value = None

        request = factory.get("/api/self-healing/config/drift-thresholds/")
        request.user = admin_user

        view = DriftThresholdConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "success"
        assert response.data["config"]["warning_threshold"] == 0.05
        assert response.data["config"]["critical_threshold"] == 0.20
        assert response.data["config"]["incident_threshold"] == 0.50
        assert response.data["thresholds_percent"]["warning"] == "5.0%"
        assert response.data["thresholds_percent"]["critical"] == "20.0%"
        assert response.data["thresholds_percent"]["incident"] == "50.0%"

    def test_get_returns_stored_config(self, factory, admin_user, mock_backend):
        """GET returns stored config."""
        mock_backend.get.return_value = {
            "warning_threshold": 0.10,
            "critical_threshold": 0.30,
            "incident_threshold": 0.60,
            "alert_enabled": True,
            "incident_auto_create": False,
        }

        request = factory.get("/api/self-healing/config/drift-thresholds/")
        request.user = admin_user

        view = DriftThresholdConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["config"]["warning_threshold"] == 0.10
        assert response.data["config"]["critical_threshold"] == 0.30
        assert response.data["config"]["incident_threshold"] == 0.60
        assert response.data["thresholds_percent"]["warning"] == "10.0%"

    def test_put_updates_thresholds(self, factory, admin_user, mock_backend):
        """PUT updates threshold values."""
        mock_backend.get.return_value = None

        request = factory.put(
            "/api/self-healing/config/drift-thresholds/",
            data={
                "warning_threshold": 0.10,
                "critical_threshold": 0.25,
                "incident_threshold": 0.55,
            },
            content_type="application/json",
        )
        request.user = admin_user

        view = DriftThresholdConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "updated"
        assert response.data["config"]["warning_threshold"] == 0.10
        assert response.data["config"]["critical_threshold"] == 0.25
        assert response.data["config"]["incident_threshold"] == 0.55
        assert response.data["updated_by"] == "admin"

        # Verify backend was called
        mock_backend.set.assert_called_once()
        saved_data = mock_backend.set.call_args[0][1]
        assert saved_data["warning_threshold"] == 0.10

    def test_put_updates_boolean_fields(self, factory, admin_user, mock_backend):
        """PUT updates boolean configuration fields."""
        mock_backend.get.return_value = None

        request = factory.put(
            "/api/self-healing/config/drift-thresholds/",
            data={
                "alert_enabled": False,
                "incident_auto_create": False,
            },
            content_type="application/json",
        )
        request.user = admin_user

        view = DriftThresholdConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["config"]["alert_enabled"] is False
        assert response.data["config"]["incident_auto_create"] is False

    def test_put_validates_threshold_order(self, factory, admin_user, mock_backend):
        """PUT validates that warning < critical < incident."""
        mock_backend.get.return_value = None

        # warning >= critical is invalid
        request = factory.put(
            "/api/self-healing/config/drift-thresholds/",
            data={
                "warning_threshold": 0.30,
                "critical_threshold": 0.20,
            },
            content_type="application/json",
        )
        request.user = admin_user

        view = DriftThresholdConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in response.data

    def test_put_validates_threshold_range(self, factory, admin_user, mock_backend):
        """PUT validates threshold is between 0 and 1."""
        mock_backend.get.return_value = None

        # > 1.0 is invalid
        request = factory.put(
            "/api/self-healing/config/drift-thresholds/",
            data={"warning_threshold": 1.5},
            content_type="application/json",
        )
        request.user = admin_user

        view = DriftThresholdConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "must be between 0 and 1.0" in response.data["error"]

    def test_put_validates_threshold_type(self, factory, admin_user, mock_backend):
        """PUT validates threshold is a number."""
        mock_backend.get.return_value = None

        request = factory.put(
            "/api/self-healing/config/drift-thresholds/",
            data={"warning_threshold": "invalid"},
            content_type="application/json",
        )
        request.user = admin_user

        view = DriftThresholdConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "must be a number" in response.data["error"]

    def test_put_rejects_empty_update(self, factory, admin_user, mock_backend):
        """PUT rejects request with no valid fields."""
        mock_backend.get.return_value = None

        request = factory.put(
            "/api/self-healing/config/drift-thresholds/",
            data={"invalid_field": "value"},
            content_type="application/json",
        )
        request.user = admin_user

        view = DriftThresholdConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "No valid fields" in response.data["error"]

    def test_put_partial_update(self, factory, admin_user, mock_backend):
        """PUT allows partial update of single field."""
        mock_backend.get.return_value = {
            "warning_threshold": 0.05,
            "critical_threshold": 0.20,
            "incident_threshold": 0.50,
        }

        request = factory.put(
            "/api/self-healing/config/drift-thresholds/",
            data={"warning_threshold": 0.08},
            content_type="application/json",
        )
        request.user = admin_user

        view = DriftThresholdConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["config"]["warning_threshold"] == 0.08
        # Other values should remain unchanged
        assert response.data["config"]["critical_threshold"] == 0.20
        assert response.data["config"]["incident_threshold"] == 0.50


class TestDriftThresholdResetView:
    """Tests for DriftThresholdResetView."""

    @pytest.fixture
    def factory(self):
        return APIRequestFactory()

    @pytest.fixture
    def admin_user(self):
        user = MagicMock()
        user.is_authenticated = True
        user.is_staff = True
        user.__str__ = lambda self: "admin"
        return user

    @pytest.fixture
    def mock_backend(self):
        """Mock state backend."""
        with patch("selfhealing.api.django.views.drift_threshold.get_state_backend") as mock:
            backend = MagicMock()
            mock.return_value = backend
            yield backend

    def test_reset_returns_default_values(self, factory, admin_user, mock_backend):
        """POST /reset returns default configuration."""
        mock_backend.get.return_value = {
            "warning_threshold": 0.10,
            "critical_threshold": 0.30,
        }

        request = factory.post("/api/self-healing/config/drift-thresholds/reset/")
        request.user = admin_user

        view = DriftThresholdResetView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "reset"
        assert response.data["config"]["warning_threshold"] == 0.05
        assert response.data["config"]["critical_threshold"] == 0.20
        assert response.data["config"]["incident_threshold"] == 0.50
        assert response.data["reset_by"] == "admin"

    def test_reset_saves_to_backend(self, factory, admin_user, mock_backend):
        """POST /reset saves default config to backend."""
        mock_backend.get.return_value = None

        request = factory.post("/api/self-healing/config/drift-thresholds/reset/")
        request.user = admin_user

        view = DriftThresholdResetView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        mock_backend.set.assert_called_once()
        saved_data = mock_backend.set.call_args[0][1]
        assert saved_data["warning_threshold"] == 0.05
        assert saved_data["critical_threshold"] == 0.20
        assert saved_data["incident_threshold"] == 0.50


class TestDriftThresholdIntegration:
    """Integration tests for drift threshold with MetricReconciler."""

    @pytest.fixture
    def mock_backend(self):
        """Mock state backend."""
        with patch("selfhealing.core.state_backend.get_state_backend") as mock:
            backend = MagicMock()
            mock.return_value = backend
            yield backend

    def test_reconciler_uses_stored_config(self, mock_backend):
        """MetricReconciler uses drift config from state backend."""
        from selfhealing.metrics.reconciler import MetricReconciler, DriftResult
        from selfhealing.models.drift_config import DriftThresholdConfig

        # Create a custom config directly for the reconciler
        custom_config = DriftThresholdConfig(
            warning_threshold=0.10,
            critical_threshold=0.30,
            incident_threshold=0.60,
        )
        reconciler = MetricReconciler(drift_config=custom_config)

        # Test classification with custom thresholds
        # 25% drift should be WARNING (between 10% and 30%)
        drift = DriftResult(max_drift_percent=25.0)
        severity = reconciler._classify_drift_severity(drift)
        assert severity == "warning"

        # 45% drift should be CRITICAL (between 30% and 60%)
        drift = DriftResult(max_drift_percent=45.0)
        severity = reconciler._classify_drift_severity(drift)
        assert severity == "critical"

        # 70% drift should be INCIDENT (above 60%)
        drift = DriftResult(max_drift_percent=70.0)
        severity = reconciler._classify_drift_severity(drift)
        assert severity == "incident"

    def test_reconciler_uses_default_when_no_config(self, mock_backend):
        """MetricReconciler uses default config when none is stored."""
        from selfhealing.metrics.reconciler import MetricReconciler, DriftResult

        mock_backend.get.return_value = None
        reconciler = MetricReconciler()

        # 10% drift with default thresholds should be WARNING
        drift = DriftResult(max_drift_percent=10.0)
        severity = reconciler._classify_drift_severity(drift)
        assert severity == "warning"

        # 3% drift should be NORMAL
        drift = DriftResult(max_drift_percent=3.0)
        severity = reconciler._classify_drift_severity(drift)
        assert severity == "normal"
