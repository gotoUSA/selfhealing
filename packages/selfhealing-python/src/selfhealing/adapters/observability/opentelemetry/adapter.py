"""
OpenTelemetry Adapter Implementation

Main adapter class that bridges self-healing signals to OpenTelemetry.

CRITICAL DESIGN RULES:
────────────────────────────────────────────────────────────────────────
1. This adapter MUST NOT own or configure the OpenTelemetry environment
2. This adapter MUST NOT alter global OpenTelemetry state
3. This adapter MUST NOT call trace.set_tracer_provider()
4. This adapter MUST NOT configure exporters, samplers, or resources
5. All OpenTelemetry configuration is owned by the application/platform
────────────────────────────────────────────────────────────────────────

SPAN OWNERSHIP RULES:
- The adapter NEVER autonomously creates spans
- The adapter NEVER decides when a span starts or ends
- Span lifecycle is owned exclusively by the self-healing engine
- If no active span exists, events are silently dropped

EVENT EMISSION RULES:
- If there is an active decision span: attach events to that span
- If there is NO active span: DO NOT create a span, silently drop
- Optional DEBUG log when events are dropped (for troubleshooting)

This adapter ONLY emits self-healing decision signals
into an EXISTING OpenTelemetry environment, if one exists.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, TYPE_CHECKING
from datetime import datetime
import threading

from .config import OpenTelemetryConfig
from .spans import DecisionSpanContext, DecisionOutcome
from .events import SelfHealingEventType, EventAttribute

logger = logging.getLogger(__name__)

# =============================================================================
# OpenTelemetry SDK Detection (Lazy Import)
# =============================================================================
#
# WHY LAZY IMPORT:
# - OpenTelemetry is an OPTIONAL dependency
# - If not installed, the adapter becomes a complete NO-OP
# - No ImportError may escape to the application
# - The system MUST start and function identically without OTel
#

OPENTELEMETRY_AVAILABLE = False

try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode, SpanKind

    OPENTELEMETRY_AVAILABLE = True
    logger.debug("OpenTelemetry API detected and available")

except ImportError:
    # OpenTelemetry not installed - adapter becomes NO-OP
    OPENTELEMETRY_AVAILABLE = False
    trace = None
    Status = None
    StatusCode = None
    SpanKind = None
    logger.debug("OpenTelemetry API not installed, adapter will use NO-OP mode")


# =============================================================================
# OpenTelemetry Adapter
# =============================================================================


class OpenTelemetryAdapter:
    """
    OpenTelemetry Adapter for Self-Healing System.

    Emits self-healing decision events into an EXISTING OpenTelemetry
    environment. This adapter does NOT configure OpenTelemetry.

    CRITICAL CONSTRAINTS:
    ─────────────────────────────────────────────────────────────────
    ❌ NEVER calls trace.set_tracer_provider()
    ❌ NEVER configures exporters, samplers, or resources
    ❌ NEVER creates spans autonomously
    ❌ NEVER emits events when no active span exists
    ─────────────────────────────────────────────────────────────────
    ✅ Attaches events to existing active spans
    ✅ Silently drops events when no span exists
    ✅ Uses application's existing TracerProvider
    ✅ Becomes complete NO-OP when disabled or OTel not installed
    ─────────────────────────────────────────────────────────────────

    Usage:
        # The application/platform configures OpenTelemetry externally
        # This adapter only emits events into that existing environment

        config = OpenTelemetryConfig(
            enabled=True,
            service_name="my-payment-service",
            environment="production",
        )
        adapter = OpenTelemetryAdapter(config)

        # Start a decision span (owned by self-healing engine)
        span_ctx = adapter.start_decision_span(
            decision_type="policy_evaluation",
            domain="payment",
        )

        # Events are attached to the active span
        adapter.emit_circuit_breaker_transition(
            service_name="payment_api",
            from_state="closed",
            to_state="open",
        )

        # End the decision span
        adapter.end_decision_span(span_ctx, outcome="auto_healed")
    """

    _instance: Optional["OpenTelemetryAdapter"] = None
    _lock = threading.Lock()

    def __init__(self, config: Optional[OpenTelemetryConfig] = None):
        """
        Initialize the OpenTelemetry adapter.

        NOTE: This does NOT configure OpenTelemetry.
        All OTel configuration is owned by the application/platform.

        Args:
            config: Optional configuration. If None, loads from environment.
        """
        self.config = config or OpenTelemetryConfig.from_env()
        self._tracer = None
        self._initialized = False

        # Active decision spans managed by the self-healing engine
        # Key: span_id, Value: OTel span object
        self._active_decision_spans: Dict[str, Any] = {}
        self._spans_lock = threading.Lock()

        if self.config.enabled and OPENTELEMETRY_AVAILABLE:
            self._initialize_tracer()

    def _initialize_tracer(self) -> None:
        """
        Get a tracer from the EXISTING TracerProvider.

        CRITICAL: We do NOT call trace.set_tracer_provider().
        We use whatever TracerProvider the application has configured.
        If no TracerProvider is configured, we get a NO-OP tracer.
        """
        if not OPENTELEMETRY_AVAILABLE:
            logger.warning(
                "OpenTelemetry enabled in config but API not installed. "
                "Install opentelemetry-api to enable."
            )
            return

        try:
            # WHY WE DON'T SET TRACER PROVIDER:
            # ─────────────────────────────────────────────────────────
            # The application/platform owns OpenTelemetry configuration.
            # We only get a tracer from the existing provider.
            # If no provider is configured, trace.get_tracer() returns
            # a NO-OP tracer, which is the correct behavior.
            # ─────────────────────────────────────────────────────────

            self._tracer = trace.get_tracer(
                instrumenting_module_name="selfhealing.opentelemetry",
                instrumenting_library_version="1.0.0",
            )

            self._initialized = True
            logger.info(
                f"OpenTelemetry adapter initialized (service={self.config.service_name}). "
                f"Using application's existing TracerProvider."
            )

        except Exception as e:
            logger.error(f"Failed to get OpenTelemetry tracer: {e}")
            self._initialized = False

    @property
    def is_enabled(self) -> bool:
        """Check if adapter is enabled and initialized."""
        return self.config.enabled and self._initialized

    @property
    def is_available(self) -> bool:
        """Check if OpenTelemetry API is available."""
        return OPENTELEMETRY_AVAILABLE

    # =========================================================================
    # Generic Event Emission
    # =========================================================================

    def emit_event(
        self,
        event_type: str,
        attributes: Optional[Dict[str, Any]] = None,
        domain: Optional[str] = None,
    ) -> None:
        """
        Emit a self-healing event.

        EVENT EMISSION RULES:
        ─────────────────────────────────────────────────────────────────
        1. If there is an active decision span → attach event to that span
        2. If there is NO active span → silently drop the event
        3. NEVER create a span just for an event
        4. NEVER emit standalone OpenTelemetry events
        ─────────────────────────────────────────────────────────────────

        WHY EVENTS ARE DROPPED WHEN NO SPAN EXISTS:
        - The adapter does NOT own span lifecycle
        - Creating spans autonomously violates the design contract
        - Standalone events without parent context are meaningless
          for decision-trace analysis
        - The self-healing engine must explicitly start spans
          when decision cycles begin

        Args:
            event_type: Event type identifier
            attributes: Event attributes
            domain: Business domain (optional)
        """
        if not self.is_enabled:
            return

        if not self.config.should_export_event(event_type):
            return

        try:
            # Build complete attributes
            event_attrs = {
                EventAttribute.SERVICE_NAME: self.config.service_name,
                EventAttribute.ENVIRONMENT: self.config.environment,
                EventAttribute.TIMESTAMP: datetime.utcnow().isoformat() + "Z",
            }

            if domain:
                event_attrs[EventAttribute.DOMAIN] = domain

            if attributes:
                event_attrs.update(attributes)

            # Check for active decision span first (managed by us)
            active_span = self._get_active_decision_span()

            if active_span is not None and active_span.is_recording():
                # Attach event to active decision span
                active_span.add_event(event_type, attributes=event_attrs)
                logger.debug(f"Emitted OTel event to decision span: {event_type}")
                return

            # Check for any current span in OTel context (managed by application)
            current_span = trace.get_current_span() if trace else None

            if current_span is not None and current_span.is_recording():
                # Attach event to application's current span
                current_span.add_event(event_type, attributes=event_attrs)
                logger.debug(f"Emitted OTel event to current span: {event_type}")
                return

            # WHY WE DROP THE EVENT HERE:
            # ─────────────────────────────────────────────────────────
            # No active span exists. Per design rules:
            # - We MUST NOT create a span just for this event
            # - We MUST NOT emit standalone events
            # - We silently drop the event
            # - The self-healing engine should have started a span
            #   if this event was part of a decision cycle
            # ─────────────────────────────────────────────────────────
            logger.debug(
                f"Dropped OTel event (no active span): {event_type}. "
                f"Start a decision span to capture events."
            )

        except Exception as e:
            logger.debug(f"Failed to emit OTel event: {e}")

    def _get_active_decision_span(self) -> Optional[Any]:
        """Get the most recently started active decision span."""
        with self._spans_lock:
            for span in reversed(list(self._active_decision_spans.values())):
                if span is not None and hasattr(span, "is_recording") and span.is_recording():
                    return span
        return None

    # =========================================================================
    # Decision Span Management
    # =========================================================================
    #
    # SPAN OWNERSHIP RULES:
    # ─────────────────────────────────────────────────────────────────────
    # - start_decision_span() and end_decision_span() are called ONLY
    #   by the self-healing engine
    # - The adapter does NOT decide when spans start or end
    # - Spans represent coarse-grained decision cycles (seconds to minutes)
    # - Per-request, per-transaction, or per-user spans are FORBIDDEN
    # ─────────────────────────────────────────────────────────────────────

    def start_decision_span(
        self,
        decision_type: str,
        attributes: Optional[Dict[str, Any]] = None,
        domain: Optional[str] = None,
    ) -> DecisionSpanContext:
        """
        Start a coarse-grained decision span.

        This method is called by the SELF-HEALING ENGINE when a decision
        cycle begins. The adapter does NOT autonomously decide to start spans.

        SPAN SEMANTICS:
        - Represents a self-healing decision cycle
        - Duration: seconds to minutes
        - NOT per-request or per-transaction

        Args:
            decision_type: Type of decision cycle (e.g., "policy_evaluation")
            attributes: Initial span attributes
            domain: Business domain

        Returns:
            DecisionSpanContext for tracking the span
        """
        context = DecisionSpanContext(
            decision_type=decision_type,
            domain=domain,
        )

        if not self.is_enabled or not self.config.decision_span_enabled:
            return context

        if self._tracer is None:
            return context

        try:
            span_attrs = {
                EventAttribute.DECISION_TYPE: decision_type,
                EventAttribute.SERVICE_NAME: self.config.service_name,
                EventAttribute.ENVIRONMENT: self.config.environment,
            }

            if domain:
                span_attrs[EventAttribute.DOMAIN] = domain

            if attributes:
                span_attrs.update(attributes)

            # Start the span
            span = self._tracer.start_span(
                f"selfhealing.decision.{decision_type}",
                kind=SpanKind.INTERNAL,
                attributes=span_attrs,
            )

            # Store the span reference
            with self._spans_lock:
                self._active_decision_spans[context.span_id] = span

            context._otel_span = span
            context.attributes = span_attrs

            # Add start event
            span.add_event(
                SelfHealingEventType.DECISION_CYCLE_STARTED.value,
                attributes={"decision_type": decision_type},
            )

            logger.debug(f"Started decision span: {decision_type} (id={context.span_id})")

        except Exception as e:
            logger.debug(f"Failed to start decision span: {e}")

        return context

    def end_decision_span(
        self,
        span_context: DecisionSpanContext,
        outcome: str = "completed",
        attributes: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        End a decision span.

        This method is called by the SELF-HEALING ENGINE when a decision
        cycle completes. The adapter does NOT autonomously end spans.

        Args:
            span_context: Context from start_decision_span
            outcome: Final outcome
            attributes: Additional final attributes
        """
        span_context.is_active = False

        if not self.is_enabled or not self.config.decision_span_enabled:
            return

        # Remove from active spans
        with self._spans_lock:
            self._active_decision_spans.pop(span_context.span_id, None)

        if span_context._otel_span is None:
            return

        try:
            span = span_context._otel_span

            # Set duration
            duration_ms = span_context.get_duration_ms()
            span.set_attribute(EventAttribute.DECISION_DURATION_MS, duration_ms)

            # Set outcome
            span.set_attribute(EventAttribute.DECISION_OUTCOME, outcome)

            # Add additional attributes
            if attributes:
                for key, value in attributes.items():
                    span.set_attribute(key, value)

            # Add completion event
            span.add_event(
                SelfHealingEventType.DECISION_CYCLE_COMPLETED.value,
                attributes={
                    "outcome": outcome,
                    "duration_ms": duration_ms,
                },
            )

            # Set status based on outcome
            if outcome in ("error", "aborted"):
                span.set_status(Status(StatusCode.ERROR, f"Decision {outcome}"))
            else:
                span.set_status(Status(StatusCode.OK))

            # End the span
            span.end()

            logger.debug(
                f"Ended decision span: {span_context.decision_type} -> {outcome} "
                f"(duration={duration_ms}ms)"
            )

        except Exception as e:
            logger.debug(f"Failed to end decision span: {e}")

    # =========================================================================
    # Circuit Breaker Events
    # =========================================================================

    def emit_circuit_breaker_transition(
        self,
        service_name: str,
        from_state: str,
        to_state: str,
        reason: Optional[str] = None,
        manually_controlled: bool = False,
        failure_count: Optional[int] = None,
        success_count: Optional[int] = None,
    ) -> None:
        """
        Emit circuit breaker state transition event.

        Only emitted on STATE TRANSITIONS, not on every request.
        """
        # Determine event type
        event_type = {
            "open": SelfHealingEventType.CIRCUIT_BREAKER_OPENED,
            "closed": SelfHealingEventType.CIRCUIT_BREAKER_CLOSED,
            "half_open": SelfHealingEventType.CIRCUIT_BREAKER_HALF_OPENED,
        }.get(to_state, SelfHealingEventType.CIRCUIT_BREAKER_OPENED)

        if manually_controlled:
            event_type = SelfHealingEventType.CIRCUIT_BREAKER_MANUAL_OVERRIDE

        attributes = {
            EventAttribute.CB_STATE_FROM: from_state,
            EventAttribute.CB_STATE_TO: to_state,
            EventAttribute.CB_MANUALLY_CONTROLLED: manually_controlled,
        }

        if reason:
            attributes[EventAttribute.CB_REASON] = reason
        if failure_count is not None:
            attributes[EventAttribute.CB_FAILURE_COUNT] = failure_count
        if success_count is not None:
            attributes[EventAttribute.CB_SUCCESS_COUNT] = success_count

        self.emit_event(
            event_type=event_type.value,
            attributes=attributes,
            domain=service_name,
        )

    # =========================================================================
    # Retry Events
    # =========================================================================

    def emit_retry_attempt(
        self,
        domain: str,
        attempt_number: int,
        max_attempts: int,
        error_type: Optional[str] = None,
        backoff_seconds: Optional[float] = None,
    ) -> None:
        """Emit retry attempt event."""
        attributes = {
            EventAttribute.RETRY_ATTEMPT_NUMBER: attempt_number,
            EventAttribute.RETRY_MAX_ATTEMPTS: max_attempts,
        }

        if error_type:
            attributes[EventAttribute.RETRY_ERROR_TYPE] = error_type
        if backoff_seconds is not None:
            attributes[EventAttribute.RETRY_BACKOFF_SECONDS] = backoff_seconds

        self.emit_event(
            event_type=SelfHealingEventType.RETRY_ATTEMPT.value,
            attributes=attributes,
            domain=domain,
        )

    def emit_retry_exhausted(
        self,
        domain: str,
        total_attempts: int,
        final_error: Optional[str] = None,
    ) -> None:
        """Emit retry exhausted event."""
        attributes = {
            EventAttribute.RETRY_ATTEMPT_NUMBER: total_attempts,
        }

        if final_error:
            attributes[EventAttribute.RETRY_ERROR_TYPE] = final_error

        self.emit_event(
            event_type=SelfHealingEventType.RETRY_EXHAUSTED.value,
            attributes=attributes,
            domain=domain,
        )

    def emit_retry_success(
        self,
        domain: str,
        attempt_number: int,
    ) -> None:
        """Emit retry success event."""
        self.emit_event(
            event_type=SelfHealingEventType.RETRY_SUCCESS.value,
            attributes={EventAttribute.RETRY_ATTEMPT_NUMBER: attempt_number},
            domain=domain,
        )

    # =========================================================================
    # DLQ Events
    # =========================================================================

    def emit_dlq_enqueue(
        self,
        domain: str,
        failure_type: str,
        dlq_id: Optional[int] = None,
    ) -> None:
        """Emit DLQ enqueue event."""
        attributes = {EventAttribute.DLQ_FAILURE_TYPE: failure_type}
        if dlq_id is not None:
            attributes[EventAttribute.DLQ_ID] = dlq_id

        self.emit_event(
            event_type=SelfHealingEventType.DLQ_ENQUEUED.value,
            attributes=attributes,
            domain=domain,
        )

    def emit_dlq_replay_started(
        self,
        domain: str,
        dlq_id: int,
        replay_type: str = "auto",
    ) -> None:
        """Emit DLQ replay started event."""
        self.emit_event(
            event_type=SelfHealingEventType.DLQ_REPLAY_STARTED.value,
            attributes={
                EventAttribute.DLQ_ID: dlq_id,
                EventAttribute.DLQ_REPLAY_TYPE: replay_type,
            },
            domain=domain,
        )

    def emit_dlq_replay_completed(
        self,
        domain: str,
        dlq_id: int,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        """Emit DLQ replay completed event."""
        event_type = (
            SelfHealingEventType.DLQ_REPLAY_SUCCESS
            if success
            else SelfHealingEventType.DLQ_REPLAY_FAILED
        )

        attributes = {EventAttribute.DLQ_ID: dlq_id}
        if error:
            attributes["error"] = error

        self.emit_event(
            event_type=event_type.value,
            attributes=attributes,
            domain=domain,
        )

    # =========================================================================
    # Rate Limit Events
    # =========================================================================

    def emit_rate_limit_triggered(
        self,
        service_name: str,
        limit_type: str,
        current_count: int,
        threshold: int,
        window_seconds: Optional[int] = None,
    ) -> None:
        """Emit rate limit triggered event."""
        attributes = {
            EventAttribute.RATE_LIMIT_TYPE: limit_type,
            EventAttribute.RATE_LIMIT_CURRENT: current_count,
            EventAttribute.RATE_LIMIT_THRESHOLD: threshold,
        }
        if window_seconds is not None:
            attributes[EventAttribute.RATE_LIMIT_WINDOW_SECONDS] = window_seconds

        self.emit_event(
            event_type=SelfHealingEventType.RATE_LIMIT_TRIGGERED.value,
            attributes=attributes,
            domain=service_name,
        )

    def emit_rate_limit_cascade_detected(
        self,
        service_name: str,
        count_in_window: int,
        threshold: int,
    ) -> None:
        """Emit rate limit cascade detection event."""
        self.emit_event(
            event_type=SelfHealingEventType.RATE_LIMIT_CASCADE_DETECTED.value,
            attributes={
                EventAttribute.RATE_LIMIT_CURRENT: count_in_window,
                EventAttribute.RATE_LIMIT_THRESHOLD: threshold,
            },
            domain=service_name,
        )

    def emit_self_ddos_detected(
        self,
        service_name: str,
        request_count: int,
        threshold: int,
    ) -> None:
        """Emit self-DDoS detection event."""
        self.emit_event(
            event_type=SelfHealingEventType.SELF_DDOS_DETECTED.value,
            attributes={
                EventAttribute.RATE_LIMIT_CURRENT: request_count,
                EventAttribute.RATE_LIMIT_THRESHOLD: threshold,
            },
            domain=service_name,
        )

    # =========================================================================
    # SLO Events
    # =========================================================================

    def emit_slo_threshold_approaching(
        self,
        slo_name: str,
        current_value: float,
        threshold_value: float,
        threshold_percentage: float,
    ) -> None:
        """Emit SLO threshold approaching event."""
        self.emit_event(
            event_type=SelfHealingEventType.SLO_THRESHOLD_APPROACHING.value,
            attributes={
                EventAttribute.SLO_NAME: slo_name,
                EventAttribute.SLO_CURRENT_VALUE: current_value,
                EventAttribute.SLO_THRESHOLD_VALUE: threshold_value,
                EventAttribute.SLO_THRESHOLD_PERCENTAGE: threshold_percentage,
            },
        )

    def emit_slo_breached(
        self,
        slo_name: str,
        current_value: float,
        threshold_value: float,
    ) -> None:
        """Emit SLO breached event."""
        self.emit_event(
            event_type=SelfHealingEventType.SLO_BREACHED.value,
            attributes={
                EventAttribute.SLO_NAME: slo_name,
                EventAttribute.SLO_CURRENT_VALUE: current_value,
                EventAttribute.SLO_THRESHOLD_VALUE: threshold_value,
            },
        )

    def emit_slo_recovered(
        self,
        slo_name: str,
        current_value: float,
    ) -> None:
        """Emit SLO recovered event."""
        self.emit_event(
            event_type=SelfHealingEventType.SLO_RECOVERED.value,
            attributes={
                EventAttribute.SLO_NAME: slo_name,
                EventAttribute.SLO_CURRENT_VALUE: current_value,
            },
        )

    # =========================================================================
    # Policy Events
    # =========================================================================

    def emit_policy_evaluation(
        self,
        policy_name: str,
        outcome: str,
        reason: Optional[str] = None,
        is_automatic: bool = True,
    ) -> None:
        """Emit policy evaluation event."""
        attributes = {
            EventAttribute.POLICY_NAME: policy_name,
            EventAttribute.POLICY_OUTCOME: outcome,
            EventAttribute.POLICY_IS_AUTOMATIC: is_automatic,
        }
        if reason:
            attributes[EventAttribute.CB_REASON] = reason

        self.emit_event(
            event_type=SelfHealingEventType.POLICY_EVALUATED.value,
            attributes=attributes,
        )

    def emit_auto_heal_allowed(
        self,
        policy_name: str,
        action: str,
        reason: Optional[str] = None,
    ) -> None:
        """Emit auto-heal allowed event."""
        attributes = {
            EventAttribute.POLICY_NAME: policy_name,
            EventAttribute.OPERATOR_ACTION: action,
        }
        if reason:
            attributes[EventAttribute.CB_REASON] = reason

        self.emit_event(
            event_type=SelfHealingEventType.POLICY_AUTO_HEAL_ALLOWED.value,
            attributes=attributes,
        )

    def emit_auto_heal_blocked(
        self,
        policy_name: str,
        action: str,
        reason: Optional[str] = None,
    ) -> None:
        """Emit auto-heal blocked event."""
        attributes = {
            EventAttribute.POLICY_NAME: policy_name,
            EventAttribute.OPERATOR_ACTION: action,
        }
        if reason:
            attributes[EventAttribute.CB_REASON] = reason

        self.emit_event(
            event_type=SelfHealingEventType.POLICY_AUTO_HEAL_BLOCKED.value,
            attributes=attributes,
        )

    def emit_manual_override(
        self,
        action: str,
        target: str,
        reason: Optional[str] = None,
        operator_id: Optional[str] = None,
    ) -> None:
        """Emit manual override event."""
        attributes = {
            EventAttribute.OPERATOR_ACTION: action,
            "target": target,
        }
        if reason:
            attributes[EventAttribute.CB_REASON] = reason
        if operator_id:
            attributes[EventAttribute.OPERATOR_ID] = operator_id

        self.emit_event(
            event_type=SelfHealingEventType.CIRCUIT_BREAKER_MANUAL_OVERRIDE.value,
            attributes=attributes,
            domain=target,
        )

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def shutdown(self) -> None:
        """
        Shutdown the adapter.

        NOTE: We do NOT shutdown the TracerProvider because we don't own it.
        The application/platform is responsible for OTel lifecycle.
        """
        # Clear active spans
        with self._spans_lock:
            self._active_decision_spans.clear()

        logger.debug("OpenTelemetry adapter shutdown complete")

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """
        Request flush of pending telemetry.

        NOTE: We request flush but don't own the TracerProvider,
        so we rely on the application's provider implementation.
        """
        if not self.is_enabled:
            return True

        try:
            if trace and hasattr(trace, "get_tracer_provider"):
                provider = trace.get_tracer_provider()
                if hasattr(provider, "force_flush"):
                    return provider.force_flush(timeout_millis)
            return True
        except Exception as e:
            logger.debug(f"Error during OTel flush: {e}")
            return False


# =============================================================================
# Singleton Access
# =============================================================================


_adapter_instance: Optional[OpenTelemetryAdapter] = None
_adapter_lock = threading.Lock()


def get_opentelemetry_adapter(
    config: Optional[OpenTelemetryConfig] = None,
) -> OpenTelemetryAdapter:
    """
    Get the global OpenTelemetry adapter instance.

    Returns a singleton adapter. If OpenTelemetry is not installed
    or adapter is disabled, returns a NO-OP adapter.

    The adapter does NOT configure OpenTelemetry - it uses whatever
    TracerProvider the application has configured.

    Args:
        config: Optional configuration for first initialization

    Returns:
        OpenTelemetryAdapter instance (or NO-OP equivalent)
    """
    global _adapter_instance

    if _adapter_instance is not None:
        return _adapter_instance

    with _adapter_lock:
        # Double-check after acquiring lock
        if _adapter_instance is not None:
            return _adapter_instance

        # Determine configuration
        if config is None:
            try:
                config = OpenTelemetryConfig.from_django_settings()
            except Exception:
                config = OpenTelemetryConfig.from_env()

        # Check if we should use NO-OP
        if not config.enabled or not OPENTELEMETRY_AVAILABLE:
            from .noop import NoOpOpenTelemetryAdapter

            _adapter_instance = NoOpOpenTelemetryAdapter(config)
            logger.debug(
                f"OpenTelemetry adapter created in NO-OP mode "
                f"(enabled={config.enabled}, api_available={OPENTELEMETRY_AVAILABLE})"
            )
        else:
            _adapter_instance = OpenTelemetryAdapter(config)

        return _adapter_instance


def reset_adapter() -> None:
    """
    Reset the global adapter instance.

    Used primarily for testing.
    """
    global _adapter_instance

    with _adapter_lock:
        if _adapter_instance is not None:
            _adapter_instance.shutdown()
        _adapter_instance = None
