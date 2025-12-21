"""
Tests for Metric Reconciler.

Reference: docs/self_healing/13_METRIC_COLLECTION_STRATEGY.md
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone

from selfhealing.metrics.reconciler import (
    MetricReconciler,
    DriftSeverity,
    DriftResult,
    SyncResult,
    get_reconciler,
    reset_reconciler,
)
from selfhealing.adapters.metrics.base import NullMetricSourceAdapter
from selfhealing.models.drift_config import DriftThresholdConfig


class TestDriftSeverity:
    """Tests for DriftSeverity constants."""

    def test_severity_levels(self):
        """Should have correct severity level values."""
        assert DriftSeverity.NORMAL == "normal"
        assert DriftSeverity.WARNING == "warning"
        assert DriftSeverity.CRITICAL == "critical"
        assert DriftSeverity.INCIDENT == "incident"


class TestDriftResult:
    """Tests for DriftResult dataclass."""

    def test_default_values(self):
        """Should have sensible defaults."""
        result = DriftResult()
        assert result.details == {}
        assert result.max_drift_percent == 0.0
        assert result.severity == DriftSeverity.NORMAL
        assert result.calculated_at is not None

    def test_custom_values(self):
        """Should accept custom values."""
        result = DriftResult(
            details={"payment": {"before": 0, "after": 10}},
            max_drift_percent=50.0,
            severity=DriftSeverity.CRITICAL,
        )
        assert result.max_drift_percent == 50.0
        assert result.severity == DriftSeverity.CRITICAL


class TestSyncResult:
    """Tests for SyncResult dataclass."""

    def test_default_values(self):
        """Should have sensible defaults."""
        result = SyncResult()
        assert result.dlq_pending == {}
        assert result.circuit_breaker_states == {}
        assert result.retry_success_rates == {}
        assert result.drift is None
        assert result.synced_at is not None


class TestMetricReconciler:
    """Tests for MetricReconciler class."""

    def setup_method(self):
        """Reset reconciler before each test."""
        reset_reconciler()

    def teardown_method(self):
        """Clean up after each test."""
        reset_reconciler()

    def test_init_with_null_adapter(self):
        """Should work with NullMetricSourceAdapter."""
        adapter = NullMetricSourceAdapter()
        reconciler = MetricReconciler(adapter=adapter)

        assert reconciler.adapter is adapter

    @patch("selfhealing.metrics.reconciler.get_metric_adapter")
    def test_init_without_adapter_uses_factory(self, mock_factory):
        """Should use factory when no adapter provided."""
        mock_adapter = Mock()
        mock_factory.return_value = mock_adapter

        reconciler = MetricReconciler()

        mock_factory.assert_called_once()
        assert reconciler.adapter is mock_adapter

    def test_sync_all_gauges_with_null_adapter(self):
        """sync_all_gauges should work with NullAdapter."""
        adapter = NullMetricSourceAdapter()
        reconciler = MetricReconciler(
            adapter=adapter,
            domains=["payment", "point"],
            services=["api_service"],
        )

        result = reconciler.sync_all_gauges()

        assert isinstance(result, SyncResult)
        assert "payment" in result.dlq_pending
        assert result.dlq_pending["payment"] == 0

    def test_sync_domain_gauges(self):
        """sync_domain_gauges should sync specific domain."""
        adapter = Mock()
        adapter.get_dlq_pending_count.return_value = 5
        adapter.get_retry_success_rate.return_value = 85.0

        reconciler = MetricReconciler(adapter=adapter)
        result = reconciler.sync_domain_gauges("payment")

        assert result["domain"] == "payment"
        assert result["dlq_pending"] == 5
        assert result["retry_rate"] == 85.0

    def test_last_sync_time_updated(self):
        """last_sync_time should be updated after sync."""
        adapter = NullMetricSourceAdapter()
        reconciler = MetricReconciler(adapter=adapter, domains=["test"])

        assert reconciler.last_sync_time is None

        reconciler.sync_all_gauges()

        assert reconciler.last_sync_time is not None
        assert isinstance(reconciler.last_sync_time, datetime)

    def test_classify_drift_severity_normal(self):
        """Should classify small drift as normal."""
        config = DriftThresholdConfig()
        reconciler = MetricReconciler(
            adapter=NullMetricSourceAdapter(),
            drift_config=config,
        )

        drift = DriftResult(max_drift_percent=3.0)
        severity = reconciler._classify_drift_severity(drift)

        assert severity == DriftSeverity.NORMAL

    def test_classify_drift_severity_warning(self):
        """Should classify 5-20% drift as warning."""
        config = DriftThresholdConfig()
        reconciler = MetricReconciler(
            adapter=NullMetricSourceAdapter(),
            drift_config=config,
        )

        drift = DriftResult(max_drift_percent=10.0)
        severity = reconciler._classify_drift_severity(drift)

        assert severity == DriftSeverity.WARNING

    def test_classify_drift_severity_critical(self):
        """Should classify 20-50% drift as critical."""
        config = DriftThresholdConfig()
        reconciler = MetricReconciler(
            adapter=NullMetricSourceAdapter(),
            drift_config=config,
        )

        drift = DriftResult(max_drift_percent=30.0)
        severity = reconciler._classify_drift_severity(drift)

        assert severity == DriftSeverity.CRITICAL

    def test_classify_drift_severity_incident(self):
        """Should classify >50% drift as incident."""
        config = DriftThresholdConfig()
        reconciler = MetricReconciler(
            adapter=NullMetricSourceAdapter(),
            drift_config=config,
        )

        drift = DriftResult(max_drift_percent=60.0)
        severity = reconciler._classify_drift_severity(drift)

        assert severity == DriftSeverity.INCIDENT


class TestGetReconciler:
    """Tests for get_reconciler function."""

    def setup_method(self):
        """Reset reconciler before each test."""
        reset_reconciler()

    def teardown_method(self):
        """Clean up after each test."""
        reset_reconciler()

    def test_returns_singleton_instance(self):
        """Should return the same instance on multiple calls."""
        r1 = get_reconciler()
        r2 = get_reconciler()

        assert r1 is r2

    def test_reset_clears_singleton(self):
        """reset_reconciler should clear the singleton."""
        r1 = get_reconciler()
        reset_reconciler()
        r2 = get_reconciler()

        assert r1 is not r2
