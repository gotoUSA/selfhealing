"""
Tests for Metric Decorators.

Reference: docs/self_healing/13_METRIC_COLLECTION_STRATEGY.md
"""

import pytest
import time
from unittest.mock import patch, MagicMock

from selfhealing.metrics.decorators import (
    track_dlq_creation,
    track_dlq_resolution,
    track_replay,
    track_execution_time,
    track_counter,
)


class TestTrackDLQCreation:
    """Tests for track_dlq_creation decorator."""

    @patch("selfhealing.metrics.decorators.DLQMetricEventHandler")
    def test_calls_event_handler_on_success(self, mock_handler):
        """Decorator should call on_item_created after function succeeds."""

        @track_dlq_creation(domain="payment")
        def create_dlq(failure_type: str, payload: dict):
            return {"id": 1, "failure_type": failure_type}

        result = create_dlq(failure_type="PG_TIMEOUT", payload={"order_id": "123"})

        assert result["id"] == 1
        mock_handler.on_item_created.assert_called_once_with("payment", "PG_TIMEOUT")

    @patch("selfhealing.metrics.decorators.DLQMetricEventHandler")
    def test_uses_unknown_for_missing_failure_type(self, mock_handler):
        """Should use 'unknown' when failure_type is not provided."""

        @track_dlq_creation(domain="payment")
        def create_dlq():
            return {"id": 1}

        result = create_dlq()

        mock_handler.on_item_created.assert_called_once_with("payment", "unknown")


class TestTrackDLQResolution:
    """Tests for track_dlq_resolution decorator."""

    @patch("selfhealing.metrics.decorators.DLQMetricEventHandler")
    def test_calls_event_handler_with_duration(self, mock_handler):
        """Decorator should measure duration and call on_item_resolved."""

        @track_dlq_resolution(domain="payment")
        def resolve_dlq(dlq_item, resolution_type: str = "auto_replay"):
            return dlq_item

        result = resolve_dlq({"id": 1}, resolution_type="manual")

        mock_handler.on_item_resolved.assert_called_once()
        call_args = mock_handler.on_item_resolved.call_args
        assert call_args.kwargs["domain"] == "payment"
        assert call_args.kwargs["resolution_type"] == "manual"
        assert call_args.kwargs["duration_seconds"] >= 0


class TestTrackReplay:
    """Tests for track_replay decorator."""

    @patch("selfhealing.metrics.decorators.ReplayEventHandler")
    def test_tracks_successful_replay(self, mock_handler):
        """Should track successful replay completion."""

        @track_replay(domain="payment")
        def replay_item(item):
            return True

        result = replay_item({"id": 1})

        assert result is True
        mock_handler.on_replay_started.assert_called_once()
        mock_handler.on_replay_completed.assert_called_once()

    @patch("selfhealing.metrics.decorators.ReplayEventHandler")
    def test_tracks_failed_replay(self, mock_handler):
        """Should track failed replay when exception is raised."""

        @track_replay(domain="payment")
        def replay_item(item):
            raise ValueError("Replay failed")

        with pytest.raises(ValueError):
            replay_item({"id": 1})

        mock_handler.on_replay_started.assert_called_once()
        # Completion should still be called with success=False
        mock_handler.on_replay_completed.assert_called_once()
        call_args = mock_handler.on_replay_completed.call_args
        assert call_args[0][1] is False  # success argument


class TestTrackExecutionTime:
    """Tests for track_execution_time decorator."""

    def test_measures_execution_time(self):
        """Should measure and log execution time."""

        @track_execution_time("test_metric", labels={"type": "test"})
        def slow_function():
            time.sleep(0.01)
            return "done"

        result = slow_function()

        assert result == "done"


class TestTrackCounter:
    """Tests for track_counter decorator."""

    def test_tracks_on_success(self):
        """Should track successful calls."""

        @track_counter("api_calls", labels={"endpoint": "/test"})
        def api_call():
            return {"status": "ok"}

        result = api_call()

        assert result["status"] == "ok"

    def test_tracks_on_failure(self):
        """Should track failed calls when configured."""

        @track_counter("api_calls", on_success=False, on_failure=True)
        def failing_api_call():
            raise RuntimeError("API Error")

        with pytest.raises(RuntimeError):
            failing_api_call()
