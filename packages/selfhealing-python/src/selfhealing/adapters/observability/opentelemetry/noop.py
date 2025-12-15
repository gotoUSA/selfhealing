"""
NO-OP Implementations for OpenTelemetry Adapter

When OpenTelemetry SDK is not installed, these NO-OP classes
provide the same interface with zero functionality.

This ensures:
- No ImportError when OTel is not installed
- Zero overhead when adapter is disabled
- Same API contract for calling code
"""

from __future__ import annotations

from typing import Any, Optional, Dict
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# NO-OP Tracer
# =============================================================================


class NoOpSpan:
    """NO-OP Span that does nothing."""

    def __init__(self, name: str = "noop"):
        self._name = name

    def set_attribute(self, key: str, value: Any) -> "NoOpSpan":
        return self

    def set_attributes(self, attributes: Dict[str, Any]) -> "NoOpSpan":
        return self

    def add_event(
        self,
        name: str,
        attributes: Optional[Dict[str, Any]] = None,
        timestamp: Optional[int] = None,
    ) -> None:
        pass

    def record_exception(
        self,
        exception: Exception,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> None:
        pass

    def set_status(self, status: Any, description: Optional[str] = None) -> None:
        pass

    def end(self, end_time: Optional[int] = None) -> None:
        pass

    def is_recording(self) -> bool:
        return False

    def get_span_context(self) -> "NoOpSpanContext":
        return NoOpSpanContext()

    def __enter__(self) -> "NoOpSpan":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class NoOpSpanContext:
    """NO-OP SpanContext."""

    trace_id: int = 0
    span_id: int = 0
    is_valid: bool = False
    is_remote: bool = False


class NoOpTracer:
    """NO-OP Tracer that creates NoOpSpan instances."""

    def start_span(
        self,
        name: str,
        context: Optional[Any] = None,
        kind: Optional[Any] = None,
        attributes: Optional[Dict[str, Any]] = None,
        start_time: Optional[int] = None,
        record_exception: bool = True,
        set_status_on_exception: bool = True,
    ) -> NoOpSpan:
        return NoOpSpan(name)

    def start_as_current_span(
        self,
        name: str,
        context: Optional[Any] = None,
        kind: Optional[Any] = None,
        attributes: Optional[Dict[str, Any]] = None,
        start_time: Optional[int] = None,
        record_exception: bool = True,
        set_status_on_exception: bool = True,
        end_on_exit: bool = True,
    ) -> NoOpSpan:
        return NoOpSpan(name)


class NoOpTracerProvider:
    """NO-OP TracerProvider."""

    def get_tracer(
        self,
        instrumenting_module_name: str,
        instrumenting_library_version: Optional[str] = None,
        schema_url: Optional[str] = None,
    ) -> NoOpTracer:
        return NoOpTracer()


# =============================================================================
# NO-OP Event Logger
# =============================================================================


class NoOpEventLogger:
    """NO-OP Event Logger that does nothing."""

    def emit(
        self,
        name: str,
        attributes: Optional[Dict[str, Any]] = None,
        timestamp: Optional[int] = None,
        context: Optional[Any] = None,
        severity_number: Optional[int] = None,
    ) -> None:
        pass


class NoOpLoggerProvider:
    """NO-OP LoggerProvider."""

    def get_logger(
        self,
        name: str,
        version: Optional[str] = None,
        schema_url: Optional[str] = None,
    ) -> NoOpEventLogger:
        return NoOpEventLogger()


# =============================================================================
# NO-OP Adapter
# =============================================================================


@dataclass
class NoOpDecisionSpanContext:
    """NO-OP Decision Span Context."""

    span_id: str = ""
    decision_type: str = ""
    is_active: bool = False

    def add_event(
        self,
        name: str,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> None:
        pass

    def set_outcome(
        self,
        outcome: str,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> None:
        pass


class NoOpOpenTelemetryAdapter:
    """
    NO-OP OpenTelemetry Adapter.

    Used when:
    - OpenTelemetry SDK is not installed
    - Adapter is explicitly disabled
    - Configuration sets enabled=False

    All methods are safe to call but do nothing.
    """

    def __init__(self, config: Optional[Any] = None):
        self._initialized = False
        logger.debug(
            "OpenTelemetry adapter initialized in NO-OP mode. " "Install opentelemetry-api and opentelemetry-sdk to enable."
        )

    @property
    def is_enabled(self) -> bool:
        return False

    @property
    def is_available(self) -> bool:
        return False

    def emit_event(
        self,
        event_type: str,
        attributes: Optional[Dict[str, Any]] = None,
        domain: Optional[str] = None,
    ) -> None:
        """NO-OP event emission."""
        pass

    def start_decision_span(
        self,
        decision_type: str,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> NoOpDecisionSpanContext:
        """Return NO-OP decision span context."""
        return NoOpDecisionSpanContext(decision_type=decision_type)

    def end_decision_span(
        self,
        span_context: NoOpDecisionSpanContext,
        outcome: str = "completed",
        attributes: Optional[Dict[str, Any]] = None,
    ) -> None:
        """NO-OP span end."""
        pass

    # =========================================================================
    # Circuit Breaker Events (NO-OP)
    # =========================================================================

    def emit_circuit_breaker_transition(
        self,
        service_name: str,
        from_state: str,
        to_state: str,
        reason: Optional[str] = None,
        manually_controlled: bool = False,
    ) -> None:
        pass

    # =========================================================================
    # Retry Events (NO-OP)
    # =========================================================================

    def emit_retry_attempt(
        self,
        domain: str,
        attempt_number: int,
        max_attempts: int,
        error_type: Optional[str] = None,
        backoff_seconds: Optional[float] = None,
    ) -> None:
        pass

    def emit_retry_exhausted(
        self,
        domain: str,
        total_attempts: int,
        final_error: Optional[str] = None,
    ) -> None:
        pass

    # =========================================================================
    # DLQ Events (NO-OP)
    # =========================================================================

    def emit_dlq_enqueue(
        self,
        domain: str,
        failure_type: str,
        dlq_id: Optional[int] = None,
    ) -> None:
        pass

    def emit_dlq_replay_started(
        self,
        domain: str,
        dlq_id: int,
        replay_type: str = "auto",
    ) -> None:
        pass

    def emit_dlq_replay_completed(
        self,
        domain: str,
        dlq_id: int,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        pass

    # =========================================================================
    # Rate Limit Events (NO-OP)
    # =========================================================================

    def emit_rate_limit_triggered(
        self,
        service_name: str,
        limit_type: str,
        current_count: int,
        threshold: int,
    ) -> None:
        pass

    # =========================================================================
    # SLO Events (NO-OP)
    # =========================================================================

    def emit_slo_threshold_approaching(
        self,
        slo_name: str,
        current_value: float,
        threshold_value: float,
        threshold_percentage: float,
    ) -> None:
        pass

    def emit_slo_breached(
        self,
        slo_name: str,
        current_value: float,
        threshold_value: float,
    ) -> None:
        pass

    # =========================================================================
    # Policy Events (NO-OP)
    # =========================================================================

    def emit_policy_evaluation(
        self,
        policy_name: str,
        outcome: str,
        reason: Optional[str] = None,
        is_automatic: bool = True,
    ) -> None:
        pass

    def emit_manual_override(
        self,
        action: str,
        target: str,
        reason: Optional[str] = None,
        operator_id: Optional[str] = None,
    ) -> None:
        pass

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def shutdown(self) -> None:
        """NO-OP shutdown."""
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """NO-OP flush."""
        return True
