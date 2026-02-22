"""
Tests for HTTP Client OpenTelemetry Integration (Phase 3).

Tests for:
- SelfHealingHttpClient OTEL integration
- suppress_otel_instrumentation context manager
- Chaos context preservation with OTEL enabled
- requests instrumentation enable/disable
"""

import os
from unittest.mock import patch


class TestSuppressOtelInstrumentation:
    """Tests for suppress_otel_instrumentation context manager."""

    def test_suppress_instrumentation_when_otel_disabled(self):
        """Context manager works correctly when OTEL is disabled."""
        from selfhealing.services.http_client import suppress_otel_instrumentation

        with patch.dict(os.environ, {"OTEL_ENABLED": "false"}, clear=False):
            # Should not raise any errors
            with suppress_otel_instrumentation():
                pass

    def test_suppress_instrumentation_no_import_error(self):
        """No ImportError when OTEL modules are missing."""
        from selfhealing.services.http_client import suppress_otel_instrumentation

        # Should handle ImportError gracefully
        with suppress_otel_instrumentation():
            pass


class TestSelfHealingHttpClientOtelIntegration:
    """Tests for SelfHealingHttpClient OTEL integration."""

    def test_chaos_context_preserved_regardless_of_otel(self):
        """Chaos context headers are always propagated."""
        from selfhealing.services.http_client import (
            SYNTHETIC_HEADER,
            SelfHealingHttpClient,
        )

        client = SelfHealingHttpClient()
        client.set_experiment_id("exp-123")
        SelfHealingHttpClient.set_chaos_context(is_chaos=True, experiment_id="exp-123")

        try:
            headers = client._get_headers()
            assert SYNTHETIC_HEADER in headers
            assert headers[SYNTHETIC_HEADER] == "chaos-experiment"
        finally:
            SelfHealingHttpClient.clear_chaos_context()

    def test_client_with_suppress_internal_spans(self):
        """Client with suppress_internal_spans=True initializes correctly."""
        from selfhealing.services.http_client import SelfHealingHttpClient

        client = SelfHealingHttpClient(suppress_internal_spans=True)
        assert client._suppress_internal_spans is True

    def test_client_without_suppress_internal_spans(self):
        """Client without suppress_internal_spans defaults to False."""
        from selfhealing.services.http_client import SelfHealingHttpClient

        client = SelfHealingHttpClient()
        assert client._suppress_internal_spans is False

    def test_get_headers_without_chaos_context(self):
        """Headers do not include chaos headers when not in chaos mode."""
        from selfhealing.services.http_client import (
            SYNTHETIC_HEADER,
            SelfHealingHttpClient,
        )

        client = SelfHealingHttpClient(base_headers={"X-Custom": "value"})

        headers = client._get_headers()
        assert SYNTHETIC_HEADER not in headers
        assert headers.get("X-Custom") == "value"


class TestRequestsInstrumentationFunctions:
    """Tests for requests instrumentation enable/disable functions."""

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
            uninstrument_requests,
        )
        from selfhealing.settings.observability import reset_otel_settings

        uninstrument_requests()
        reset_opentelemetry()
        reset_otel_settings()

    def test_instrument_requests_returns_false_when_otel_disabled(self):
        """instrument_requests returns False when OTEL is not enabled."""
        from selfhealing.observability import instrument_requests, reset_opentelemetry

        reset_opentelemetry()

        with patch.dict(os.environ, {"OTEL_ENABLED": "false"}, clear=False):
            result = instrument_requests()
            assert result is False

    def test_is_requests_instrumented_default_false(self):
        """is_requests_instrumented returns False by default."""
        from selfhealing.observability import (
            is_requests_instrumented,
            reset_opentelemetry,
        )

        reset_opentelemetry()
        assert is_requests_instrumented() is False


class TestCeleryInstrumentationFunctions:
    """Tests for Celery instrumentation enable/disable functions."""

    def setup_method(self):
        """Reset OTEL state before each test."""
        from selfhealing.observability import reset_opentelemetry
        from selfhealing.settings.observability import reset_otel_settings

        reset_opentelemetry()
        reset_otel_settings()

    def teardown_method(self):
        """Clean up after each test."""
        from selfhealing.observability import reset_opentelemetry, uninstrument_celery
        from selfhealing.settings.observability import reset_otel_settings

        uninstrument_celery()
        reset_opentelemetry()
        reset_otel_settings()

    def test_instrument_celery_returns_false_when_otel_disabled(self):
        """instrument_celery returns False when OTEL is not enabled."""
        from selfhealing.observability import instrument_celery, reset_opentelemetry

        reset_opentelemetry()

        with patch.dict(os.environ, {"OTEL_ENABLED": "false"}, clear=False):
            result = instrument_celery()
            assert result is False

    def test_is_celery_instrumented_default_false(self):
        """is_celery_instrumented returns False by default."""
        from selfhealing.observability import (
            is_celery_instrumented,
            reset_opentelemetry,
        )

        reset_opentelemetry()
        assert is_celery_instrumented() is False
