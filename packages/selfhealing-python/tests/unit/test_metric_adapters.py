"""
Tests for Metric Source Adapters.

Reference: docs/self_healing/13_METRIC_COLLECTION_STRATEGY.md
"""

import pytest
from unittest.mock import Mock, patch, MagicMock

from selfhealing.adapters.metrics.base import (
    MetricSourceAdapter,
    BaseMetricSourceAdapter,
    NullMetricSourceAdapter,
)
from selfhealing.adapters.metrics.factory import (
    get_metric_adapter,
    configure_adapter,
    reset_adapter,
)


class TestNullMetricSourceAdapter:
    """Tests for NullMetricSourceAdapter."""

    def test_get_dlq_pending_count_returns_zero(self):
        """NullAdapter should always return 0 for DLQ pending count."""
        adapter = NullMetricSourceAdapter()
        assert adapter.get_dlq_pending_count("payment") == 0
        assert adapter.get_dlq_pending_count("any_domain") == 0

    def test_get_dlq_count_by_status_returns_zero(self):
        """NullAdapter should always return 0 for DLQ count by status."""
        adapter = NullMetricSourceAdapter()
        assert adapter.get_dlq_count_by_status("pending") == 0
        assert adapter.get_dlq_count_by_status("resolved") == 0

    def test_get_circuit_breaker_state_returns_closed(self):
        """NullAdapter should always return 'closed' for CB state."""
        adapter = NullMetricSourceAdapter()
        assert adapter.get_circuit_breaker_state("service_a") == "closed"
        assert adapter.get_circuit_breaker_state("any_service") == "closed"

    def test_get_retry_success_rate_returns_zero(self):
        """NullAdapter should always return 0.0 for retry success rate."""
        adapter = NullMetricSourceAdapter()
        assert adapter.get_retry_success_rate("payment") == 0.0


class TestAdapterFactory:
    """Tests for adapter factory functions."""

    def setup_method(self):
        """Reset adapter before each test."""
        reset_adapter()

    def teardown_method(self):
        """Clean up after each test."""
        reset_adapter()

    def test_get_metric_adapter_returns_null_by_default(self):
        """Default adapter should be NullMetricSourceAdapter."""
        adapter = get_metric_adapter()
        assert isinstance(adapter, NullMetricSourceAdapter)

    def test_configure_adapter_sets_custom_adapter(self):
        """configure_adapter should set a custom adapter."""

        class CustomAdapter(BaseMetricSourceAdapter):
            def get_dlq_pending_count(self, domain: str) -> int:
                return 42

            def get_dlq_count_by_status(self, status: str) -> int:
                return 10

        custom = CustomAdapter()
        configure_adapter(custom)

        adapter = get_metric_adapter()
        assert adapter is custom
        assert adapter.get_dlq_pending_count("any") == 42

    def test_get_metric_adapter_returns_same_instance(self):
        """get_metric_adapter should return singleton."""
        adapter1 = get_metric_adapter()
        adapter2 = get_metric_adapter()
        assert adapter1 is adapter2

    @patch.dict("os.environ", {"SELFHEALING_METRICS_ADAPTER_TYPE": "null"})
    def test_adapter_type_null_from_env(self):
        """Should use NullAdapter when type is 'null'."""
        reset_adapter()
        adapter = get_metric_adapter()
        assert isinstance(adapter, NullMetricSourceAdapter)


class TestMetricSourceAdapterProtocol:
    """Tests for MetricSourceAdapter protocol compliance."""

    def test_null_adapter_implements_protocol(self):
        """NullMetricSourceAdapter should implement MetricSourceAdapter protocol."""
        adapter = NullMetricSourceAdapter()
        assert isinstance(adapter, MetricSourceAdapter)

    def test_custom_adapter_implements_protocol(self):
        """Custom adapter implementing required methods should pass protocol check."""

        class CustomAdapter:
            def get_dlq_pending_count(self, domain: str) -> int:
                return 0

            def get_dlq_count_by_status(self, status: str) -> int:
                return 0

            def get_circuit_breaker_state(self, service: str) -> str:
                return "closed"

            def get_retry_success_rate(self, domain: str) -> float:
                return 0.0

        adapter = CustomAdapter()
        assert isinstance(adapter, MetricSourceAdapter)
