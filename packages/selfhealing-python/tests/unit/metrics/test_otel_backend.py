"""Unit tests for metrics/otel_backend.py — OTEL Meter-based metrics.

Tests _GaugeStore thread-safety and OTELSelfHealingMetrics initialization
and recording methods added in commit cf89883a.

Reference:
    docs/self_healing/middleware_system/316_GUNICORN_PRELOAD_OPTIMIZATION.md §5.8
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.metrics.otel_backend import OTELSelfHealingMetrics, _GaugeStore


class TestGaugeStoreContract:
    """Contract: _GaugeStore stores values keyed by sorted attribute tuples."""

    def test_set_without_attributes_uses_empty_key(self):
        """set(value) with no attributes uses empty tuple as key."""
        store = _GaugeStore()
        store.set(42.0)
        assert store._values[()] == 42.0

    def test_set_with_attributes_sorts_keys(self):
        """Attributes are sorted by key for deterministic lookup."""
        store = _GaugeStore()
        store.set(10.0, {"z_key": "z", "a_key": "a"})
        expected_key = (("a_key", "a"), ("z_key", "z"))
        assert expected_key in store._values

    def test_overwrite_same_key(self):
        """Setting same attributes twice overwrites the value."""
        store = _GaugeStore()
        store.set(1.0, {"service": "api"})
        store.set(2.0, {"service": "api"})
        key = (("service", "api"),)
        assert store._values[key] == 2.0


class TestGaugeStoreCallbackBehavior:
    """Behavior: callback returns Observation list for OTEL SDK."""

    def test_callback_returns_observations_for_all_stored_values(self):
        """callback() returns one Observation per stored key."""
        store = _GaugeStore()
        store.set(1.0, {"a": "1"})
        store.set(2.0, {"b": "2"})

        mock_observation = MagicMock()
        with patch("opentelemetry.metrics.Observation", mock_observation):
            results = store.callback(options=None)

        assert len(results) == 2
        assert mock_observation.call_count == 2

    def test_callback_empty_store_returns_empty_list(self):
        """Empty store → empty observation list."""
        store = _GaugeStore()
        with patch("opentelemetry.metrics.Observation", MagicMock()):
            results = store.callback(options=None)
        assert results == []


class TestGaugeStoreThreadSafetyBehavior:
    """Behavior: concurrent set() calls do not corrupt data."""

    def test_concurrent_set_no_data_corruption(self):
        """10 threads writing simultaneously produce no errors."""
        store = _GaugeStore()
        errors = []

        def worker(thread_id):
            try:
                for i in range(100):
                    store.set(float(i), {"thread": str(thread_id)})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(store._values) == 10


class TestOTELSelfHealingMetricsInitBehavior:
    """Behavior: initialization with/without OTEL meter."""

    @patch("selfhealing.observability.get_meter", return_value=None)
    def test_uninitialized_when_meter_is_none(self, _):
        """When get_meter() returns None, _initialized stays False."""
        metrics = OTELSelfHealingMetrics()
        assert metrics._initialized is False

    @patch("selfhealing.observability.get_meter", side_effect=ImportError)
    def test_uninitialized_on_import_error(self, _):
        """Import error during init → _initialized stays False, no crash."""
        metrics = OTELSelfHealingMetrics()
        assert metrics._initialized is False

    @patch("selfhealing.observability.get_meter")
    def test_initialized_when_meter_available(self, mock_get_meter):
        """When get_meter() returns a valid Meter, _initialized is True."""
        mock_meter = MagicMock()
        mock_get_meter.return_value = mock_meter

        metrics = OTELSelfHealingMetrics(prefix="test")

        assert metrics._initialized is True
        assert metrics.prefix == "test"
        assert mock_meter.create_counter.call_count > 0
        assert mock_meter.create_histogram.call_count > 0
        assert mock_meter.create_observable_gauge.call_count > 0


class TestOTELSelfHealingMetricsRecordingBehavior:
    """Behavior: recording methods are no-ops when uninitialized."""

    def _make_uninitialized_metrics(self):
        """Create an OTELSelfHealingMetrics that is not initialized."""
        with patch("selfhealing.observability.get_meter", return_value=None):
            return OTELSelfHealingMetrics()

    def test_record_dlq_item_created_noop_when_uninitialized(self):
        """Uninitialized metrics silently skip recording."""
        metrics = self._make_uninitialized_metrics()
        metrics.record_dlq_item_created("orders", "timeout")

    def test_record_retry_attempt_noop_when_uninitialized(self):
        """Uninitialized metrics silently skip retry recording."""
        metrics = self._make_uninitialized_metrics()
        metrics.record_retry_attempt("payments", 3, "success")

    def test_set_circuit_state_noop_when_uninitialized(self):
        """Uninitialized metrics silently skip circuit state."""
        metrics = self._make_uninitialized_metrics()
        metrics.set_circuit_state("payment_service", "open")

    @patch("selfhealing.observability.get_meter")
    def test_record_dlq_item_created_calls_counters(self, mock_get_meter):
        """Initialized metrics call both dlq_items_total and dlq_created_total."""
        # Use side_effect to return unique mocks for each create_counter call
        counters = {}

        def make_counter(name, **kw):
            mock = MagicMock(name=f"counter_{name}")
            counters[name] = mock
            return mock

        mock_meter = MagicMock()
        mock_meter.create_counter.side_effect = make_counter
        mock_get_meter.return_value = mock_meter
        metrics = OTELSelfHealingMetrics()

        metrics.record_dlq_item_created("orders", "timeout")

        metrics.dlq_items_total.add.assert_called_once_with(
            1, {"domain": "orders", "failure_type": "timeout"}
        )
        metrics.dlq_created_total.add.assert_called_once_with(1, {"domain": "orders"})

    @patch("selfhealing.observability.get_meter")
    def test_set_circuit_state_stores_numeric_value(self, mock_get_meter):
        """Circuit state string → numeric: closed=0, open=1, half_open=2."""
        mock_meter = MagicMock()
        mock_get_meter.return_value = mock_meter
        metrics = OTELSelfHealingMetrics()

        metrics.set_circuit_state("svc", "open", "cell-0")

        # Value 1 (open) stored with service_name and cell_id attributes
        key = (("cell_id", "cell-0"), ("service_name", "svc"))
        assert metrics._cb_state_store._values[key] == 1


class TestOTELSelfHealingMetricsTimerBehavior:
    """Behavior: context manager timer records duration."""

    @patch("selfhealing.observability.get_meter")
    def test_timer_records_replay_duration(self, mock_get_meter):
        """timer() context manager records duration to replay histogram."""
        histograms = {}

        def make_histogram(name, **_kw):
            mock = MagicMock(name=f"hist_{name}")
            histograms[name] = mock
            return mock

        mock_meter = MagicMock()
        mock_meter.create_histogram.side_effect = make_histogram
        mock_get_meter.return_value = mock_meter
        metrics = OTELSelfHealingMetrics()

        with metrics.timer("orders", "replay"):
            pass

        metrics.replay_duration_seconds.record.assert_called_once()
        call_args = metrics.replay_duration_seconds.record.call_args
        # record(duration, {"domain": "orders"}) — both positional
        duration = call_args[0][0]
        attrs = call_args[0][1]
        assert duration >= 0
        assert attrs == {"domain": "orders"}

    @patch("selfhealing.observability.get_meter")
    def test_track_http_request_records_on_error(self, mock_get_meter):
        """track_http_request records duration and error type on exception."""
        counters = {}
        histograms = {}

        def make_counter(name, **_kw):
            mock = MagicMock(name=f"counter_{name}")
            counters[name] = mock
            return mock

        def make_histogram(name, **_kw):
            mock = MagicMock(name=f"hist_{name}")
            histograms[name] = mock
            return mock

        mock_meter = MagicMock()
        mock_meter.create_counter.side_effect = make_counter
        mock_meter.create_histogram.side_effect = make_histogram
        mock_get_meter.return_value = mock_meter
        metrics = OTELSelfHealingMetrics()

        with pytest.raises(ValueError):
            with metrics.track_http_request("GET", "/api/test"):
                raise ValueError("test error")

        metrics.http_request_duration_seconds.record.assert_called_once()
        metrics.http_request_errors_total.add.assert_called_once()
        error_call = metrics.http_request_errors_total.add.call_args
        # add(1, {"method": ..., "endpoint": ..., "error_type": ...})
        error_attrs = error_call[0][1]
        assert error_attrs["error_type"] == "ValueError"
