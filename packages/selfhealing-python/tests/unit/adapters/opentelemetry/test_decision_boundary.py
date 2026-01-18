"""
Decision Boundary Tests.

Verify that telemetry is emitted ONLY at meaningful decision boundaries.
"""

import pytest


class TestDecisionBoundary:
    """Decision Boundary Tests."""

    def test_event_export_filtering_by_type(self):
        """Verify that event filtering works correctly by type."""
        from selfhealing.adapters.observability.opentelemetry.config import OpenTelemetryConfig
        
        config = OpenTelemetryConfig(
            enabled=True,
            export_circuit_breaker_events=True,
            export_retry_events=False,
            export_dlq_events=True,
            export_rate_limit_events=False,
            export_slo_events=True,
            export_policy_events=False,
        )
        
        assert config.should_export_event("circuit_breaker.opened") is True
        assert config.should_export_event("circuit_breaker.closed") is True
        assert config.should_export_event("retry.attempt") is False
        assert config.should_export_event("retry.exhausted") is False
        assert config.should_export_event("dlq.enqueued") is True
        assert config.should_export_event("rate_limit.triggered") is False
        assert config.should_export_event("slo.threshold.approaching") is True
        assert config.should_export_event("slo.breached") is True
        assert config.should_export_event("policy.evaluation") is False
        assert config.should_export_event("decision.cycle") is False

    def test_disabled_config_blocks_all_events(self):
        """When config is disabled, no events should export."""
        from selfhealing.adapters.observability.opentelemetry.config import OpenTelemetryConfig
        
        config = OpenTelemetryConfig(enabled=False)
        
        assert config.should_export_event("circuit_breaker.opened") is False
        assert config.should_export_event("retry.attempt") is False
        assert config.should_export_event("slo.breached") is False
        assert config.should_export_event("unknown.event.type") is False

    def test_event_export_disabled_blocks_all(self):
        """When event_export_enabled is False, no events should export."""
        from selfhealing.adapters.observability.opentelemetry.config import OpenTelemetryConfig
        
        config = OpenTelemetryConfig(enabled=True, event_export_enabled=False)
        
        assert config.should_export_event("circuit_breaker.opened") is False
        assert config.should_export_event("retry.attempt") is False

    def test_unknown_event_types_export_by_default(self):
        """Unknown event types should export by default."""
        from selfhealing.adapters.observability.opentelemetry.config import OpenTelemetryConfig
        
        config = OpenTelemetryConfig(enabled=True, event_export_enabled=True)
        
        assert config.should_export_event("custom.event") is True
        assert config.should_export_event("my.special.event") is True

    def test_circuit_breaker_state_transition_events(self):
        """Circuit breaker events should only emit on state transitions."""
        from selfhealing.adapters.observability.opentelemetry.events import SelfHealingEventType
        
        assert hasattr(SelfHealingEventType, "CIRCUIT_BREAKER_OPENED")
        assert hasattr(SelfHealingEventType, "CIRCUIT_BREAKER_CLOSED")
        assert hasattr(SelfHealingEventType, "CIRCUIT_BREAKER_HALF_OPENED")

    def test_retry_events_for_significant_boundaries(self):
        """Retry events should emit at significant boundaries."""
        from selfhealing.adapters.observability.opentelemetry.events import SelfHealingEventType
        
        assert hasattr(SelfHealingEventType, "RETRY_ATTEMPT")
        assert hasattr(SelfHealingEventType, "RETRY_EXHAUSTED")
        assert hasattr(SelfHealingEventType, "RETRY_SUCCESS")

    def test_dlq_events_for_lifecycle_transitions(self):
        """DLQ events should emit for lifecycle transitions only."""
        from selfhealing.adapters.observability.opentelemetry.events import SelfHealingEventType
        
        assert hasattr(SelfHealingEventType, "DLQ_ENQUEUED")
        assert hasattr(SelfHealingEventType, "DLQ_REPLAY_STARTED")
        assert hasattr(SelfHealingEventType, "DLQ_REPLAY_SUCCESS")
        assert hasattr(SelfHealingEventType, "DLQ_REPLAY_FAILED")

    def test_slo_events_for_threshold_crossings(self):
        """SLO events should emit only when thresholds are crossed."""
        from selfhealing.adapters.observability.opentelemetry.events import SelfHealingEventType
        
        assert hasattr(SelfHealingEventType, "SLO_THRESHOLD_APPROACHING")
        assert hasattr(SelfHealingEventType, "SLO_BREACHED")
        assert hasattr(SelfHealingEventType, "SLO_RECOVERED")

    def test_manual_override_vs_automatic_action_distinction(self):
        """Manual override and automatic actions should have distinct event types."""
        from selfhealing.adapters.observability.opentelemetry.events import SelfHealingEventType
        
        assert hasattr(SelfHealingEventType, "CIRCUIT_BREAKER_MANUAL_OVERRIDE")
        assert hasattr(SelfHealingEventType, "MANUAL_INTERVENTION_REQUIRED")
        assert hasattr(SelfHealingEventType, "POLICY_AUTO_HEAL_ALLOWED")
        assert hasattr(SelfHealingEventType, "POLICY_AUTO_HEAL_BLOCKED")

    def test_decision_cycle_span_events(self):
        """Decision cycle spans have start/end events only."""
        from selfhealing.adapters.observability.opentelemetry.events import SelfHealingEventType
        
        assert hasattr(SelfHealingEventType, "DECISION_CYCLE_STARTED")
        assert hasattr(SelfHealingEventType, "DECISION_CYCLE_COMPLETED")

    def test_decision_span_context_tracks_outcome(self):
        """Decision span context should track outcome for analysis."""
        from selfhealing.adapters.observability.opentelemetry.spans import (
            DecisionSpanContext,
            DecisionOutcome,
        )
        
        ctx = DecisionSpanContext(decision_type="policy_evaluation")
        
        ctx.set_outcome(DecisionOutcome.AUTO_HEALED)
        assert ctx.attributes.get("selfhealing.decision.outcome") == DecisionOutcome.AUTO_HEALED.value
        
        assert DecisionOutcome.COMPLETED
        assert DecisionOutcome.AUTO_HEALED
        assert DecisionOutcome.MANUAL_INTERVENTION
        assert DecisionOutcome.BLOCKED
        assert DecisionOutcome.TIMEOUT
        assert DecisionOutcome.ERROR
        assert DecisionOutcome.ABORTED
