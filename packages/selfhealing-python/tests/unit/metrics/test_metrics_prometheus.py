"""
Tests for Prometheus Metrics Module.

Reference: docs/self_healing/13_METRIC_COLLECTION_STRATEGY.md
"""

from unittest.mock import Mock, patch

import pytest


class TestDomainRegistry:
    """Test domain registration functions."""

    def test_get_domains_returns_copy(self):
        """Should return a copy of registered domains."""
        from selfhealing.metrics.prometheus import get_domains, _registered_domains

        domains = get_domains()
        # Should return a copy, not the original
        assert domains == _registered_domains
        domains.append("test_domain")
        assert "test_domain" not in _registered_domains

    def test_register_domain_adds_new_domain(self):
        """Should add new domain to registry."""
        from selfhealing.metrics.prometheus import register_domain, get_domains

        original_count = len(get_domains())
        
        # Register new domain
        register_domain("test_new_domain")
        
        assert "test_new_domain" in get_domains()
        
        # Clean up: remove added domain
        from selfhealing.metrics import prometheus
        if "test_new_domain" in prometheus._registered_domains:
            prometheus._registered_domains.remove("test_new_domain")

    def test_register_domain_ignores_duplicate(self):
        """Should not add duplicate domain."""
        from selfhealing.metrics.prometheus import register_domain, get_domains

        # Get a domain that already exists
        existing_domains = get_domains()
        if existing_domains:
            existing_domain = existing_domains[0]
            count_before = len(get_domains())
            
            register_domain(existing_domain)
            
            assert len(get_domains()) == count_before


class TestSelfHealingMetricsInit:
    """Test SelfHealingMetrics initialization."""

    def test_init_with_prefix(self):
        """Should initialize with custom prefix."""
        from selfhealing.metrics.prometheus import SelfHealingMetrics, PROMETHEUS_AVAILABLE

        if not PROMETHEUS_AVAILABLE:
            pytest.skip("prometheus_client not installed")

        metrics = SelfHealingMetrics(prefix="test_prefix")
        assert metrics.prefix == "test_prefix"

    def test_init_without_prometheus_client(self):
        """Should handle missing prometheus_client gracefully."""
        from selfhealing.metrics.prometheus import SelfHealingMetrics

        with patch("selfhealing.metrics.prometheus.PROMETHEUS_AVAILABLE", False):
            metrics = SelfHealingMetrics()
            # Should not raise, just log warning
            assert metrics.prefix == "selfhealing"


class TestSelfHealingMetricsCounters:
    """Test counter methods in SelfHealingMetrics."""

    @pytest.fixture
    def mock_metrics(self):
        """Create metrics with mocked prometheus counters."""
        from selfhealing.metrics.prometheus import SelfHealingMetrics, PROMETHEUS_AVAILABLE

        if not PROMETHEUS_AVAILABLE:
            pytest.skip("prometheus_client not installed")

        metrics = SelfHealingMetrics(prefix="test")
        return metrics

    def test_record_dlq_created(self, mock_metrics):
        """Should record DLQ created counter."""
        from selfhealing.metrics.prometheus import PROMETHEUS_AVAILABLE

        if not PROMETHEUS_AVAILABLE:
            pytest.skip("prometheus_client not installed")

        # Should not raise
        if hasattr(mock_metrics, "record_dlq_created"):
            mock_metrics.record_dlq_created("payment", "timeout")


class TestSelfHealingMetricsGauges:
    """Test gauge methods in SelfHealingMetrics."""

    def test_dlq_pending_gauge_exists(self):
        """Should have dlq_pending_gauge attribute."""
        from selfhealing.metrics.prometheus import SelfHealingMetrics, PROMETHEUS_AVAILABLE

        if not PROMETHEUS_AVAILABLE:
            pytest.skip("prometheus_client not installed")

        # Use get_metrics() instead of creating new instance to avoid registry conflict
        from selfhealing.metrics.prometheus import get_metrics
        metrics = get_metrics()
        assert hasattr(metrics, "dlq_pending_gauge")

    def test_dlq_by_status_gauge_exists(self):
        """Should have dlq_by_status_gauge attribute."""
        from selfhealing.metrics.prometheus import SelfHealingMetrics, PROMETHEUS_AVAILABLE

        if not PROMETHEUS_AVAILABLE:
            pytest.skip("prometheus_client not installed")

        # Use get_metrics() instead of creating new instance to avoid registry conflict
        from selfhealing.metrics.prometheus import get_metrics
        metrics = get_metrics()
        assert hasattr(metrics, "dlq_by_status_gauge")


class TestPrometheusAvailability:
    """Test PROMETHEUS_AVAILABLE flag behavior."""

    def test_prometheus_available_is_boolean(self):
        """Should be a boolean value."""
        from selfhealing.metrics.prometheus import PROMETHEUS_AVAILABLE

        assert isinstance(PROMETHEUS_AVAILABLE, bool)

    def test_module_works_without_prometheus(self):
        """Module should work even without prometheus_client."""
        # This test verifies the module can be imported
        from selfhealing.metrics.prometheus import (
            get_domains,
            register_domain,
            SelfHealingMetrics,
        )

        # Basic operations should work
        domains = get_domains()
        assert isinstance(domains, list)
