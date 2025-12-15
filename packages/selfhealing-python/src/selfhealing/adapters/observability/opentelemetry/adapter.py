"""
OpenTelemetry Adapter Implementation

Main adapter class that bridges self-healing signals to OpenTelemetry.

ARCHITECTURE:
- Uses lazy imports for OpenTelemetry SDK
- Falls back to NO-OP when OTel not installed
- Disabled by default, requires explicit enablement
- Event-based emission (not request-based)

IMPORTANT:
- This does NOT replace Prometheus metrics
- This is an OPTIONAL extension for APM integration
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, TYPE_CHECKING
from functools import lru_cache
from datetime import datetime
import threading

from .config import OpenTelemetryConfig
from .spans import DecisionSpanContext, DecisionOutcome
from .events import SelfHealingEventType, EventAttribute

logger = logging.getLogger(__name__)

# =============================================================================
# OpenTelemetry SDK Detection
# =============================================================================

OPENTELEMETRY_AVAILABLE = False
_otel_tracer = None
_otel_event_logger = None

try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode, SpanKind
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME, DEPLOYMENT_ENVIRONMENT

    # Try to import OTLP exporter (optional)
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        OTLP_AVAILABLE = True
    except ImportError:
        OTLP_AVAILABLE = False
        OTLPSpanExporter = None

    # Try to import batch processor
    try:
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        BatchSpanProcessor = None

    OPENTELEMETRY_AVAILABLE = True
    logger.debug("OpenTelemetry SDK detected and available")

except ImportError:
    OPENTELEMETRY_AVAILABLE = False
    trace = None
    Status = None
    StatusCode = None
    SpanKind = None
    TracerProvider = None
    Resource = None
    SERVICE_NAME = None
    DEPLOYMENT_ENVIRONMENT = None
    OTLPSpanExporter = None
    BatchSpanProcessor = None
    OTLP_AVAILABLE = False
    logger.debug("OpenTelemetry SDK not installed, adapter will use NO-OP mode")


# =============================================================================
# OpenTelemetry Adapter
# =============================================================================


class OpenTelemetryAdapter:
    """
    OpenTelemetry Adapter for Self-Healing System.

    Exports self-healing decision events and operational signals
    to external APM platforms via OpenTelemetry protocol.

    DESIGN PRINCIPLES:
    - Optional extension (graceful NO-OP when OTel not installed)
    - Disabled by default
    - Event-based emission only
    - No per-request tracing
    - No sensitive data export

    Usage:
        # Initialize with configuration
        config = OpenTelemetryConfig(
            enabled=True,
            service_name="my-payment-service",
            environment="production",
        )
        adapter = OpenTelemetryAdapter(config)

        # Emit events
        adapter.emit_circuit_breaker_transition(
            service_name="payment_api",
            from_state="closed",
            to_state="open",
            reason="failure_threshold_exceeded",
        )

        # Use decision spans for coarse-grained cycles
        span_ctx = adapter.start_decision_span(
            decision_type="policy_evaluation",
            domain="payment",
        )
        # ... evaluation logic ...
        adapter.end_decision_span(span_ctx, outcome="auto_healed")
    """

    _instance: Optional["OpenTelemetryAdapter"] = None
    _lock = threading.Lock()

    def __init__(self, config: Optional[OpenTelemetryConfig] = None):
        """
        Initialize the OpenTelemetry adapter.

        Args:
            config: Optional configuration. If None, loads from environment.
        """
        self.config = config or OpenTelemetryConfig.from_env()
        self._tracer = None
        self._initialized = False
        self._resource = None

        if self.config.enabled and OPENTELEMETRY_AVAILABLE:
            self._initialize_otel()

    def _initialize_otel(self) -> None:
        """Initialize OpenTelemetry tracer and exporter."""
        if not OPENTELEMETRY_AVAILABLE:
            logger.warning(
                "OpenTelemetry enabled in config but SDK not installed. " "Install opentelemetry-api and opentelemetry-sdk."
            )
            return

        try:
            # Build resource attributes
            resource_attrs = {
                SERVICE_NAME: self.config.service_name,
                DEPLOYMENT_ENVIRONMENT: self.config.environment,
                "selfhealing.adapter.version": "1.0.0",
            }
            resource_attrs.update(self.config.additional_resource_attributes)

            self._resource = Resource.create(resource_attrs)

            # Create tracer provider
            provider = TracerProvider(resource=self._resource)

            # Add OTLP exporter if endpoint is configured and available
            if self.config.endpoint and OTLP_AVAILABLE and BatchSpanProcessor:
                exporter = OTLPSpanExporter(
                    endpoint=self.config.endpoint,
                    timeout=self.config.export_timeout_seconds,
                )
                processor = BatchSpanProcessor(exporter)
                provider.add_span_processor(processor)
                logger.info(f"OpenTelemetry OTLP exporter configured: {self.config.endpoint}")

            # Set as global tracer provider
            trace.set_tracer_provider(provider)

            # Get tracer
            self._tracer = trace.get_tracer(
                "selfhealing.opentelemetry",
                "1.0.0",
            )

            self._initialized = True
            logger.info(f"OpenTelemetry adapter initialized for service: {self.config.service_name}")

        except Exception as e:
            logger.error(f"Failed to initialize OpenTelemetry: {e}")
            self._initialized = False

    @property
    def is_enabled(self) -> bool:
        """Check if adapter is enabled and initialized."""
        return self.config.enabled and self._initialized

    @property
    def is_available(self) -> bool:
        """Check if OpenTelemetry SDK is available."""
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

            # Get current span (if any) and add event
            current_span = trace.get_current_span() if trace else None
            if current_span and current_span.is_recording():
                current_span.add_event(event_type, attributes=event_attrs)
            else:
                # Create a minimal span just for the event
                with self._tracer.start_as_current_span(
                    f"selfhealing.event.{event_type.split('.')[-1]}",
                    kind=SpanKind.INTERNAL,
                ) as span:
                    span.add_event(event_type, attributes=event_attrs)

            logger.debug(f"Emitted OTel event: {event_type}")

        except Exception as e:
            logger.debug(f"Failed to emit OTel event: {e}")

    # =========================================================================
    # Decision Span Management
    # =========================================================================

    def start_decision_span(
        self,
        decision_type: str,
        attributes: Optional[Dict[str, Any]] = None,
        domain: Optional[str] = None,
    ) -> DecisionSpanContext:
        """
        Start a coarse-grained decision span.

        Args:
            decision_type: Type of decision cycle
            attributes: Initial attributes
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

            # Start the span (but don't set as current - we manage it explicitly)
            span = self._tracer.start_span(
                f"selfhealing.decision.{decision_type}",
                kind=SpanKind.INTERNAL,
                attributes=span_attrs,
            )

            context._otel_span = span
            context.attributes = span_attrs

            # Add start event
            span.add_event(
                SelfHealingEventType.DECISION_CYCLE_STARTED.value,
                attributes={"decision_type": decision_type},
            )

            logger.debug(f"Started decision span: {decision_type}")

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

        Args:
            span_context: Context from start_decision_span
            outcome: Final outcome
            attributes: Additional final attributes
        """
        span_context.is_active = False

        if not self.is_enabled or not self.config.decision_span_enabled:
            return

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

            logger.debug(f"Ended decision span: {span_context.decision_type} -> {outcome}")

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

        Args:
            service_name: Affected service
            from_state: Previous state
            to_state: New state
            reason: Transition reason
            manually_controlled: Whether manually triggered
            failure_count: Current failure count
            success_count: Current success count
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
        event_type = SelfHealingEventType.DLQ_REPLAY_SUCCESS if success else SelfHealingEventType.DLQ_REPLAY_FAILED

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
        """Shutdown the adapter and flush pending telemetry."""
        if not self.is_enabled:
            return

        try:
            if trace and hasattr(trace, "get_tracer_provider"):
                provider = trace.get_tracer_provider()
                if hasattr(provider, "shutdown"):
                    provider.shutdown()
                    logger.info("OpenTelemetry adapter shutdown complete")
        except Exception as e:
            logger.debug(f"Error during OTel shutdown: {e}")

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """
        Force flush pending telemetry.

        Args:
            timeout_millis: Timeout in milliseconds

        Returns:
            True if flush succeeded
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

    Returns a singleton adapter. If no adapter exists and config is not provided,
    loads configuration from environment/settings.

    If OpenTelemetry SDK is not installed or adapter is disabled,
    returns a NO-OP adapter.

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
                f"(enabled={config.enabled}, sdk_available={OPENTELEMETRY_AVAILABLE})"
            )
        else:
            _adapter_instance = OpenTelemetryAdapter(config)
            logger.info(f"OpenTelemetry adapter initialized " f"(service={config.service_name}, env={config.environment})")

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
