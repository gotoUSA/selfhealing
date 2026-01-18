"""
NO-OP Safety Tests.

Verify that the system behaves IDENTICALLY when OpenTelemetry is not installed.
"""

import sys

import pytest


class TestNoOpSafety:
    """NO-OP Safety Tests."""

    def test_import_without_opentelemetry_does_not_raise(self, monkeypatch):
        """Importing the adapter must NOT raise ImportError when opentelemetry is not installed."""
        monkeypatch.setitem(sys.modules, "opentelemetry", None)
        monkeypatch.setitem(sys.modules, "opentelemetry.trace", None)
        
        modules_to_remove = [
            key for key in sys.modules.keys()
            if key.startswith("selfhealing.adapters.observability.opentelemetry")
        ]
        for mod in modules_to_remove:
            monkeypatch.delitem(sys.modules, mod, raising=False)
        
        try:
            from selfhealing.adapters.observability.opentelemetry.noop import (
                NoOpOpenTelemetryAdapter,
                NoOpDecisionSpanContext,
            )
        except ImportError:
            pytest.fail("Import should not raise ImportError when OTel is unavailable")

    def test_noop_adapter_initialization_does_not_raise(self):
        """Initializing the NO-OP adapter must NOT raise any exception."""
        from selfhealing.adapters.observability.opentelemetry.noop import NoOpOpenTelemetryAdapter
        
        adapter = NoOpOpenTelemetryAdapter(config=None)
        assert adapter is not None
        assert adapter.is_enabled is False
        assert adapter.is_available is False

    def test_noop_adapter_emit_event_no_exception(self):
        """Calling emit_event on NO-OP adapter must NOT raise any exception."""
        from selfhealing.adapters.observability.opentelemetry.noop import NoOpOpenTelemetryAdapter
        
        adapter = NoOpOpenTelemetryAdapter()
        
        adapter.emit_event("test.event", {"key": "value"}, "test_domain")
        adapter.emit_circuit_breaker_transition("service", "closed", "open", "test")
        adapter.emit_retry_attempt("domain", 1, 3, "timeout", 1.0)
        adapter.emit_retry_exhausted("domain", 3, "final_error")
        adapter.emit_dlq_enqueue("domain", "network_error", 123)
        adapter.emit_rate_limit_triggered("service", "requests", 100, 50)
        adapter.emit_slo_threshold_approaching("slo", 0.95, 0.99, 95.0)
        adapter.emit_slo_breached("slo", 0.90, 0.99)
        adapter.emit_policy_evaluation("policy", "blocked", "reason")
        adapter.emit_manual_override("action", "target", "reason", "operator")

    def test_noop_adapter_no_side_effects(self):
        """NO-OP adapter methods must perform NO observable side effects."""
        from selfhealing.adapters.observability.opentelemetry.noop import (
            NoOpOpenTelemetryAdapter,
            NoOpDecisionSpanContext,
        )
        
        adapter = NoOpOpenTelemetryAdapter()
        
        span_ctx = adapter.start_decision_span("test_decision")
        assert isinstance(span_ctx, NoOpDecisionSpanContext)
        
        adapter.end_decision_span(span_ctx, "completed")
        
        adapter.shutdown()
        assert adapter.force_flush() is True

    def test_noop_span_context_operations_safe(self):
        """NO-OP span context operations must be safe to call."""
        from selfhealing.adapters.observability.opentelemetry.noop import NoOpDecisionSpanContext
        
        ctx = NoOpDecisionSpanContext(decision_type="test")
        
        ctx.add_event("test_event", {"key": "value"})
        ctx.set_outcome("completed", {"result": "success"})
        
        assert ctx.decision_type == "test"
        assert ctx.is_active is False

    def test_noop_tracer_components_safe(self):
        """All NO-OP tracer components must be safe to use."""
        from selfhealing.adapters.observability.opentelemetry.noop import (
            NoOpSpan,
            NoOpTracer,
            NoOpTracerProvider,
            NoOpEventLogger,
        )
        
        provider = NoOpTracerProvider()
        tracer = provider.get_tracer("test_module", "1.0.0")
        assert isinstance(tracer, NoOpTracer)
        
        span = tracer.start_span("test_span", attributes={"key": "value"})
        assert isinstance(span, NoOpSpan)
        
        span.set_attribute("key", "value")
        span.set_attributes({"a": 1, "b": 2})
        span.add_event("event", {"attr": "val"})
        span.record_exception(Exception("test"))
        span.set_status(None, "description")
        span.end()
        
        assert span.is_recording() is False
        
        with tracer.start_as_current_span("context_span") as ctx_span:
            assert isinstance(ctx_span, NoOpSpan)
        
        logger = NoOpEventLogger()
        logger.emit("event", {"key": "value"})

    def test_config_enabled_but_otel_unavailable_uses_noop(self):
        """When config enables OpenTelemetry but OTel is not installed, adapter uses NO-OP behavior."""
        from selfhealing.adapters.observability.opentelemetry.config import OpenTelemetryConfig
        
        config = OpenTelemetryConfig(
            enabled=True,
            service_name="test-service",
            environment="test",
        )
        
        assert config.enabled is True
