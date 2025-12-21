"""
Decision Span Management

Provides coarse-grained spans for self-healing decision cycles.
Decision spans represent long-lived decision windows (seconds to minutes),
NOT individual requests or transactions.

CRITICAL DESIGN RULES:
- Span boundaries are explicitly triggered by self-healing engine
- Adapter does NOT autonomously decide when cycles start/end
- No per-request spans
- Spans only for meaningful decision evaluations
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, TYPE_CHECKING
from datetime import datetime, timezone
from enum import Enum
import uuid
import logging

logger = logging.getLogger(__name__)


class DecisionType(str, Enum):
    """
    Types of decision cycles that warrant a span.

    Each type represents a coarse-grained decision window.
    """

    # Policy evaluation cycle (e.g., evaluating auto-heal eligibility)
    POLICY_EVALUATION = "policy_evaluation"

    # Manual override session (operator intervention period)
    MANUAL_OVERRIDE = "manual_override"

    # Circuit breaker recovery cycle
    CIRCUIT_BREAKER_RECOVERY = "circuit_breaker_recovery"

    # DLQ batch replay cycle
    DLQ_BATCH_REPLAY = "dlq_batch_replay"

    # SLO breach response cycle
    SLO_BREACH_RESPONSE = "slo_breach_response"

    # Rate limit cooldown cycle
    RATE_LIMIT_COOLDOWN = "rate_limit_cooldown"


class DecisionOutcome(str, Enum):
    """Possible outcomes for a decision cycle."""

    COMPLETED = "completed"
    """Normal completion."""

    AUTO_HEALED = "auto_healed"
    """Automatic recovery succeeded."""

    MANUAL_INTERVENTION = "manual_intervention"
    """Escalated to manual intervention."""

    BLOCKED = "blocked"
    """Action blocked by policy."""

    TIMEOUT = "timeout"
    """Decision cycle timed out."""

    ERROR = "error"
    """Error during decision cycle."""

    ABORTED = "aborted"
    """Decision cycle aborted."""


@dataclass
class DecisionSpanContext:
    """
    Context for an active decision span.

    This context is returned when starting a decision span and
    must be passed back when ending the span.

    IMPORTANT:
    - Hold this context for the duration of the decision cycle
    - Events can be added to this context during the cycle
    - End the span explicitly when the decision cycle completes
    """

    span_id: str = field(default_factory=lambda: str(uuid.uuid4())[:16])
    """Unique identifier for this span."""

    decision_type: str = ""
    """Type of decision being evaluated."""

    started_at: datetime = field(default_factory=datetime.utcnow)
    """When the decision cycle started."""

    is_active: bool = True
    """Whether this span is still active."""

    domain: Optional[str] = None
    """Business domain associated with this decision."""

    attributes: Dict[str, Any] = field(default_factory=dict)
    """Attributes collected during the decision cycle."""

    events: list = field(default_factory=list)
    """Events recorded during the decision cycle."""

    # Internal: reference to actual OTel span (when available)
    _otel_span: Optional[Any] = None

    def add_event(
        self,
        name: str,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Add an event to this decision span.

        Args:
            name: Event name
            attributes: Event attributes
        """
        event_data = {
            "name": name,
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "attributes": attributes or {},
        }
        self.events.append(event_data)

        # If we have an actual OTel span, add the event there too
        if self._otel_span is not None and hasattr(self._otel_span, "add_event"):
            try:
                self._otel_span.add_event(name, attributes=attributes)
            except Exception as e:
                logger.debug(f"Failed to add event to OTel span: {e}")

    def set_attribute(self, key: str, value: Any) -> None:
        """
        Set an attribute on this decision span.

        Args:
            key: Attribute key
            value: Attribute value
        """
        self.attributes[key] = value

        if self._otel_span is not None and hasattr(self._otel_span, "set_attribute"):
            try:
                self._otel_span.set_attribute(key, value)
            except Exception as e:
                logger.debug(f"Failed to set attribute on OTel span: {e}")

    def set_outcome(
        self,
        outcome: DecisionOutcome | str,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Set the outcome for this decision span.

        Args:
            outcome: Decision outcome
            attributes: Additional outcome attributes
        """
        outcome_value = outcome.value if isinstance(outcome, Enum) else outcome
        self.set_attribute("selfhealing.decision.outcome", outcome_value)

        if attributes:
            for key, value in attributes.items():
                self.set_attribute(key, value)

    def get_duration_ms(self) -> int:
        """Get duration of this span in milliseconds."""
        elapsed = datetime.now(timezone.utc) - self.started_at
        return int(elapsed.total_seconds() * 1000)


# =============================================================================
# Module-level convenience functions
# =============================================================================


def start_decision_span(
    decision_type: DecisionType | str,
    attributes: Optional[Dict[str, Any]] = None,
    domain: Optional[str] = None,
) -> DecisionSpanContext:
    """
    Start a new decision span.

    A decision span represents a coarse-grained self-healing decision cycle.
    This function should be called at the START of a decision evaluation,
    not on every request.

    Args:
        decision_type: Type of decision being evaluated
        attributes: Initial span attributes
        domain: Business domain (optional)

    Returns:
        DecisionSpanContext to be passed to end_decision_span

    Example:
        # Start a policy evaluation cycle
        span_ctx = start_decision_span(
            DecisionType.POLICY_EVALUATION,
            attributes={"target_service": "payment_api"},
            domain="payment",
        )

        try:
            # ... evaluate policy ...
            span_ctx.add_event("policy_rule_evaluated", {"rule": "max_retries"})

            # ... take action ...
            span_ctx.set_outcome(DecisionOutcome.AUTO_HEALED)

        finally:
            end_decision_span(span_ctx)
    """
    from selfhealing.adapters.observability.opentelemetry.adapter import (
        get_opentelemetry_adapter,
    )

    adapter = get_opentelemetry_adapter()
    return adapter.start_decision_span(
        decision_type=decision_type.value if isinstance(decision_type, Enum) else decision_type,
        attributes=attributes,
        domain=domain,
    )


def end_decision_span(
    span_context: DecisionSpanContext,
    outcome: DecisionOutcome | str = DecisionOutcome.COMPLETED,
    attributes: Optional[Dict[str, Any]] = None,
) -> None:
    """
    End a decision span.

    Must be called when the decision cycle completes.

    Args:
        span_context: Context returned from start_decision_span
        outcome: Final outcome of the decision cycle
        attributes: Additional final attributes

    Example:
        end_decision_span(
            span_ctx,
            outcome=DecisionOutcome.AUTO_HEALED,
            attributes={"actions_taken": 3},
        )
    """
    from selfhealing.adapters.observability.opentelemetry.adapter import (
        get_opentelemetry_adapter,
    )

    adapter = get_opentelemetry_adapter()
    adapter.end_decision_span(
        span_context=span_context,
        outcome=outcome.value if isinstance(outcome, Enum) else outcome,
        attributes=attributes,
    )
