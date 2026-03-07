"""
Tests for Circuit Breaker and Audit OTEL Integration (Phase 5).

Tests for:
- TriggeringRequestInfo OTEL auto-fill
- ExternalTraceContext.from_current_otel_context()
- AuditLogger trace_id_full field
- CircuitBreakerTracingManager OTEL span creation
"""

import os
from unittest.mock import patch


class TestTriggeringRequestInfoOtelAutoFill:
    """Tests for TriggeringRequestInfo OTEL auto-fill."""

    def setup_method(self):
        """Reset OTEL state before each test."""
        from selfhealing.observability import reset_opentelemetry
        from selfhealing.settings.otel import reset_otel_settings

        reset_opentelemetry()
        reset_otel_settings()

    def teardown_method(self):
        """Clean up after each test."""
        from selfhealing.observability import reset_opentelemetry
        from selfhealing.settings.otel import reset_otel_settings

        reset_opentelemetry()
        reset_otel_settings()

    def test_triggering_request_info_basic_creation(self):
        """TriggeringRequestInfo can be created with basic fields."""
        from selfhealing.services.circuit_breaker.tracing import TriggeringRequestInfo

        with patch.dict(os.environ, {"OTEL_ENABLED": "false"}, clear=False):
            info = TriggeringRequestInfo(
                trace_id="req-abc12345",
                endpoint="/api/test",
                method="POST",
            )

            assert info.trace_id == "req-abc12345"
            assert info.endpoint == "/api/test"
            assert info.method == "POST"

    def test_triggering_request_info_to_dict(self):
        """TriggeringRequestInfo.to_dict() includes all fields."""
        from selfhealing.services.circuit_breaker.tracing import TriggeringRequestInfo

        info = TriggeringRequestInfo(
            trace_id="req-test123",
            trace_id_full="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
            span_id="1234567890abcdef",
            endpoint="/api/v1/test",
        )

        result = info.to_dict()

        assert result["trace_id"] == "req-test123"
        assert result["trace_id_full"] == "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
        assert result["span_id"] == "1234567890abcdef"
        assert result["endpoint"] == "/api/v1/test"

    def test_triggering_request_info_from_dict(self):
        """TriggeringRequestInfo.from_dict() restores all fields."""
        from selfhealing.services.circuit_breaker.tracing import TriggeringRequestInfo

        data = {
            "trace_id": "req-xyz789",
            "trace_id_full": "deadbeefdeadbeefdeadbeefdeadbeef",
            "span_id": "cafebabecafebabe",
            "endpoint": "/api/test",
            "method": "GET",
        }

        info = TriggeringRequestInfo.from_dict(data)

        assert info.trace_id == "req-xyz789"
        assert info.trace_id_full == "deadbeefdeadbeefdeadbeefdeadbeef"
        assert info.span_id == "cafebabecafebabe"

    def test_from_current_otel_context_when_disabled(self):
        """from_current_otel_context returns basic info when OTEL disabled."""
        from selfhealing.services.circuit_breaker.tracing import TriggeringRequestInfo

        with patch.dict(os.environ, {"OTEL_ENABLED": "false"}, clear=False):
            info = TriggeringRequestInfo.from_current_otel_context(
                endpoint="/test",
                method="POST",
            )

            assert info.trace_id != ""
            assert info.endpoint == "/test"
            assert info.method == "POST"


class TestExternalTraceContextOtelIntegration:
    """Tests for ExternalTraceContext OTEL integration."""

    def setup_method(self):
        """Reset OTEL state before each test."""
        from selfhealing.observability import reset_opentelemetry
        from selfhealing.settings.otel import reset_otel_settings

        reset_opentelemetry()
        reset_otel_settings()

    def teardown_method(self):
        """Clean up after each test."""
        from selfhealing.observability import reset_opentelemetry
        from selfhealing.settings.otel import reset_otel_settings

        reset_opentelemetry()
        reset_otel_settings()

    def test_from_headers_extracts_traceparent(self):
        """from_headers correctly extracts W3C traceparent."""
        from selfhealing.audit.cascade_event import ExternalTraceContext

        headers = {
            "traceparent": "00-a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6-1234567890abcdef-01",
            "x-request-id": "req-123",
            "x-correlation-id": "corr-456",
        }

        ctx = ExternalTraceContext.from_headers(headers)

        assert ctx.trace_id == "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
        assert ctx.span_id == "1234567890abcdef"
        assert ctx.trace_flags == "01"
        assert ctx.trace_id_short == "req-a1b2c3d4"
        assert ctx.request_id == "req-123"
        assert ctx.correlation_id == "corr-456"

    def test_from_headers_handles_baggage(self):
        """from_headers correctly parses baggage header."""
        from selfhealing.audit.cascade_event import ExternalTraceContext

        headers = {
            "baggage": "key1=value1, key2=value2",
        }

        ctx = ExternalTraceContext.from_headers(headers)

        assert ctx.baggage.get("key1") == "value1"
        assert ctx.baggage.get("key2") == "value2"

    def test_from_current_otel_context_when_disabled(self):
        """from_current_otel_context returns empty when OTEL disabled."""
        from selfhealing.audit.cascade_event import ExternalTraceContext

        with patch.dict(os.environ, {"OTEL_ENABLED": "false"}, clear=False):
            ctx = ExternalTraceContext.from_current_otel_context()

            assert ctx.trace_id is None
            assert ctx.span_id is None

    def test_to_dict_includes_trace_id_short(self):
        """to_dict includes trace_id_short field."""
        from selfhealing.audit.cascade_event import ExternalTraceContext

        ctx = ExternalTraceContext(
            trace_id="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
            trace_id_short="req-a1b2c3d4",
        )

        result = ctx.to_dict()

        assert result["trace_id"] == "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
        assert result["trace_id_short"] == "req-a1b2c3d4"


class TestAuditLoggerTraceIdFull:
    """Tests for AuditLogger trace_id_full field."""

    def setup_method(self):
        """Reset state before each test."""
        from selfhealing.audit.trace import clear_trace_id
        from selfhealing.observability import reset_opentelemetry
        from selfhealing.settings.otel import reset_otel_settings

        reset_opentelemetry()
        reset_otel_settings()
        clear_trace_id()

    def teardown_method(self):
        """Clean up after each test."""
        from selfhealing.audit.trace import clear_trace_id
        from selfhealing.observability import reset_opentelemetry
        from selfhealing.settings.otel import reset_otel_settings

        reset_opentelemetry()
        reset_otel_settings()
        clear_trace_id()

    def test_build_entry_includes_trace_id(self):
        """_build_entry includes trace_id in output."""
        from selfhealing.audit.logger import AuditLogger
        from selfhealing.audit.trace import set_trace_id

        set_trace_id("req-test123")

        logger = AuditLogger(enable_console_log=False)
        entry = logger._build_entry(
            {
                "config_type": "TEST_CONFIG",
                "config_key": "test_key",
                "action": "update",
            }
        )

        assert entry.get("trace_id") == "req-test123"

    def test_build_entry_trace_id_full_none_when_otel_disabled(self):
        """trace_id_full is None when OTEL is disabled."""
        from selfhealing.audit.logger import AuditLogger
        from selfhealing.audit.trace import set_trace_id

        with patch.dict(os.environ, {"OTEL_ENABLED": "false"}, clear=False):
            set_trace_id("req-abc123")

            logger = AuditLogger(enable_console_log=False)
            entry = logger._build_entry(
                {
                    "config_type": "TEST_CONFIG",
                    "config_key": "key",
                    "action": "update",
                }
            )

            # trace_id_full should not be in entry when None (removed by cleanup)
            assert "trace_id_full" not in entry or entry.get("trace_id_full") is None


class TestCircuitBreakerTracingManagerOtel:
    """Tests for CircuitBreakerTracingManager OTEL integration."""

    def setup_method(self):
        """Reset state before each test."""
        from selfhealing.observability import reset_opentelemetry
        from selfhealing.services.circuit_breaker.tracing import (
            CircuitBreakerTracingManager,
        )
        from selfhealing.settings.otel import reset_otel_settings

        reset_opentelemetry()
        reset_otel_settings()
        CircuitBreakerTracingManager.reset_instance()

    def teardown_method(self):
        """Clean up after each test."""
        from selfhealing.observability import reset_opentelemetry
        from selfhealing.services.circuit_breaker.tracing import (
            CircuitBreakerTracingManager,
        )
        from selfhealing.settings.otel import reset_otel_settings

        reset_opentelemetry()
        reset_otel_settings()
        CircuitBreakerTracingManager.reset_instance()

    def test_create_otel_span_returns_none_when_disabled(self):
        """create_otel_span returns None when OTEL is disabled."""
        from selfhealing.services.circuit_breaker.tracing import (
            CircuitBreakerTracingManager,
        )

        with patch.dict(os.environ, {"OTEL_ENABLED": "false"}, clear=False):
            manager = CircuitBreakerTracingManager()
            span = manager.create_otel_span(
                service_id="test-service",
                previous_state="CLOSED",
                new_state="OPEN",
                trigger="AUTO_THRESHOLD",
            )

            assert span is None

    def test_create_otel_span_returns_none_when_create_spans_disabled(self):
        """create_otel_span returns None when config.create_spans=False."""
        from selfhealing.services.circuit_breaker.tracing import (
            CircuitBreakerTracingManager,
            TracingConfig,
        )

        # Reset and create fresh instance with disabled config
        CircuitBreakerTracingManager.reset_instance()
        CircuitBreakerTracingManager._instance = None

        # Create manager and manually set config
        manager = CircuitBreakerTracingManager()
        manager.config = TracingConfig(create_spans=False)

        span = manager.create_otel_span(
            service_id="test-service",
            previous_state="CLOSED",
            new_state="OPEN",
            trigger="AUTO_THRESHOLD",
        )

        assert span is None

    def test_record_failure_with_trace_stores_info(self):
        """record_failure_with_trace stores TriggeringRequestInfo."""
        from selfhealing.services.circuit_breaker.tracing import (
            CircuitBreakerTracingManager,
        )

        manager = CircuitBreakerTracingManager()

        info = manager.record_failure_with_trace(
            service_id="payment-api",
            error=Exception("Connection timeout"),
            endpoint="/api/payments",
            method="POST",
        )

        assert info.endpoint == "/api/payments"
        assert info.method == "POST"
        assert "Connection timeout" in info.error_message

        # Verify it's stored
        stored = manager.get_triggering_request("payment-api")
        assert stored is not None
        assert stored.endpoint == "/api/payments"

    def test_clear_triggering_request(self):
        """clear_triggering_request removes stored info."""
        from selfhealing.services.circuit_breaker.tracing import (
            CircuitBreakerTracingManager,
        )

        manager = CircuitBreakerTracingManager()

        manager.record_failure_with_trace(
            service_id="test-service",
            endpoint="/test",
        )

        assert manager.get_triggering_request("test-service") is not None

        manager.clear_triggering_request("test-service")

        assert manager.get_triggering_request("test-service") is None


class TestTracingConfigFromEnv:
    """Tests for TracingConfig.from_env()."""

    def test_from_env_defaults(self):
        """TracingConfig.from_env() uses correct defaults."""
        from selfhealing.services.circuit_breaker.tracing import TracingConfig

        with patch.dict(os.environ, {}, clear=True):
            config = TracingConfig.from_env()

            assert config.enabled is True
            assert config.record_triggering_request is True
            assert config.create_spans is True

    def test_from_env_disabled(self):
        """TracingConfig.from_env() respects disabled settings."""
        from selfhealing.services.circuit_breaker.tracing import TracingConfig

        with patch.dict(
            os.environ,
            {
                "CB_TRACING_ENABLED": "false",
                "CB_TRACING_CREATE_SPANS": "false",
            },
            clear=True,
        ):
            config = TracingConfig.from_env()

            assert config.enabled is False
            assert config.create_spans is False
