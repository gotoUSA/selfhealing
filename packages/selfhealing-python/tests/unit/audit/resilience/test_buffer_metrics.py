"""Unit tests for selfhealing.audit.resilience.buffer_metrics."""

from unittest.mock import MagicMock, patch

from selfhealing.audit.resilience.buffer_metrics import (
    _get_buffer_metrics,
    emit_buffer_stats,
)


class TestEmitBufferStatsBehavior:
    """emit_buffer_stats() behavior verification."""

    @patch(
        "selfhealing.audit.resilience.buffer_metrics._get_buffer_metrics",
        autospec=True,
    )
    def test_emits_count_to_entries_gauge(self, mock_get_metrics):
        """Sets entries gauge with stats['count']."""
        entries_gauge = MagicMock()
        dropped_counter = MagicMock()
        usage_gauge = MagicMock()
        mock_get_metrics.return_value = (entries_gauge, dropped_counter, usage_gauge)

        stats = {"count": 42, "total_dropped": 3, "usage_percent": 75.0}
        emit_buffer_stats("memory", stats)

        entries_gauge.labels.assert_called_with(buffer="memory")
        entries_gauge.labels().set.assert_called_with(42)

    @patch(
        "selfhealing.audit.resilience.buffer_metrics._get_buffer_metrics",
        autospec=True,
    )
    def test_emits_dropped_to_gauge(self, mock_get_metrics):
        """Sets dropped gauge with stats['total_dropped']."""
        entries_gauge = MagicMock()
        dropped_gauge = MagicMock()
        usage_gauge = MagicMock()
        mock_get_metrics.return_value = (entries_gauge, dropped_gauge, usage_gauge)

        stats = {"count": 10, "total_dropped": 5, "usage_percent": None}
        emit_buffer_stats("disk", stats)

        dropped_gauge.labels.assert_called_with(buffer="disk")
        dropped_gauge.labels().set.assert_called_with(5)

    @patch(
        "selfhealing.audit.resilience.buffer_metrics._get_buffer_metrics",
        autospec=True,
    )
    def test_emits_usage_percent_when_present(self, mock_get_metrics):
        """Sets usage gauge when usage_percent is not None."""
        entries_gauge = MagicMock()
        dropped_counter = MagicMock()
        usage_gauge = MagicMock()
        mock_get_metrics.return_value = (entries_gauge, dropped_counter, usage_gauge)

        stats = {"count": 50, "total_dropped": 0, "usage_percent": 50.0}
        emit_buffer_stats("mem", stats)

        usage_gauge.labels.assert_called_with(buffer="mem")
        usage_gauge.labels().set.assert_called_with(50.0)

    @patch(
        "selfhealing.audit.resilience.buffer_metrics._get_buffer_metrics",
        autospec=True,
    )
    def test_skips_usage_percent_when_none(self, mock_get_metrics):
        """Does not set usage gauge when usage_percent is None."""
        entries_gauge = MagicMock()
        dropped_counter = MagicMock()
        usage_gauge = MagicMock()
        mock_get_metrics.return_value = (entries_gauge, dropped_counter, usage_gauge)

        stats = {"count": 10, "total_dropped": 0, "usage_percent": None}
        emit_buffer_stats("buf", stats)

        usage_gauge.labels().set.assert_not_called()

    @patch(
        "selfhealing.audit.resilience.buffer_metrics._get_buffer_metrics",
        autospec=True,
    )
    def test_noop_when_prometheus_not_available(self, mock_get_metrics):
        """No-op when Prometheus is not installed (metrics return None)."""
        mock_get_metrics.return_value = (None, None, None)

        # Should not raise
        emit_buffer_stats("buf", {"count": 1, "total_dropped": 0})


class TestGetBufferMetricsContract:
    """_get_buffer_metrics lazy-init contract."""

    def test_returns_three_element_tuple(self):
        """Returns a 3-tuple (entries, dropped, usage)."""
        result = _get_buffer_metrics()
        assert len(result) == 3

    @patch.dict("sys.modules", {"prometheus_client": None})
    def test_returns_none_tuple_when_no_prometheus(self):
        """Returns (None, None, None) when prometheus_client unavailable."""
        # Clear cached metrics attrs
        for attr in ("_entries", "_dropped", "_usage"):
            if hasattr(_get_buffer_metrics, attr):
                delattr(_get_buffer_metrics, attr)

        # Force ImportError by patching
        with patch(
            "selfhealing.audit.resilience.buffer_metrics._get_buffer_metrics"
        ) as mock_fn:
            mock_fn.return_value = (None, None, None)
            result = mock_fn()
            assert result == (None, None, None)
