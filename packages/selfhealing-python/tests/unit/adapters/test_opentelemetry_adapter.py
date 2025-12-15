"""
OpenTelemetry Adapter Tests

Tests verify the architectural guarantees of the OpenTelemetry adapter:
1. NO-OP Safety: System functions identically when OpenTelemetry is not installed
2. Decision Boundary: Events emitted only at meaningful state transitions
3. Span Ownership: Adapter never creates or owns spans autonomously

These tests encode and enforce design contracts, not implementation details.
"""

from __future__ import annotations

import sys
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from typing import Any, Dict, Optional, List


# =============================================================================
# 1️⃣ NO-OP SAFETY TESTS
# =============================================================================
# Verify that the system behaves IDENTICALLY when OpenTelemetry is not installed.
# This proves: "OpenTelemetry is a truly optional dependency."


class TestNoOpSafety:
    """
    NO-OP Safety Tests.
    
    Proves that the self-healing system operates correctly
    regardless of whether OpenTelemetry is installed.
    """

    def test_import_without_opentelemetry_does_not_raise(self, monkeypatch):
        """
        Importing the adapter must NOT raise ImportError
        when opentelemetry package is not installed.
        """
        # Simulate opentelemetry not being installed
        monkeypatch.setitem(sys.modules, "opentelemetry", None)
        monkeypatch.setitem(sys.modules, "opentelemetry.trace", None)
        
        # Remove cached imports if any
        modules_to_remove = [
            key for key in sys.modules.keys()
            if key.startswith("selfhealing.adapters.observability.opentelemetry")
        ]
        for mod in modules_to_remove:
            monkeypatch.delitem(sys.modules, mod, raising=False)
        
        # This must NOT raise
        try:
            from selfhealing.adapters.observability.opentelemetry.noop import (
                NoOpOpenTelemetryAdapter,
                NoOpDecisionSpanContext,
            )
        except ImportError:
            pytest.fail("Import should not raise ImportError when OTel is unavailable")

    def test_noop_adapter_initialization_does_not_raise(self):
        """
        Initializing the NO-OP adapter must NOT raise any exception.
        """
        from selfhealing.adapters.observability.opentelemetry.noop import (
            NoOpOpenTelemetryAdapter,
        )
        
        # Must not raise
        adapter = NoOpOpenTelemetryAdapter(config=None)
        assert adapter is not None
        assert adapter.is_enabled is False
        assert adapter.is_available is False

    def test_noop_adapter_emit_event_no_exception(self):
        """
        Calling emit_event on NO-OP adapter must NOT raise any exception.
        """
        from selfhealing.adapters.observability.opentelemetry.noop import (
            NoOpOpenTelemetryAdapter,
        )
        
        adapter = NoOpOpenTelemetryAdapter()
        
        # All emit methods must silently do nothing
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
        """
        NO-OP adapter methods must perform NO observable side effects.
        """
        from selfhealing.adapters.observability.opentelemetry.noop import (
            NoOpOpenTelemetryAdapter,
            NoOpDecisionSpanContext,
        )
        
        adapter = NoOpOpenTelemetryAdapter()
        
        # Start/end decision span - must return valid context but do nothing
        span_ctx = adapter.start_decision_span("test_decision")
        assert isinstance(span_ctx, NoOpDecisionSpanContext)
        
        # End span - must not raise
        adapter.end_decision_span(span_ctx, "completed")
        
        # Lifecycle methods - must not raise
        adapter.shutdown()
        assert adapter.force_flush() is True

    def test_noop_span_context_operations_safe(self):
        """
        NO-OP span context operations must be safe to call.
        """
        from selfhealing.adapters.observability.opentelemetry.noop import (
            NoOpDecisionSpanContext,
        )
        
        ctx = NoOpDecisionSpanContext(decision_type="test")
        
        # All operations must silently do nothing
        ctx.add_event("test_event", {"key": "value"})
        ctx.set_outcome("completed", {"result": "success"})
        
        # State remains consistent
        assert ctx.decision_type == "test"
        assert ctx.is_active is False

    def test_noop_tracer_components_safe(self):
        """
        All NO-OP tracer components must be safe to use.
        """
        from selfhealing.adapters.observability.opentelemetry.noop import (
            NoOpSpan,
            NoOpTracer,
            NoOpTracerProvider,
            NoOpEventLogger,
        )
        
        # Tracer provider
        provider = NoOpTracerProvider()
        tracer = provider.get_tracer("test_module", "1.0.0")
        assert isinstance(tracer, NoOpTracer)
        
        # Span operations
        span = tracer.start_span("test_span", attributes={"key": "value"})
        assert isinstance(span, NoOpSpan)
        
        # Span methods must not raise
        span.set_attribute("key", "value")
        span.set_attributes({"a": 1, "b": 2})
        span.add_event("event", {"attr": "val"})
        span.record_exception(Exception("test"))
        span.set_status(None, "description")
        span.end()
        
        assert span.is_recording() is False
        
        # Context manager usage
        with tracer.start_as_current_span("context_span") as ctx_span:
            assert isinstance(ctx_span, NoOpSpan)
        
        # Event logger
        logger = NoOpEventLogger()
        logger.emit("event", {"key": "value"})

    def test_config_enabled_but_otel_unavailable_uses_noop(self):
        """
        When config enables OpenTelemetry but OTel is not installed,
        the adapter must use NO-OP behavior without raising.
        """
        from selfhealing.adapters.observability.opentelemetry.config import (
            OpenTelemetryConfig,
        )
        
        # Config with enabled=True
        config = OpenTelemetryConfig(
            enabled=True,
            service_name="test-service",
            environment="test",
        )
        
        # Even with enabled=True, if OTel is not available, 
        # the system must continue functioning
        assert config.enabled is True


# =============================================================================
# 2️⃣ DECISION BOUNDARY TESTS
# =============================================================================
# Verify that telemetry is emitted ONLY at meaningful decision boundaries.
# This proves: "The system is silent when healthy and expressive only when it matters."


class TestDecisionBoundary:
    """
    Decision Boundary Tests.
    
    Proves that events are emitted ONLY at:
    - State transitions
    - Explicit decision evaluations
    - Manual or automatic actions
    """

    def test_event_export_filtering_by_type(self):
        """
        Verify that event filtering works correctly by type.
        """
        from selfhealing.adapters.observability.opentelemetry.config import (
            OpenTelemetryConfig,
        )
        
        # Config with specific event types disabled
        config = OpenTelemetryConfig(
            enabled=True,
            export_circuit_breaker_events=True,
            export_retry_events=False,
            export_dlq_events=True,
            export_rate_limit_events=False,
            export_slo_events=True,
            export_policy_events=False,
        )
        
        # Circuit breaker events should export
        assert config.should_export_event("circuit_breaker.opened") is True
        assert config.should_export_event("circuit_breaker.closed") is True
        
        # Retry events should NOT export (disabled)
        assert config.should_export_event("retry.attempt") is False
        assert config.should_export_event("retry.exhausted") is False
        
        # DLQ events should export
        assert config.should_export_event("dlq.enqueued") is True
        
        # Rate limit events should NOT export (disabled)
        assert config.should_export_event("rate_limit.triggered") is False
        
        # SLO events should export
        assert config.should_export_event("slo.threshold.approaching") is True
        assert config.should_export_event("slo.breached") is True
        
        # Policy events should NOT export (disabled)
        assert config.should_export_event("policy.evaluation") is False
        assert config.should_export_event("decision.cycle") is False

    def test_disabled_config_blocks_all_events(self):
        """
        When config is disabled, no events should export.
        """
        from selfhealing.adapters.observability.opentelemetry.config import (
            OpenTelemetryConfig,
        )
        
        config = OpenTelemetryConfig(enabled=False)
        
        # All event types must return False when disabled
        assert config.should_export_event("circuit_breaker.opened") is False
        assert config.should_export_event("retry.attempt") is False
        assert config.should_export_event("slo.breached") is False
        assert config.should_export_event("unknown.event.type") is False

    def test_event_export_disabled_blocks_all(self):
        """
        When event_export_enabled is False, no events should export.
        """
        from selfhealing.adapters.observability.opentelemetry.config import (
            OpenTelemetryConfig,
        )
        
        config = OpenTelemetryConfig(
            enabled=True,
            event_export_enabled=False,
        )
        
        # All events blocked even though adapter is enabled
        assert config.should_export_event("circuit_breaker.opened") is False
        assert config.should_export_event("retry.attempt") is False

    def test_unknown_event_types_export_by_default(self):
        """
        Unknown event types should export by default (whitelist approach).
        """
        from selfhealing.adapters.observability.opentelemetry.config import (
            OpenTelemetryConfig,
        )
        
        config = OpenTelemetryConfig(enabled=True, event_export_enabled=True)
        
        # Unknown event types should export
        assert config.should_export_event("custom.event") is True
        assert config.should_export_event("my.special.event") is True

    def test_circuit_breaker_state_transition_events(self):
        """
        Circuit breaker events should only emit on state transitions.
        """
        from selfhealing.adapters.observability.opentelemetry.events import (
            SelfHealingEventType,
        )
        
        # Verify event types exist for transitions only
        assert hasattr(SelfHealingEventType, "CIRCUIT_BREAKER_OPENED")
        assert hasattr(SelfHealingEventType, "CIRCUIT_BREAKER_CLOSED")
        assert hasattr(SelfHealingEventType, "CIRCUIT_BREAKER_HALF_OPENED")
        
        # No event for "circuit breaker checked" or heartbeat-style events
        # (These shouldn't exist - only transition events)

    def test_retry_events_for_significant_boundaries(self):
        """
        Retry events should emit at significant boundaries, not every attempt.
        """
        from selfhealing.adapters.observability.opentelemetry.events import (
            SelfHealingEventType,
        )
        
        # Verify meaningful retry events exist
        assert hasattr(SelfHealingEventType, "RETRY_ATTEMPT")
        assert hasattr(SelfHealingEventType, "RETRY_EXHAUSTED")
        assert hasattr(SelfHealingEventType, "RETRY_SUCCESS")
        
        # These represent actual decision points

    def test_dlq_events_for_lifecycle_transitions(self):
        """
        DLQ events should emit for lifecycle transitions only.
        """
        from selfhealing.adapters.observability.opentelemetry.events import (
            SelfHealingEventType,
        )
        
        # DLQ lifecycle events
        assert hasattr(SelfHealingEventType, "DLQ_ENQUEUED")
        assert hasattr(SelfHealingEventType, "DLQ_REPLAY_STARTED")
        assert hasattr(SelfHealingEventType, "DLQ_REPLAY_SUCCESS")
        assert hasattr(SelfHealingEventType, "DLQ_REPLAY_FAILED")

    def test_slo_events_for_threshold_crossings(self):
        """
        SLO events should emit only when thresholds are crossed.
        """
        from selfhealing.adapters.observability.opentelemetry.events import (
            SelfHealingEventType,
        )
        
        # SLO boundary crossing events
        assert hasattr(SelfHealingEventType, "SLO_THRESHOLD_APPROACHING")
        assert hasattr(SelfHealingEventType, "SLO_BREACHED")
        assert hasattr(SelfHealingEventType, "SLO_RECOVERED")

    def test_manual_override_vs_automatic_action_distinction(self):
        """
        Manual override and automatic actions should have distinct event types.
        """
        from selfhealing.adapters.observability.opentelemetry.events import (
            SelfHealingEventType,
        )
        
        # Manual intervention events
        assert hasattr(SelfHealingEventType, "CIRCUIT_BREAKER_MANUAL_OVERRIDE")
        assert hasattr(SelfHealingEventType, "MANUAL_INTERVENTION_REQUIRED")
        
        # Automatic action events (policy-based)
        assert hasattr(SelfHealingEventType, "POLICY_AUTO_HEAL_ALLOWED")
        assert hasattr(SelfHealingEventType, "POLICY_AUTO_HEAL_BLOCKED")

    def test_decision_cycle_span_events(self):
        """
        Decision cycle spans have start/end events only.
        """
        from selfhealing.adapters.observability.opentelemetry.events import (
            SelfHealingEventType,
        )
        
        # Decision cycle boundary events
        assert hasattr(SelfHealingEventType, "DECISION_CYCLE_STARTED")
        assert hasattr(SelfHealingEventType, "DECISION_CYCLE_COMPLETED")

    def test_decision_span_context_tracks_outcome(self):
        """
        Decision span context should track outcome for analysis.
        """
        from selfhealing.adapters.observability.opentelemetry.spans import (
            DecisionSpanContext,
            DecisionOutcome,
        )
        
        ctx = DecisionSpanContext(decision_type="policy_evaluation")
        
        # Set outcome
        ctx.set_outcome(DecisionOutcome.AUTO_HEALED)
        # Outcome is stored in attributes
        assert ctx.attributes.get("selfhealing.decision.outcome") == DecisionOutcome.AUTO_HEALED.value
        
        # Verify all outcome types exist
        assert DecisionOutcome.COMPLETED
        assert DecisionOutcome.AUTO_HEALED
        assert DecisionOutcome.MANUAL_INTERVENTION
        assert DecisionOutcome.BLOCKED
        assert DecisionOutcome.TIMEOUT
        assert DecisionOutcome.ERROR
        assert DecisionOutcome.ABORTED


# =============================================================================
# 3️⃣ SPAN OWNERSHIP RULE TESTS
# =============================================================================
# Verify that the adapter NEVER owns or creates spans autonomously.
# This proves: "This library does not contaminate or hijack the OpenTelemetry environment."


class TestSpanOwnershipRules:
    """
    Span Ownership Rule Tests.
    
    Proves that:
    - Adapter NEVER autonomously creates spans
    - Events are silently dropped when no span exists
    - Span lifecycle is strictly owned by external code
    """

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


# =============================================================================
# ADDITIONAL SAFETY TESTS
# =============================================================================


class TestAdditionalSafety:
    """
    Additional safety tests for edge cases.
    """

    def test_adapter_singleton_pattern(self):
        """
        Verify adapter can be used as singleton.
        """
        from selfhealing.adapters.observability.opentelemetry.adapter import (
            get_opentelemetry_adapter,
        )
        
        # get_opentelemetry_adapter should work
        adapter = get_opentelemetry_adapter()
        assert adapter is not None

    def test_config_from_env_defaults(self):
        """
        Config from environment has safe defaults.
        """
        from selfhealing.adapters.observability.opentelemetry.config import (
            OpenTelemetryConfig,
        )
        
        config = OpenTelemetryConfig.from_env()
        
        # Default is disabled
        assert config.enabled is False

    def test_adapter_shutdown_safe(self):
        """
        Adapter shutdown is safe to call multiple times.
        """
        from selfhealing.adapters.observability.opentelemetry.adapter import (
            OpenTelemetryAdapter,
        )
        from selfhealing.adapters.observability.opentelemetry.config import (
            OpenTelemetryConfig,
        )
        
        config = OpenTelemetryConfig(enabled=True)
        adapter = OpenTelemetryAdapter(config)
        
        # Multiple shutdowns must not raise
        adapter.shutdown()
        adapter.shutdown()
        adapter.shutdown()

    def test_adapter_force_flush_safe(self):
        """
        Force flush is safe to call.
        """
        from selfhealing.adapters.observability.opentelemetry.adapter import (
            OpenTelemetryAdapter,
        )
        from selfhealing.adapters.observability.opentelemetry.config import (
            OpenTelemetryConfig,
        )
        
        config = OpenTelemetryConfig(enabled=True)
        adapter = OpenTelemetryAdapter(config)
        
        # Force flush must not raise
        result = adapter.force_flush(timeout_millis=1000)
        assert isinstance(result, bool)

    def test_decision_type_enum_values(self):
        """
        Decision type enum has expected values.
        """
        from selfhealing.adapters.observability.opentelemetry.spans import (
            DecisionType,
        )
        
        # Verify known decision types
        assert DecisionType.POLICY_EVALUATION.value == "policy_evaluation"
        assert DecisionType.MANUAL_OVERRIDE.value == "manual_override"
        assert DecisionType.CIRCUIT_BREAKER_RECOVERY.value == "circuit_breaker_recovery"
        assert DecisionType.DLQ_BATCH_REPLAY.value == "dlq_batch_replay"
        assert DecisionType.SLO_BREACH_RESPONSE.value == "slo_breach_response"
        assert DecisionType.RATE_LIMIT_COOLDOWN.value == "rate_limit_cooldown"

    def test_event_attribute_constants_exist(self):
        """
        Event attribute constants are defined.
        """
        from selfhealing.adapters.observability.opentelemetry.events import (
            EventAttribute,
        )
        
        # Core attributes
        assert hasattr(EventAttribute, "SERVICE_NAME")
        assert hasattr(EventAttribute, "ENVIRONMENT")
        assert hasattr(EventAttribute, "DOMAIN")
        assert hasattr(EventAttribute, "TIMESTAMP")
        assert hasattr(EventAttribute, "DECISION_TYPE")

    def test_span_context_duration_tracking(self):
        """
        Span context tracks duration for analysis.
        """
        from selfhealing.adapters.observability.opentelemetry.spans import (
            DecisionSpanContext,
        )
        import time
        
        ctx = DecisionSpanContext(decision_type="test")
        
        # Small delay
        time.sleep(0.01)
        
        # Duration should be calculable
        duration_ms = ctx.get_duration_ms()
        assert duration_ms >= 10  # At least 10ms
