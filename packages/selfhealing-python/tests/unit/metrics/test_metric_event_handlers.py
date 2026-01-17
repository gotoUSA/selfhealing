"""
Tests for Metric Event Handlers.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock

from selfhealing.metrics.event_handlers import (
    DLQMetricEventHandler,
    CircuitBreakerEventHandler,
    ReplayEventHandler,
)


class TestDLQMetricEventHandler:
    """Tests for DLQMetricEventHandler."""

    @patch("selfhealing.metrics.event_handlers._get_metrics")
    def test_on_item_created_calls_metrics(self, mock_get_metrics):
        """on_item_created should call the appropriate metrics methods."""
        mock_metrics = MagicMock()
        mock_get_metrics.return_value = mock_metrics

        DLQMetricEventHandler.on_item_created("payment", "PG_TIMEOUT")

        mock_metrics.record_dlq_item_created.assert_called_once_with("payment", "PG_TIMEOUT")

    @patch("selfhealing.metrics.event_handlers._get_metrics")
    def test_on_item_created_handles_missing_metrics(self, mock_get_metrics):
        """on_item_created should handle gracefully when metrics are not available."""
        mock_get_metrics.return_value = None

        # Should not raise
        DLQMetricEventHandler.on_item_created("payment", "PG_TIMEOUT")

    @patch("selfhealing.metrics.event_handlers._get_safe_pending_gauge")
    @patch("selfhealing.metrics.event_handlers._get_metrics")
    def test_on_item_resolved_calls_metrics(self, mock_get_metrics, mock_get_safe_gauge):
        """on_item_resolved should update metrics correctly using SafeGauge."""
        mock_metrics = MagicMock()
        mock_metrics.recovery_time_seconds = MagicMock()
        mock_metrics.retry_outcomes_total = MagicMock()
        mock_get_metrics.return_value = mock_metrics

        mock_safe_gauge = MagicMock()
        mock_get_safe_gauge.return_value = mock_safe_gauge

        DLQMetricEventHandler.on_item_resolved(
            domain="payment",
            resolution_type="auto_replay",
            duration_seconds=30.5,
        )

        # SafeGauge를 통해 dec() 호출됨
        mock_safe_gauge.labels.assert_called_with(domain="payment")
        mock_safe_gauge.labels.return_value.dec.assert_called_once()
        mock_metrics.recovery_time_seconds.labels.assert_called()

    @patch("selfhealing.metrics.event_handlers._get_metrics")
    def test_on_item_failed_increments_failure_counter(self, mock_get_metrics):
        """on_item_failed should increment failure counter."""
        mock_metrics = MagicMock()
        mock_metrics.retry_outcomes_total = MagicMock()
        mock_metrics.retry_attempts_histogram = MagicMock()
        mock_get_metrics.return_value = mock_metrics

        DLQMetricEventHandler.on_item_failed("payment", "PG_TIMEOUT", attempt_count=3)

        mock_metrics.retry_outcomes_total.labels.assert_called()

    @patch("selfhealing.metrics.event_handlers._get_metrics")
    def test_on_sla_breach_increments_counter(self, mock_get_metrics):
        """on_sla_breach should increment SLA breach counter."""
        mock_metrics = MagicMock()
        mock_metrics.sla_breach_total = MagicMock()
        mock_get_metrics.return_value = mock_metrics

        DLQMetricEventHandler.on_sla_breach("payment")

        mock_metrics.sla_breach_total.labels.assert_called_once_with(domain="payment")


class TestCircuitBreakerEventHandler:
    """Tests for CircuitBreakerEventHandler."""

    def test_state_values_mapping(self):
        """STATE_VALUES should map states to numeric values."""
        assert CircuitBreakerEventHandler.STATE_VALUES["closed"] == 0
        assert CircuitBreakerEventHandler.STATE_VALUES["open"] == 1
        assert CircuitBreakerEventHandler.STATE_VALUES["half_open"] == 2

    @patch("selfhealing.metrics.event_handlers._get_metrics")
    def test_on_state_changed_updates_gauge(self, mock_get_metrics):
        """on_state_changed should update circuit breaker gauge."""
        mock_metrics = MagicMock()
        mock_metrics.circuit_breaker_state = MagicMock()
        mock_metrics.circuit_breaker_transitions = MagicMock()
        mock_metrics.circuit_breaker_trips = MagicMock()
        mock_get_metrics.return_value = mock_metrics

        CircuitBreakerEventHandler.on_state_changed(
            service="payment_api",
            from_state="closed",
            to_state="open",
        )

        mock_metrics.circuit_breaker_state.labels.assert_called()
        mock_metrics.circuit_breaker_trips.labels.assert_called()

    @patch("selfhealing.metrics.event_handlers._get_metrics")
    def test_on_failure_increments_counter(self, mock_get_metrics):
        """on_failure should increment failures counter."""
        mock_metrics = MagicMock()
        mock_metrics.circuit_breaker_failures = MagicMock()
        mock_get_metrics.return_value = mock_metrics

        CircuitBreakerEventHandler.on_failure("payment_api")

        mock_metrics.circuit_breaker_failures.labels.assert_called_once_with(
            service_name="payment_api"
        )


class TestReplayEventHandler:
    """Tests for ReplayEventHandler."""

    @patch("selfhealing.metrics.event_handlers._get_metrics")
    def test_on_replay_started_increments_counter(self, mock_get_metrics):
        """on_replay_started should increment replay attempts counter."""
        mock_metrics = MagicMock()
        mock_metrics.replay_attempts_total = MagicMock()
        mock_get_metrics.return_value = mock_metrics

        ReplayEventHandler.on_replay_started("payment", "auto")

        mock_metrics.replay_attempts_total.labels.assert_called_once_with(
            domain="payment",
            replay_type="auto",
        )

    @patch("selfhealing.metrics.event_handlers._get_metrics")
    def test_on_replay_completed_records_outcome(self, mock_get_metrics):
        """on_replay_completed should record outcome and duration."""
        mock_metrics = MagicMock()
        mock_metrics.replay_outcomes_total = MagicMock()
        mock_metrics.replay_duration_seconds = MagicMock()
        mock_get_metrics.return_value = mock_metrics

        ReplayEventHandler.on_replay_completed("payment", True, 2.5)

        mock_metrics.replay_outcomes_total.labels.assert_called()
        mock_metrics.replay_duration_seconds.labels.assert_called()
