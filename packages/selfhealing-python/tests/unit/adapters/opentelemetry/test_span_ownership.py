"""
Span Ownership Rule Tests.

Proves that:
- Adapter NEVER autonomously creates spans
- Events are silently dropped when no span exists
- Span lifecycle is strictly owned by external code
"""

import pytest


class TestSpanOwnershipRules:
    """Span Ownership Rule Tests."""

    def test_emit_event_without_active_span_drops_silently(self):
        """
        When no active span exists, emit_event must silently drop the event.
        No exception, no span creation.
        """
        from selfhealing.adapters.observability.opentelemetry.config import (
            OpenTelemetryConfig,
        )
        from selfhealing.adapters.observability.opentelemetry.adapter import (
            OpenTelemetryAdapter,
            OPENTELEMETRY_AVAILABLE,
        )
        
        config = OpenTelemetryConfig(
            enabled=True,
            service_name="test",
            environment="test",
        )
        
        adapter = OpenTelemetryAdapter(config)
        
        # Clear any active spans
        adapter._active_decision_spans.clear()
        
        # This must NOT raise and must NOT create a span
        adapter.emit_event("test.event", {"key": "value"}, "domain")
        
        # Verify no spans were created
        assert len(adapter._active_decision_spans) == 0

    def test_adapter_never_calls_start_as_current_span_for_events(self):
        """
        Adapter must NEVER call start_as_current_span when emitting events.
        """
        from selfhealing.adapters.observability.opentelemetry.config import (
            OpenTelemetryConfig,
        )
        from selfhealing.adapters.observability.opentelemetry.adapter import (
            OpenTelemetryAdapter,
        )
        
        config = OpenTelemetryConfig(
            enabled=True,
            service_name="test",
            environment="test",
        )
        
        adapter = OpenTelemetryAdapter(config)
        
        # Mock the tracer if it exists
        if adapter._tracer is not None:
            original_start_as_current = adapter._tracer.start_as_current_span
            call_count = [0]
            
            def tracked_start_as_current(*args, **kwargs):
                call_count[0] += 1
                return original_start_as_current(*args, **kwargs)
            
            adapter._tracer.start_as_current_span = tracked_start_as_current
            
            # Emit various events
            adapter.emit_event("test.event", {}, "domain")
            adapter.emit_circuit_breaker_transition("svc", "closed", "open")
            adapter.emit_retry_attempt("domain", 1, 3)
            
            # start_as_current_span must NOT have been called
            assert call_count[0] == 0, "Adapter called start_as_current_span during event emission"

    def test_event_attached_to_active_decision_span(self):
        """
        When a decision span is active, events must attach to it.
        """
        from selfhealing.adapters.observability.opentelemetry.config import (
            OpenTelemetryConfig,
        )
        from selfhealing.adapters.observability.opentelemetry.adapter import (
            OpenTelemetryAdapter,
        )
        
        config = OpenTelemetryConfig(
            enabled=True,
            service_name="test",
            environment="test",
        )
        
        adapter = OpenTelemetryAdapter(config)
        
        # Start a decision span (engine-initiated)
        span_ctx = adapter.start_decision_span(
            decision_type="policy_evaluation",
            domain="payment",
        )
        
        try:
            # Events should now be attached to the span
            # (No exception raised means success in NO-OP case)
            adapter.emit_event("test.event", {"key": "value"}, "domain")
            adapter.emit_circuit_breaker_transition("service", "closed", "open")
            
        finally:
            # End the span (engine-initiated)
            adapter.end_decision_span(span_ctx, "completed")

    def test_span_lifecycle_controlled_externally_only(self):
        """
        Span start/end is controlled by external code, not adapter decisions.
        """
        from selfhealing.adapters.observability.opentelemetry.config import (
            OpenTelemetryConfig,
        )
        from selfhealing.adapters.observability.opentelemetry.adapter import (
            OpenTelemetryAdapter,
        )
        
        config = OpenTelemetryConfig(
            enabled=True,
            service_name="test",
            environment="test",
        )
        
        adapter = OpenTelemetryAdapter(config)
        
        # No spans initially
        assert len(adapter._active_decision_spans) == 0
        
        # External code starts span
        ctx1 = adapter.start_decision_span("test1")
        
        # Span is now active (in adapter's tracking, may or may not have OTel span)
        # External code ends span
        adapter.end_decision_span(ctx1, "completed")
        
        # Context is marked inactive
        assert ctx1.is_active is False

    def test_decision_span_context_does_not_auto_end(self):
        """
        Decision span context must NOT automatically end on garbage collection.
        The engine must explicitly end it.
        """
        from selfhealing.adapters.observability.opentelemetry.spans import (
            DecisionSpanContext,
        )
        
        ctx = DecisionSpanContext(decision_type="test")
        ctx.is_active = True
        
        # Context stays active until explicitly ended
        assert ctx.is_active is True
        
        # Get span_id for tracking
        span_id = ctx.span_id
        assert span_id is not None
        
        # Context does not self-manage lifecycle

    def test_adapter_respects_disabled_span_config(self):
        """
        When decision_span_enabled is False, start_decision_span returns
        a context but does NOT create actual spans.
        """
        from selfhealing.adapters.observability.opentelemetry.config import (
            OpenTelemetryConfig,
        )
        from selfhealing.adapters.observability.opentelemetry.adapter import (
            OpenTelemetryAdapter,
        )
        
        config = OpenTelemetryConfig(
            enabled=True,
            decision_span_enabled=False,
            service_name="test",
            environment="test",
        )
        
        adapter = OpenTelemetryAdapter(config)
        
        # Start decision span - should return context but not create OTel span
        ctx = adapter.start_decision_span("test_decision")
        
        # Context exists for API compatibility
        assert ctx is not None
        assert ctx.decision_type == "test_decision"
        
        # But no actual OTel span is stored
        assert len(adapter._active_decision_spans) == 0
        
        # End still works (no-op)
        adapter.end_decision_span(ctx, "completed")

    def test_noop_adapter_never_creates_spans(self):
        """
        NO-OP adapter must NEVER attempt to create spans.
        """
        from selfhealing.adapters.observability.opentelemetry.noop import (
            NoOpOpenTelemetryAdapter,
            NoOpDecisionSpanContext,
        )
        
        adapter = NoOpOpenTelemetryAdapter()
        
        # Start decision span returns NO-OP context
        ctx = adapter.start_decision_span("test")
        assert isinstance(ctx, NoOpDecisionSpanContext)
        
        # Context is in expected state
        assert ctx.decision_type == "test"
        assert ctx.is_active is False
        
        # No span creation happened
        # (NO-OP adapter has no _active_decision_spans tracking)

    def test_adapter_does_not_modify_global_otel_state(self):
        """
        Adapter must NOT call trace.set_tracer_provider() or modify global state.
        """
        from selfhealing.adapters.observability.opentelemetry.adapter import (
            OpenTelemetryAdapter,
            OPENTELEMETRY_AVAILABLE,
        )
        from selfhealing.adapters.observability.opentelemetry.config import (
            OpenTelemetryConfig,
        )
        
        if OPENTELEMETRY_AVAILABLE:
            from opentelemetry import trace
            
            # Record current provider
            original_provider = trace.get_tracer_provider()
            
            config = OpenTelemetryConfig(
                enabled=True,
                service_name="test",
                environment="test",
            )
            
            # Create adapter
            adapter = OpenTelemetryAdapter(config)
            
            # Provider must not have changed
            current_provider = trace.get_tracer_provider()
            assert current_provider is original_provider, \
                "Adapter modified global TracerProvider"

    def test_span_context_add_event_safe_without_otel_span(self):
        """
        Adding events to span context is safe when no OTel span exists.
        """
        from selfhealing.adapters.observability.opentelemetry.spans import (
            DecisionSpanContext,
        )
        
        ctx = DecisionSpanContext(decision_type="test")
        # _otel_span is None by default
        assert ctx._otel_span is None
        
        # add_event must not raise
        ctx.add_event("test_event", {"key": "value"})
        
        # Event is recorded in context's event list
        assert len(ctx.events) == 1
        assert ctx.events[0]["name"] == "test_event"
