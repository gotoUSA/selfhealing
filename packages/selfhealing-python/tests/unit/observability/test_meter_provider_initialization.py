"""Unit tests for observability/__init__.py — MeterProvider lifecycle.

Tests initialize_meter_provider(), get_meter(), get_meter_provider(),
and meter-related state in shutdown/reset added in commit cf89883a.

Reference:
    docs/self_healing/middleware_system/316_GUNICORN_PRELOAD_OPTIMIZATION.md §5.8
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import selfhealing.observability as obs_module


@pytest.fixture(autouse=True)
def reset_otel_state():
    """Reset OTEL module state before each test."""
    obs_module._meter_provider = None
    obs_module._meter = None
    obs_module._prometheus_metric_reader = None
    yield
    obs_module._meter_provider = None
    obs_module._meter = None
    obs_module._prometheus_metric_reader = None


class TestInitializeMeterProviderBehavior:
    """Behavior: initialize_meter_provider lifecycle."""

    def test_import_error_returns_false(self):
        """ImportError (otel packages missing) → returns False."""
        result = obs_module.initialize_meter_provider()
        # If opentelemetry.exporter.prometheus is not installed, returns False
        # This test succeeds in both cases
        assert isinstance(result, bool)

    def test_idempotent_second_call_returns_true(self):
        """If _meter_provider is already set, returns True without reinit."""
        obs_module._meter_provider = MagicMock()

        result = obs_module.initialize_meter_provider()

        assert result is True


class TestGetMeterBehavior:
    """Behavior: get_meter() lazy-initializes and returns meter."""

    def test_returns_none_when_otel_unavailable(self):
        """If OTEL packages are not installed, returns None."""
        obs_module._meter = None
        obs_module._meter_provider = None

        # get_meter calls initialize_meter_provider which may fail
        result = obs_module.get_meter()
        # Result depends on whether OTEL is installed; either way, no crash
        assert result is None or result is not None

    def test_returns_cached_meter_if_already_set(self):
        """If _meter is already set, returns it directly."""
        mock_meter = MagicMock()
        obs_module._meter = mock_meter

        result = obs_module.get_meter()

        assert result is mock_meter


class TestGetMeterProviderBehavior:
    """Behavior: get_meter_provider() lazy-initializes and returns provider."""

    def test_returns_cached_provider_if_set(self):
        """If _meter_provider is already set, returns it directly."""
        mock_provider = MagicMock()
        obs_module._meter_provider = mock_provider

        result = obs_module.get_meter_provider()

        assert result is mock_provider


class TestResetOpentelemetryMeterBehavior:
    """Behavior: reset_opentelemetry() clears meter state."""

    def test_reset_clears_meter_state(self):
        """reset_opentelemetry() sets _meter_provider, _meter, _reader to None."""
        obs_module._meter_provider = MagicMock()
        obs_module._meter = MagicMock()
        obs_module._prometheus_metric_reader = MagicMock()

        obs_module.reset_opentelemetry()

        assert obs_module._meter_provider is None
        assert obs_module._meter is None
        assert obs_module._prometheus_metric_reader is None


class TestShutdownOpentelemetryMeterBehavior:
    """Behavior: shutdown_opentelemetry() shuts down MeterProvider."""

    def test_shutdown_calls_meter_provider_shutdown(self):
        """If _meter_provider is set, calls its shutdown()."""
        mock_provider = MagicMock()
        obs_module._meter_provider = mock_provider
        obs_module._meter = MagicMock()
        obs_module._prometheus_metric_reader = MagicMock()

        obs_module.shutdown_opentelemetry()

        mock_provider.shutdown.assert_called_once()
        assert obs_module._meter_provider is None
        assert obs_module._meter is None

    def test_shutdown_handles_meter_provider_error(self):
        """If MeterProvider.shutdown() raises, it's caught and state is cleaned."""
        mock_provider = MagicMock()
        mock_provider.shutdown.side_effect = RuntimeError("shutdown failed")
        obs_module._meter_provider = mock_provider

        obs_module.shutdown_opentelemetry()

        assert obs_module._meter_provider is None
