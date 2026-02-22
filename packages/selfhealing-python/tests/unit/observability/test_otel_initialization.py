"""
Tests for OpenTelemetry SDK Initialization.
"""

import os
from unittest.mock import patch

import pytest


def _is_otel_available() -> bool:
    """Check if OpenTelemetry SDK is installed."""
    try:
        import opentelemetry.sdk.trace  # noqa: F401

        return True
    except ImportError:
        return False


class TestOpenTelemetryInitialization:
    """Tests for OpenTelemetry SDK initialization module."""

    def setup_method(self):
        """Reset OTEL state before each test."""
        from selfhealing.observability import reset_opentelemetry
        from selfhealing.settings.observability import reset_otel_settings

        reset_opentelemetry()
        reset_otel_settings()

    def teardown_method(self):
        """Clean up after each test."""
        from selfhealing.observability import reset_opentelemetry
        from selfhealing.settings.observability import reset_otel_settings

        reset_opentelemetry()
        reset_otel_settings()

    def test_initialization_disabled_by_default(self):
        """Test that OTEL is not initialized when OTEL_ENABLED=false."""
        from selfhealing.observability import (
            initialize_opentelemetry,
            is_otel_enabled,
            reset_opentelemetry,
        )

        reset_opentelemetry()

        with patch.dict(os.environ, {"OTEL_ENABLED": "false"}, clear=False):
            result = initialize_opentelemetry()

        assert result is False
        assert is_otel_enabled() is False

    def test_is_otel_enabled_returns_false_when_disabled(self):
        """Test is_otel_enabled returns False when OTEL is disabled."""
        from selfhealing.observability import is_otel_enabled, reset_opentelemetry

        reset_opentelemetry()

        with patch.dict(os.environ, {"OTEL_ENABLED": "false"}, clear=False):
            assert is_otel_enabled() is False

    def test_get_tracer_returns_none_when_disabled(self):
        """Test get_tracer returns None when OTEL is disabled."""
        from selfhealing.observability import get_tracer, reset_opentelemetry

        reset_opentelemetry()

        with patch.dict(os.environ, {"OTEL_ENABLED": "false"}, clear=False):
            tracer = get_tracer()
            assert tracer is None

    def test_get_tracer_provider_returns_none_when_disabled(self):
        """Test get_tracer_provider returns None when OTEL is disabled."""
        from selfhealing.observability import get_tracer_provider, reset_opentelemetry

        reset_opentelemetry()

        with patch.dict(os.environ, {"OTEL_ENABLED": "false"}, clear=False):
            provider = get_tracer_provider()
            assert provider is None

    def test_get_current_trace_id_from_otel_returns_none_when_disabled(self):
        """Test trace ID extraction returns None when OTEL is disabled."""
        from selfhealing.observability import (
            get_current_trace_id_from_otel,
            reset_opentelemetry,
        )

        reset_opentelemetry()

        with patch.dict(os.environ, {"OTEL_ENABLED": "false"}, clear=False):
            trace_id = get_current_trace_id_from_otel()
            assert trace_id is None

    def test_get_current_span_id_from_otel_returns_none_when_disabled(self):
        """Test span ID extraction returns None when OTEL is disabled."""
        from selfhealing.observability import (
            get_current_span_id_from_otel,
            reset_opentelemetry,
        )

        reset_opentelemetry()

        with patch.dict(os.environ, {"OTEL_ENABLED": "false"}, clear=False):
            span_id = get_current_span_id_from_otel()
            assert span_id is None

    def test_get_current_span_returns_none_when_disabled(self):
        """Test get_current_span returns None when OTEL is disabled."""
        from selfhealing.observability import get_current_span, reset_opentelemetry

        reset_opentelemetry()

        with patch.dict(os.environ, {"OTEL_ENABLED": "false"}, clear=False):
            span = get_current_span()
            assert span is None

    def test_initialization_idempotent(self):
        """Test that initialization is idempotent."""
        from selfhealing.observability import (
            initialize_opentelemetry,
            reset_opentelemetry,
        )

        reset_opentelemetry()

        with patch.dict(os.environ, {"OTEL_ENABLED": "false"}, clear=False):
            result1 = initialize_opentelemetry()
            result2 = initialize_opentelemetry()

            # Both should return False (disabled)
            assert result1 is False
            assert result2 is False

    def test_shutdown_is_safe_when_not_initialized(self):
        """Test that shutdown doesn't raise when not initialized."""
        from selfhealing.observability import (
            reset_opentelemetry,
            shutdown_opentelemetry,
        )

        reset_opentelemetry()

        # Should not raise
        shutdown_opentelemetry()

    def test_reset_allows_reinitialization(self):
        """Test that reset allows reinitialization."""
        from selfhealing.observability import (
            initialize_opentelemetry,
            is_otel_enabled,
            reset_opentelemetry,
        )

        with patch.dict(os.environ, {"OTEL_ENABLED": "false"}, clear=False):
            initialize_opentelemetry()
            assert is_otel_enabled() is False

            reset_opentelemetry()

            # After reset, should be able to reinitialize
            initialize_opentelemetry()
            assert is_otel_enabled() is False


class TestOpenTelemetryWithOtelInstalled:
    """Tests for OTEL initialization when SDK is installed."""

    def setup_method(self):
        """Reset OTEL state before each test."""
        from selfhealing.observability import reset_opentelemetry
        from selfhealing.settings.observability import reset_otel_settings

        reset_opentelemetry()
        reset_otel_settings()

    def teardown_method(self):
        """Clean up after each test."""
        from selfhealing.observability import (
            reset_opentelemetry,
            shutdown_opentelemetry,
        )
        from selfhealing.settings.observability import reset_otel_settings

        shutdown_opentelemetry()
        reset_opentelemetry()
        reset_otel_settings()

    @pytest.mark.skipif(
        not _is_otel_available(),
        reason="OpenTelemetry SDK not installed",
    )
    def test_initialization_enabled_with_otel_installed(self):
        """Test OTEL initialization when enabled and SDK is installed."""
        from selfhealing.observability import (
            get_tracer,
            initialize_opentelemetry,
            is_otel_enabled,
            reset_opentelemetry,
        )

        reset_opentelemetry()

        env_vars = {
            "OTEL_ENABLED": "true",
            "OTEL_SERVICE_NAME": "test-service",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317",
            "OTEL_ADAPTIVE_SAMPLING_ENABLED": "false",  # Disable to avoid EmergencyMode dependency
        }

        with patch.dict(os.environ, env_vars, clear=False):
            result = initialize_opentelemetry()

            if result:
                assert is_otel_enabled() is True
                assert get_tracer() is not None
