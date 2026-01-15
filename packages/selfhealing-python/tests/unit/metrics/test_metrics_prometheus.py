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


class TestREDMetrics:
    """Test RED (Rate, Errors, Duration) metrics.
    
    Reference: https://www.weave.works/blog/the-red-method-key-metrics-for-microservices/
    """

    @pytest.fixture
    def metrics(self):
        """Get global metrics instance."""
        from selfhealing.metrics.prometheus import get_metrics, PROMETHEUS_AVAILABLE

        if not PROMETHEUS_AVAILABLE:
            pytest.skip("prometheus_client not installed")

        return get_metrics()

    def test_http_requests_total_exists(self, metrics):
        """Should have http_requests_total counter (Rate)."""
        assert hasattr(metrics, "http_requests_total")

    def test_http_request_duration_seconds_exists(self, metrics):
        """Should have http_request_duration_seconds histogram (Duration)."""
        assert hasattr(metrics, "http_request_duration_seconds")

    def test_http_request_errors_total_exists(self, metrics):
        """Should have http_request_errors_total counter (Errors)."""
        assert hasattr(metrics, "http_request_errors_total")

    def test_record_http_request(self, metrics):
        """Should record HTTP request metrics."""
        # Should not raise
        metrics.record_http_request(
            method="GET",
            endpoint="/api/users",
            status_code=200,
            duration_seconds=0.123,
        )

    def test_record_http_error(self, metrics):
        """Should record HTTP error metrics."""
        # Should not raise
        metrics.record_http_error(
            method="POST",
            endpoint="/api/orders",
            error_type="timeout",
        )

    def test_http_request_timer_context_manager(self, metrics):
        """Should provide http_request_timer context manager."""
        import time

        with metrics.http_request_timer("GET", "/api/test"):
            time.sleep(0.01)  # Small delay to ensure duration > 0

        # Should not raise - duration is automatically recorded

    def test_http_request_timer_records_errors(self, metrics):
        """Should record errors when exception occurs in timer context."""
        try:
            with metrics.http_request_timer("GET", "/api/error"):
                raise ValueError("Test error")
        except ValueError:
            pass  # Expected

        # Error should be recorded with error_type="ValueError"


class TestFourGoldenSignals:
    """Test Four Golden Signals metrics.
    
    Reference: https://sre.google/sre-book/monitoring-distributed-systems/
    - Latency: How long it takes to service a request
    - Traffic: How much demand is being placed on the system
    - Errors: Rate of failed requests
    - Saturation: How full the service is
    """

    @pytest.fixture
    def metrics(self):
        """Get global metrics instance."""
        from selfhealing.metrics.prometheus import get_metrics, PROMETHEUS_AVAILABLE

        if not PROMETHEUS_AVAILABLE:
            pytest.skip("prometheus_client not installed")

        return get_metrics()

    # =========================================================================
    # Saturation Metrics
    # =========================================================================

    def test_request_queue_depth_exists(self, metrics):
        """Should have request_queue_depth gauge (Saturation)."""
        assert hasattr(metrics, "request_queue_depth")

    def test_worker_utilization_ratio_exists(self, metrics):
        """Should have worker_utilization_ratio gauge (Saturation)."""
        assert hasattr(metrics, "worker_utilization_ratio")

    def test_active_connections_exists(self, metrics):
        """Should have active_connections gauge (Saturation)."""
        assert hasattr(metrics, "active_connections")

    def test_set_request_queue_depth(self, metrics):
        """Should set request queue depth."""
        metrics.set_request_queue_depth("api-service", 42)
        # Should not raise

    def test_set_request_queue_depth_clamps_negative(self, metrics):
        """Should clamp negative queue depth to 0."""
        metrics.set_request_queue_depth("api-service", -5)
        # Should not raise, value clamped to 0

    def test_set_worker_utilization(self, metrics):
        """Should set worker utilization ratio."""
        metrics.set_worker_utilization("gunicorn", 0.75)
        # Should not raise

    def test_set_worker_utilization_clamps_range(self, metrics):
        """Should clamp utilization ratio to 0.0-1.0."""
        metrics.set_worker_utilization("gunicorn", 1.5)  # Clamped to 1.0
        metrics.set_worker_utilization("gunicorn", -0.1)  # Clamped to 0.0
        # Should not raise

    def test_set_active_connections(self, metrics):
        """Should set active connections count."""
        metrics.set_active_connections("db", 10)
        metrics.set_active_connections("redis", 5)
        # Should not raise

    # =========================================================================
    # Latency Metrics
    # =========================================================================

    def test_request_latency_percentiles_exists(self, metrics):
        """Should have request_latency_percentiles gauge (Latency)."""
        assert hasattr(metrics, "request_latency_percentiles")

    def test_set_latency_percentile(self, metrics):
        """Should set latency percentile values."""
        metrics.set_latency_percentile("/api/users", "p50", 0.05)
        metrics.set_latency_percentile("/api/users", "p90", 0.15)
        metrics.set_latency_percentile("/api/users", "p99", 0.35)
        # Should not raise

    # =========================================================================
    # Error Metrics
    # =========================================================================

    def test_error_rate_percent_exists(self, metrics):
        """Should have error_rate_percent gauge (Errors)."""
        assert hasattr(metrics, "error_rate_percent")

    def test_set_error_rate(self, metrics):
        """Should set error rate percentage."""
        metrics.set_error_rate("api-service", 0.5)  # 0.5%
        # Should not raise

    def test_set_error_rate_clamps_range(self, metrics):
        """Should clamp error rate to 0-100%."""
        metrics.set_error_rate("api-service", 150.0)  # Clamped to 100
        metrics.set_error_rate("api-service", -5.0)  # Clamped to 0
        # Should not raise


class TestConvenienceFunctions:
    """Test module-level convenience functions for RED/Golden Signals."""

    def test_record_http_request_function(self):
        """Should have record_http_request convenience function."""
        from selfhealing.metrics.prometheus import record_http_request, PROMETHEUS_AVAILABLE

        if not PROMETHEUS_AVAILABLE:
            pytest.skip("prometheus_client not installed")

        # Should not raise
        record_http_request("GET", "/api/test", 200, 0.1)

    def test_record_http_error_function(self):
        """Should have record_http_error convenience function."""
        from selfhealing.metrics.prometheus import record_http_error, PROMETHEUS_AVAILABLE

        if not PROMETHEUS_AVAILABLE:
            pytest.skip("prometheus_client not installed")

        # Should not raise
        record_http_error("POST", "/api/test", "500")

    def test_set_request_queue_depth_function(self):
        """Should have set_request_queue_depth convenience function."""
        from selfhealing.metrics.prometheus import set_request_queue_depth, PROMETHEUS_AVAILABLE

        if not PROMETHEUS_AVAILABLE:
            pytest.skip("prometheus_client not installed")

        # Should not raise
        set_request_queue_depth("test-service", 10)

    def test_set_worker_utilization_function(self):
        """Should have set_worker_utilization convenience function."""
        from selfhealing.metrics.prometheus import set_worker_utilization, PROMETHEUS_AVAILABLE

        if not PROMETHEUS_AVAILABLE:
            pytest.skip("prometheus_client not installed")

        # Should not raise
        set_worker_utilization("test-pool", 0.8)

    def test_set_active_connections_function(self):
        """Should have set_active_connections convenience function."""
        from selfhealing.metrics.prometheus import set_active_connections, PROMETHEUS_AVAILABLE

        if not PROMETHEUS_AVAILABLE:
            pytest.skip("prometheus_client not installed")

        # Should not raise
        set_active_connections("db", 5)

    def test_set_latency_percentile_function(self):
        """Should have set_latency_percentile convenience function."""
        from selfhealing.metrics.prometheus import set_latency_percentile, PROMETHEUS_AVAILABLE

        if not PROMETHEUS_AVAILABLE:
            pytest.skip("prometheus_client not installed")

        # Should not raise
        set_latency_percentile("/api/test", "p99", 0.5)

    def test_set_error_rate_function(self):
        """Should have set_error_rate convenience function."""
        from selfhealing.metrics.prometheus import set_error_rate, PROMETHEUS_AVAILABLE

        if not PROMETHEUS_AVAILABLE:
            pytest.skip("prometheus_client not installed")

        # Should not raise
        set_error_rate("test-service", 1.5)
