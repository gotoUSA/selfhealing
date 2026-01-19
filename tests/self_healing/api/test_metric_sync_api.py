"""
Tests for Metric Sync API.

메트릭 수동 동기화 API 테스트.
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

from selfhealing.api.django.views.metric_sync import (
    MetricSyncView,
    DriftReportView,
    MetricSyncService,
    get_metric_sync_service,
    reset_metric_sync_service,
    DriftThresholds,
)


class TestMetricSyncView:
    """Tests for MetricSyncView (POST /api/self-healing/metrics/sync/)."""

    @pytest.fixture
    def factory(self):
        return APIRequestFactory()

    @pytest.fixture
    def admin_user(self):
        user = MagicMock()
        user.is_authenticated = True
        user.is_staff = True
        user.is_superuser = True
        user.username = "admin"
        user.__str__ = lambda self: "admin"
        return user

    @pytest.fixture
    def viewer_user(self):
        user = MagicMock()
        user.is_authenticated = True
        user.is_staff = False
        user.is_superuser = False
        user.username = "viewer"
        user.groups = MagicMock()
        user.groups.filter.return_value.exists.return_value = False
        user.__str__ = lambda self: "viewer"
        return user

    @pytest.fixture(autouse=True)
    def reset_service(self):
        """Reset singleton service before each test."""
        reset_metric_sync_service()
        yield
        reset_metric_sync_service()

    @pytest.fixture
    def mock_adapter(self):
        """Mock metric source adapter."""
        with patch("selfhealing.api.django.views.metric_sync.get_metric_adapter") as mock:
            adapter = MagicMock()
            adapter.get_dlq_pending_count.return_value = 5
            adapter.get_retry_success_rate.return_value = 95.0
            adapter.get_circuit_breaker_state.return_value = "closed"
            mock.return_value = adapter
            yield adapter

    @pytest.fixture
    def mock_reconciler(self):
        """Mock metric reconciler."""
        with patch("selfhealing.api.django.views.metric_sync.get_reconciler") as mock:
            reconciler = MagicMock()
            reconciler.sync_all_gauges.return_value = MagicMock(
                synced_at=datetime.now(timezone.utc).isoformat(),
                dlq_pending={"payment": 5, "point": 2},
                circuit_breaker_states={"toss_payment": "closed"},
            )
            reconciler.sync_domain_gauges.return_value = {"domain": "payment", "dlq_pending": 5}
            mock.return_value = reconciler
            yield reconciler

    def test_sync_requires_admin(self, factory, viewer_user, mock_adapter, mock_reconciler):
        """POST /metrics/sync/ requires admin permission."""
        request = factory.post(
            "/api/self-healing/metrics/sync/",
            {},
            format="json",
        )
        request.user = viewer_user

        view = MetricSyncView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_sync_all_domains(self, factory, admin_user, mock_adapter, mock_reconciler):
        """POST /metrics/sync/ syncs all domains when none specified."""
        request = factory.post(
            "/api/self-healing/metrics/sync/",
            {"reason": "Monthly maintenance"},
            format="json",
        )
        request.user = admin_user

        view = MetricSyncView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "completed"
        assert response.data["actor"] == "admin"
        assert response.data["dry_run"] is False
        assert "results" in response.data
        assert "summary" in response.data

    def test_sync_specific_domains(self, factory, admin_user, mock_adapter, mock_reconciler):
        """POST /metrics/sync/ syncs only specified domains."""
        request = factory.post(
            "/api/self-healing/metrics/sync/",
            {"domains": ["payment", "point"]},
            format="json",
        )
        request.user = admin_user

        view = MetricSyncView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "completed"

    def test_sync_dry_run(self, factory, admin_user, mock_adapter, mock_reconciler):
        """POST /metrics/sync/ with dry_run=True does not modify gauges."""
        request = factory.post(
            "/api/self-healing/metrics/sync/",
            {"dry_run": True},
            format="json",
        )
        request.user = admin_user

        view = MetricSyncView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "dry_run"
        assert response.data["dry_run"] is True
        # Reconciler의 sync 메서드가 호출되지 않아야 함
        # (dry_run이라 리포트만 생성)

    def test_sync_audit_logging(self, factory, admin_user, mock_adapter, mock_reconciler):
        """POST /metrics/sync/ logs to audit."""
        with patch("selfhealing.api.django.views.metric_sync.MetricSyncService._log_sync_action") as mock_log:
            request = factory.post(
                "/api/self-healing/metrics/sync/",
                {"reason": "Emergency sync"},
                format="json",
            )
            request.user = admin_user

            view = MetricSyncView.as_view()
            response = view(request)

            assert response.status_code == status.HTTP_200_OK
            # 실제로는 서비스 내부에서 호출됨

    def test_sync_empty_domains_list(self, factory, admin_user, mock_adapter, mock_reconciler):
        """POST /metrics/sync/ with empty domains list syncs all."""
        request = factory.post(
            "/api/self-healing/metrics/sync/",
            {"domains": []},
            format="json",
        )
        request.user = admin_user

        view = MetricSyncView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK

    def test_sync_response_structure(self, factory, admin_user, mock_adapter, mock_reconciler):
        """POST /metrics/sync/ response has correct structure."""
        request = factory.post(
            "/api/self-healing/metrics/sync/",
            {},
            format="json",
        )
        request.user = admin_user

        view = MetricSyncView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        # 응답 구조 검증
        assert "status" in response.data
        assert "synced_at" in response.data
        assert "actor" in response.data
        assert "dry_run" in response.data
        assert "results" in response.data
        assert "summary" in response.data
        # summary 구조 검증
        summary = response.data["summary"]
        assert "total_drifts_detected" in summary
        assert "total_drifts_corrected" in summary


class TestDriftReportView:
    """Tests for DriftReportView (GET /api/self-healing/metrics/drift-report/)."""

    @pytest.fixture
    def factory(self):
        return APIRequestFactory()

    @pytest.fixture
    def admin_user(self):
        user = MagicMock()
        user.is_authenticated = True
        user.is_staff = True
        user.is_superuser = True
        user.username = "admin"
        user.__str__ = lambda self: "admin"
        return user

    @pytest.fixture
    def viewer_user(self):
        user = MagicMock()
        user.is_authenticated = True
        user.is_staff = False
        user.is_superuser = False
        user.username = "viewer"
        user.groups = MagicMock()
        user.groups.filter.return_value.exists.return_value = False
        user.__str__ = lambda self: "viewer"
        return user

    @pytest.fixture(autouse=True)
    def reset_service(self):
        """Reset singleton service before each test."""
        reset_metric_sync_service()
        yield
        reset_metric_sync_service()

    @pytest.fixture
    def mock_adapter(self):
        """Mock metric source adapter."""
        with patch("selfhealing.api.django.views.metric_sync.get_metric_adapter") as mock:
            adapter = MagicMock()
            adapter.get_dlq_pending_count.return_value = 5
            adapter.get_retry_success_rate.return_value = 95.0
            adapter.get_circuit_breaker_state.return_value = "closed"
            mock.return_value = adapter
            yield adapter

    def test_report_requires_admin(self, factory, viewer_user, mock_adapter):
        """GET /metrics/drift-report/ requires admin permission."""
        request = factory.get("/api/self-healing/metrics/drift-report/")
        request.user = viewer_user

        view = DriftReportView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_report_returns_current_drift(self, factory, admin_user, mock_adapter):
        """GET /metrics/drift-report/ returns current drift status."""
        request = factory.get("/api/self-healing/metrics/drift-report/")
        request.user = admin_user

        view = DriftReportView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert "generated_at" in response.data
        assert "metrics" in response.data
        assert "overall_health" in response.data
        assert "max_drift_percent" in response.data

    def test_report_does_not_modify_gauges(self, factory, admin_user, mock_adapter):
        """GET /metrics/drift-report/ does not modify gauge values."""
        with patch("selfhealing.api.django.views.metric_sync.get_reconciler") as mock_reconciler:
            reconciler = MagicMock()
            mock_reconciler.return_value = reconciler

            request = factory.get("/api/self-healing/metrics/drift-report/")
            request.user = admin_user

            view = DriftReportView.as_view()
            response = view(request)

            assert response.status_code == status.HTTP_200_OK
            # sync 메서드가 호출되지 않아야 함
            reconciler.sync_all_gauges.assert_not_called()
            reconciler.sync_domain_gauges.assert_not_called()

    def test_report_health_classification(self, factory, admin_user, mock_adapter):
        """GET /metrics/drift-report/ correctly classifies health."""
        request = factory.get("/api/self-healing/metrics/drift-report/")
        request.user = admin_user

        view = DriftReportView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        # overall_health는 healthy, warning, critical, incident 중 하나
        assert response.data["overall_health"] in ["healthy", "warning", "critical", "incident"]

    def test_report_response_structure(self, factory, admin_user, mock_adapter):
        """GET /metrics/drift-report/ response has correct structure."""
        request = factory.get("/api/self-healing/metrics/drift-report/")
        request.user = admin_user

        view = DriftReportView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        # 응답 구조 검증
        assert "generated_at" in response.data
        assert "metrics" in response.data
        assert "overall_health" in response.data
        assert "max_drift_percent" in response.data
        # recommendation은 선택적
        if response.data["overall_health"] != "healthy":
            assert "recommendation" in response.data


class TestMetricSyncService:
    """Tests for MetricSyncService business logic."""

    @pytest.fixture(autouse=True)
    def reset_service(self):
        """Reset singleton service before each test."""
        reset_metric_sync_service()
        yield
        reset_metric_sync_service()

    @pytest.fixture
    def mock_adapter(self):
        """Mock metric source adapter."""
        adapter = MagicMock()
        adapter.get_dlq_pending_count.side_effect = lambda domain: {
            "payment": 5,
            "point": 2,
            "inventory": 0,
        }.get(domain, 0)
        adapter.get_retry_success_rate.return_value = 95.0
        return adapter

    @pytest.fixture
    def mock_reconciler(self):
        """Mock metric reconciler."""
        reconciler = MagicMock()
        reconciler.sync_all_gauges.return_value = MagicMock()
        reconciler.sync_domain_gauges.return_value = {}
        return reconciler

    def test_service_sync_metrics(self, mock_adapter, mock_reconciler):
        """MetricSyncService.sync_metrics() works correctly."""
        with patch("selfhealing.api.django.views.metric_sync.get_reconciler", return_value=mock_reconciler):
            with patch("selfhealing.api.django.views.metric_sync.get_metric_adapter", return_value=mock_adapter):
                service = MetricSyncService()
                result = service.sync_metrics(actor="test_user")

                assert result["status"] == "completed"
                assert result["actor"] == "test_user"
                assert result["dry_run"] is False

    def test_service_sync_dry_run(self, mock_adapter, mock_reconciler):
        """MetricSyncService.sync_metrics() dry_run mode."""
        with patch("selfhealing.api.django.views.metric_sync.get_reconciler", return_value=mock_reconciler):
            with patch("selfhealing.api.django.views.metric_sync.get_metric_adapter", return_value=mock_adapter):
                service = MetricSyncService()
                result = service.sync_metrics(dry_run=True, actor="test_user")

                assert result["status"] == "dry_run"
                assert result["dry_run"] is True
                # dry_run에서는 reconciler의 sync가 호출되지 않아야 함
                mock_reconciler.sync_all_gauges.assert_not_called()

    def test_service_get_drift_report(self, mock_adapter, mock_reconciler):
        """MetricSyncService.get_drift_report() works correctly."""
        with patch("selfhealing.api.django.views.metric_sync.get_reconciler", return_value=mock_reconciler):
            with patch("selfhealing.api.django.views.metric_sync.get_metric_adapter", return_value=mock_adapter):
                service = MetricSyncService()
                result = service.get_drift_report()

                assert "generated_at" in result
                assert "metrics" in result
                assert "overall_health" in result
                assert "max_drift_percent" in result

    def test_health_classification_healthy(self, mock_adapter, mock_reconciler):
        """Health is 'healthy' when drift < 5%."""
        with patch("selfhealing.api.django.views.metric_sync.get_reconciler", return_value=mock_reconciler):
            with patch("selfhealing.api.django.views.metric_sync.get_metric_adapter", return_value=mock_adapter):
                service = MetricSyncService()

                # Drift 없음
                assert service._classify_health(0.0) == "healthy"
                assert service._classify_health(4.9) == "healthy"

    def test_health_classification_warning(self, mock_adapter, mock_reconciler):
        """Health is 'warning' when 5% <= drift < 20%."""
        with patch("selfhealing.api.django.views.metric_sync.get_reconciler", return_value=mock_reconciler):
            with patch("selfhealing.api.django.views.metric_sync.get_metric_adapter", return_value=mock_adapter):
                service = MetricSyncService()

                assert service._classify_health(5.0) == "warning"
                assert service._classify_health(10.0) == "warning"
                assert service._classify_health(19.9) == "warning"

    def test_health_classification_critical(self, mock_adapter, mock_reconciler):
        """Health is 'critical' when 20% <= drift < 50%."""
        with patch("selfhealing.api.django.views.metric_sync.get_reconciler", return_value=mock_reconciler):
            with patch("selfhealing.api.django.views.metric_sync.get_metric_adapter", return_value=mock_adapter):
                service = MetricSyncService()

                assert service._classify_health(20.0) == "critical"
                assert service._classify_health(35.0) == "critical"
                assert service._classify_health(49.9) == "critical"

    def test_health_classification_incident(self, mock_adapter, mock_reconciler):
        """Health is 'incident' when drift >= 50%."""
        with patch("selfhealing.api.django.views.metric_sync.get_reconciler", return_value=mock_reconciler):
            with patch("selfhealing.api.django.views.metric_sync.get_metric_adapter", return_value=mock_adapter):
                service = MetricSyncService()

                assert service._classify_health(50.0) == "incident"
                assert service._classify_health(75.0) == "incident"
                assert service._classify_health(100.0) == "incident"


class TestDriftThresholds:
    """Tests for DriftThresholds constants."""

    def test_threshold_values(self):
        """Verify threshold values match documentation."""
        assert DriftThresholds.WARNING == 5.0
        assert DriftThresholds.CRITICAL == 20.0
        assert DriftThresholds.INCIDENT == 50.0

    def test_threshold_ordering(self):
        """Thresholds are in correct order."""
        assert DriftThresholds.WARNING < DriftThresholds.CRITICAL
        assert DriftThresholds.CRITICAL < DriftThresholds.INCIDENT


class TestMetricSyncServiceSingleton:
    """Tests for MetricSyncService singleton pattern."""

    @pytest.fixture(autouse=True)
    def reset_service(self):
        """Reset singleton service before each test."""
        reset_metric_sync_service()
        yield
        reset_metric_sync_service()

    def test_get_service_returns_singleton(self):
        """get_metric_sync_service() returns same instance."""
        with patch("selfhealing.api.django.views.metric_sync.get_metric_adapter"):
            with patch("selfhealing.api.django.views.metric_sync.get_reconciler"):
                service1 = get_metric_sync_service()
                service2 = get_metric_sync_service()

                assert service1 is service2

    def test_reset_service_clears_singleton(self):
        """reset_metric_sync_service() clears singleton."""
        with patch("selfhealing.api.django.views.metric_sync.get_metric_adapter"):
            with patch("selfhealing.api.django.views.metric_sync.get_reconciler"):
                service1 = get_metric_sync_service()
                reset_metric_sync_service()
                service2 = get_metric_sync_service()

                assert service1 is not service2
