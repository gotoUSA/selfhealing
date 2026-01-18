"""
Additional safety tests for edge cases.
"""

import pytest
import time


class TestAdditionalSafety:
    """Additional safety tests for edge cases."""

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
        
        ctx = DecisionSpanContext(decision_type="test")
        
        # Small delay
        time.sleep(0.01)
        
        # Duration should be calculable
        duration_ms = ctx.get_duration_ms()
        assert duration_ms >= 10  # At least 10ms
