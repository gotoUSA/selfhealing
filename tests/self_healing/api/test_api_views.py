"""
Unit Tests for Self-Healing API Views.

Phase 3: API view tests for health, circuit breaker, DLQ, and dashboard endpoints.
Tests focus on view structure and permission checks.
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()


from selfhealing.api.django.views.health import (
    SelfHealingHealthView,
    LivenessView,
    ReadinessView,
    ConnectionPoolHealthView,
    SelfHealingMetricsView,
)
from selfhealing.api.django.views.circuit_breaker import (
    ControlActionView,
    ControlStatusView,
    ServiceStatusView,
    ControlAuditView,
    QuickAllowView,
    QuickBlockView,
    QuickResetView,
)
from selfhealing.api.django.views.dlq import (
    DLQReplayView,
    DLQCleanupStatsView,
    DLQArchiveView,
    DLQPurgeView,
    DLQListView,
    DLQDetailView,
    DLQRetryView,
    DLQResolveView,
)
from selfhealing.api.django.views.dashboard import DashboardSummaryView


class TestHealthViews:
    """Tests for health-related views."""

    def test_selfhealing_health_view_exists(self):
        """Test SelfHealingHealthView exists and is callable."""
        assert SelfHealingHealthView is not None
        assert hasattr(SelfHealingHealthView, "get")

    def test_liveness_view_exists(self):
        """Test LivenessView exists and is callable."""
        assert LivenessView is not None
        assert hasattr(LivenessView, "get")

    def test_readiness_view_exists(self):
        """Test ReadinessView exists and is callable."""
        assert ReadinessView is not None
        assert hasattr(ReadinessView, "get")

    def test_connection_pool_health_view_exists(self):
        """Test ConnectionPoolHealthView exists and is callable."""
        assert ConnectionPoolHealthView is not None
        assert hasattr(ConnectionPoolHealthView, "get")

    def test_metrics_view_has_permissions(self):
        """Test SelfHealingMetricsView has permission classes."""
        assert hasattr(SelfHealingMetricsView, "permission_classes")

    def test_health_view_is_public(self):
        """Test SelfHealingHealthView has empty permission classes (public)."""
        assert hasattr(SelfHealingHealthView, "permission_classes")
        # Health endpoint should be public
        assert SelfHealingHealthView.permission_classes == []


class TestCircuitBreakerViews:
    """Tests for circuit breaker control views."""

    def test_control_action_view_exists(self):
        """Test ControlActionView exists and is callable."""
        assert ControlActionView is not None
        assert hasattr(ControlActionView, "post")

    def test_control_action_requires_auth(self):
        """Test ControlActionView requires authentication."""
        assert hasattr(ControlActionView, "permission_classes")
        assert len(ControlActionView.permission_classes) > 0

    def test_control_status_view_exists(self):
        """Test ControlStatusView exists and is callable."""
        assert ControlStatusView is not None
        assert hasattr(ControlStatusView, "get")

    def test_service_status_view_exists(self):
        """Test ServiceStatusView exists and is callable."""
        assert ServiceStatusView is not None
        assert hasattr(ServiceStatusView, "get")

    def test_control_audit_view_exists(self):
        """Test ControlAuditView exists and is callable."""
        assert ControlAuditView is not None
        assert hasattr(ControlAuditView, "get")

    def test_quick_allow_view_exists(self):
        """Test QuickAllowView exists and is callable."""
        assert QuickAllowView is not None
        assert hasattr(QuickAllowView, "post")

    def test_quick_block_view_exists(self):
        """Test QuickBlockView exists and is callable."""
        assert QuickBlockView is not None
        assert hasattr(QuickBlockView, "post")

    def test_quick_reset_view_exists(self):
        """Test QuickResetView exists and is callable."""
        assert QuickResetView is not None
        assert hasattr(QuickResetView, "post")


class TestDLQViews:
    """Tests for DLQ views."""

    def test_dlq_list_view_exists(self):
        """Test DLQListView exists and is callable."""
        assert DLQListView is not None
        assert hasattr(DLQListView, "get")

    def test_dlq_detail_view_exists(self):
        """Test DLQDetailView exists and is callable."""
        assert DLQDetailView is not None
        assert hasattr(DLQDetailView, "get")

    def test_dlq_retry_view_exists(self):
        """Test DLQRetryView exists and is callable."""
        assert DLQRetryView is not None
        assert hasattr(DLQRetryView, "post")

    def test_dlq_resolve_view_exists(self):
        """Test DLQResolveView exists and is callable."""
        assert DLQResolveView is not None
        assert hasattr(DLQResolveView, "post")

    def test_dlq_replay_view_exists(self):
        """Test DLQReplayView exists and is callable."""
        assert DLQReplayView is not None
        assert hasattr(DLQReplayView, "post")

    def test_dlq_cleanup_stats_view_exists(self):
        """Test DLQCleanupStatsView exists and is callable."""
        assert DLQCleanupStatsView is not None
        assert hasattr(DLQCleanupStatsView, "get")

    def test_dlq_archive_view_exists(self):
        """Test DLQArchiveView exists and is callable."""
        assert DLQArchiveView is not None
        assert hasattr(DLQArchiveView, "post")

    def test_dlq_purge_view_exists(self):
        """Test DLQPurgeView exists and is callable."""
        assert DLQPurgeView is not None

    def test_dlq_list_requires_auth(self):
        """Test DLQListView requires authentication."""
        assert hasattr(DLQListView, "permission_classes")
        assert len(DLQListView.permission_classes) > 0


class TestDashboardViews:
    """Tests for dashboard views."""

    def test_dashboard_summary_view_exists(self):
        """Test DashboardSummaryView exists and is callable."""
        assert DashboardSummaryView is not None
        assert hasattr(DashboardSummaryView, "get")

    def test_dashboard_requires_auth(self):
        """Test DashboardSummaryView requires authentication."""
        assert hasattr(DashboardSummaryView, "permission_classes")
        assert len(DashboardSummaryView.permission_classes) > 0
