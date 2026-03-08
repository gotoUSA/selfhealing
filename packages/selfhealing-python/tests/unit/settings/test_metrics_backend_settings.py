"""Unit tests for settings/metrics.py backend field and get_metrics() routing.

Tests the new MetricsSettings.backend field added in commit cf89883a
and the get_metrics() backend selection logic in prometheus.py.

Reference:
    docs/self_healing/middleware_system/316_GUNICORN_PRELOAD_OPTIMIZATION.md §5.8
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from selfhealing.settings.metrics import MetricsSettings


class TestMetricsSettingsBackendContract:
    """Contract: backend field defaults to 'prometheus', accepts 'otel'."""

    def test_backend_default_is_prometheus(self):
        """Default backend is 'prometheus' (legacy mode)."""
        settings = MetricsSettings()
        assert settings.backend == "prometheus"

    def test_backend_accepts_otel(self):
        """'otel' is a valid backend value."""
        settings = MetricsSettings(backend="otel")
        assert settings.backend == "otel"

    def test_backend_accepts_prometheus(self):
        """'prometheus' is explicitly valid."""
        settings = MetricsSettings(backend="prometheus")
        assert settings.backend == "prometheus"

    def test_backend_rejects_invalid_value(self):
        """Invalid backend value raises ValidationError (pattern constraint)."""
        with pytest.raises(ValidationError):
            MetricsSettings(backend="graphite")

    def test_backend_rejects_empty_string(self):
        """Empty string is rejected by pattern."""
        with pytest.raises(ValidationError):
            MetricsSettings(backend="")

    def test_backend_pattern_exact_match(self):
        """Pattern enforces exact match — 'otel_extra' is rejected."""
        with pytest.raises(ValidationError):
            MetricsSettings(backend="otel_extra")


class TestMetricsSettingsBackendEnvBehavior:
    """Behavior: backend can be set via SELFHEALING_METRICS_BACKEND env var."""

    def test_env_var_sets_backend(self):
        """SELFHEALING_METRICS_BACKEND=otel → backend='otel'."""
        with patch.dict("os.environ", {"SELFHEALING_METRICS_BACKEND": "otel"}):
            settings = MetricsSettings()
            assert settings.backend == "otel"


class TestGetMetricsRoutingBehavior:
    """Behavior: get_metrics() returns OTEL or Prometheus backend."""

    @patch("selfhealing.settings.metrics.get_metrics_settings")
    def test_returns_otel_backend_when_configured(self, mock_get_settings):
        """backend='otel' → OTELSelfHealingMetrics."""
        from selfhealing.metrics.otel_backend import OTELSelfHealingMetrics
        from selfhealing.metrics.prometheus import get_metrics, reset_metrics

        reset_metrics()
        mock_get_settings.return_value = MagicMock(backend="otel")

        with patch("selfhealing.observability.get_meter") as mock_get_meter:
            mock_meter = MagicMock()
            mock_get_meter.return_value = mock_meter
            result = get_metrics()

        assert isinstance(result, OTELSelfHealingMetrics)
        reset_metrics()


class TestGetMetricsSingletonBehavior:
    """Behavior: get_metrics() returns cached singleton."""

    @patch("selfhealing.settings.metrics.get_metrics_settings")
    def test_returns_same_instance_on_repeated_calls(self, mock_get_settings):
        """get_metrics() returns the same instance (singleton)."""
        from selfhealing.metrics.prometheus import get_metrics, reset_metrics

        reset_metrics()
        mock_get_settings.return_value = MagicMock(backend="otel")

        with patch("selfhealing.observability.get_meter") as mock_get_meter:
            mock_get_meter.return_value = MagicMock()
            first = get_metrics()
            second = get_metrics()

        assert first is second
        reset_metrics()

    @patch("selfhealing.settings.metrics.get_metrics_settings")
    def test_reset_clears_cached_instance(self, mock_get_settings):
        """reset_metrics() → next call creates new instance."""
        from selfhealing.metrics.prometheus import get_metrics, reset_metrics

        mock_get_settings.return_value = MagicMock(backend="otel")

        with patch("selfhealing.observability.get_meter") as mock_get_meter:
            mock_get_meter.return_value = MagicMock()
            reset_metrics()
            first = get_metrics()
            reset_metrics()
            second = get_metrics()

        assert first is not second
        reset_metrics()
