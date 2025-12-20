"""
Self-Healing Event Types and Event Emission

Defines structured event types for self-healing telemetry.
Events are preferred over spans for most self-healing signals.

DESIGN PRINCIPLES:
- Events are emitted ONLY on state transitions or decision evaluations
- No periodic/heartbeat/polling emissions
- No request-level granularity
- No PII or sensitive data
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional, TYPE_CHECKING
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class SelfHealingEventType(str, Enum):
    """
    Enumeration of self-healing event types.

    Events are categorized by the subsystem that generates them.
    All events represent meaningful state transitions or decisions.
    """

    # =========================================================================
    # Circuit Breaker Events
    # =========================================================================

    CIRCUIT_BREAKER_OPENED = "selfhealing.circuit_breaker.opened"
    """Circuit breaker transitioned to OPEN state."""

    CIRCUIT_BREAKER_CLOSED = "selfhealing.circuit_breaker.closed"
    """Circuit breaker transitioned to CLOSED state."""

    CIRCUIT_BREAKER_HALF_OPENED = "selfhealing.circuit_breaker.half_opened"
    """Circuit breaker transitioned to HALF_OPEN state."""

    CIRCUIT_BREAKER_MANUAL_OVERRIDE = "selfhealing.circuit_breaker.manual_override"
    """Circuit breaker state manually overridden by operator."""

    # =========================================================================
    # Retry Events
    # =========================================================================

    RETRY_ATTEMPT = "selfhealing.retry.attempt"
    """A retry attempt was made."""

    RETRY_EXHAUSTED = "selfhealing.retry.exhausted"
    """All retry attempts exhausted."""

    RETRY_SUCCESS = "selfhealing.retry.success"
    """Retry succeeded after previous failures."""

    # =========================================================================
    # DLQ Events
    # =========================================================================

    DLQ_ENQUEUED = "selfhealing.dlq.enqueued"
    """Failed operation added to Dead Letter Queue."""

    DLQ_REPLAY_STARTED = "selfhealing.dlq.replay_started"
    """DLQ replay operation initiated."""

    DLQ_REPLAY_SUCCESS = "selfhealing.dlq.replay_success"
    """DLQ replay completed successfully."""

    DLQ_REPLAY_FAILED = "selfhealing.dlq.replay_failed"
    """DLQ replay failed."""

    DLQ_REPLAY_ABORTED = "selfhealing.dlq.replay_aborted"
    """DLQ replay aborted (max attempts exceeded or policy block)."""

    DLQ_EXPIRED = "selfhealing.dlq.expired"
    """DLQ entry expired without resolution."""

    # =========================================================================
    # Rate Limit Events
    # =========================================================================

    RATE_LIMIT_TRIGGERED = "selfhealing.rate_limit.triggered"
    """Rate limit threshold reached."""

    RATE_LIMIT_CASCADE_DETECTED = "selfhealing.rate_limit.cascade_detected"
    """Rate limit cascade (429 storm) detected."""

    SELF_DDOS_DETECTED = "selfhealing.rate_limit.self_ddos_detected"
    """Self-DDoS pattern detected (retry amplification)."""

    # =========================================================================
    # SLO Events
    # =========================================================================

    SLO_THRESHOLD_APPROACHING = "selfhealing.slo.threshold_approaching"
    """SLO threshold approaching (warning level)."""

    SLO_BREACHED = "selfhealing.slo.breached"
    """SLO threshold breached."""

    SLO_RECOVERED = "selfhealing.slo.recovered"
    """SLO recovered to healthy state."""

    # =========================================================================
    # Error Budget Events
    # =========================================================================

    ERROR_BUDGET_LOW = "selfhealing.error_budget.low"
    """Error budget remaining is below warning threshold (< 50%)."""

    ERROR_BUDGET_CRITICAL = "selfhealing.error_budget.critical"
    """Error budget remaining is below critical threshold (< 20%)."""

    ERROR_BUDGET_EXHAUSTED = "selfhealing.error_budget.exhausted"
    """Error budget is exhausted (0% or negative)."""

    ERROR_BUDGET_RECOVERED = "selfhealing.error_budget.recovered"
    """Error budget has recovered to healthy levels."""

    BURN_RATE_FAST = "selfhealing.error_budget.burn_rate_fast"
    """Fast burn rate detected (1-hour window)."""

    BURN_RATE_SLOW = "selfhealing.error_budget.burn_rate_slow"
    """Slow burn rate detected (6-hour window)."""

    # =========================================================================
    # Deployment Freeze Events
    # =========================================================================

    DEPLOYMENT_FREEZE_RECOMMENDED = "selfhealing.deployment.freeze_recommended"
    """Deployment freeze is recommended due to low error budget."""

    DEPLOYMENT_FREEZE_ACKNOWLEDGED = "selfhealing.deployment.freeze_acknowledged"
    """Operator has acknowledged and confirmed the deployment freeze."""

    DEPLOYMENT_OVERRIDE_APPROVED = "selfhealing.deployment.override_approved"
    """Operator has approved an override to bypass the freeze."""

    DEPLOYMENT_FREEZE_LIFTED = "selfhealing.deployment.freeze_lifted"
    """Deployment freeze has been lifted."""

    # =========================================================================
    # Policy/Decision Events
    # =========================================================================

    POLICY_EVALUATED = "selfhealing.policy.evaluated"
    """Policy evaluation completed."""

    POLICY_AUTO_HEAL_ALLOWED = "selfhealing.policy.auto_heal_allowed"
    """Automatic healing action permitted by policy."""

    POLICY_AUTO_HEAL_BLOCKED = "selfhealing.policy.auto_heal_blocked"
    """Automatic healing action blocked by policy."""

    MANUAL_INTERVENTION_REQUIRED = "selfhealing.policy.manual_intervention_required"
    """Manual intervention flagged as required."""

    DECISION_CYCLE_STARTED = "selfhealing.decision.cycle_started"
    """Decision evaluation cycle started."""

    DECISION_CYCLE_COMPLETED = "selfhealing.decision.cycle_completed"
    """Decision evaluation cycle completed."""


# =============================================================================
# Event Attribute Keys (Standardized)
# =============================================================================


class EventAttribute:
    """
    Standardized attribute keys for self-healing events.

    Using consistent attribute names enables:
    - Uniform querying across APM platforms
    - Consistent dashboard creation
    - Predictable alert definitions
    """

    # Identity
    SERVICE_NAME = "selfhealing.service_name"
    DOMAIN = "selfhealing.domain"
    ENVIRONMENT = "selfhealing.environment"

    # Circuit Breaker
    CB_STATE_FROM = "selfhealing.circuit_breaker.state_from"
    CB_STATE_TO = "selfhealing.circuit_breaker.state_to"
    CB_REASON = "selfhealing.circuit_breaker.reason"
    CB_MANUALLY_CONTROLLED = "selfhealing.circuit_breaker.manually_controlled"
    CB_FAILURE_COUNT = "selfhealing.circuit_breaker.failure_count"
    CB_SUCCESS_COUNT = "selfhealing.circuit_breaker.success_count"

    # Retry
    RETRY_ATTEMPT_NUMBER = "selfhealing.retry.attempt_number"
    RETRY_MAX_ATTEMPTS = "selfhealing.retry.max_attempts"
    RETRY_BACKOFF_SECONDS = "selfhealing.retry.backoff_seconds"
    RETRY_ERROR_TYPE = "selfhealing.retry.error_type"

    # DLQ
    DLQ_ID = "selfhealing.dlq.id"
    DLQ_FAILURE_TYPE = "selfhealing.dlq.failure_type"
    DLQ_REPLAY_TYPE = "selfhealing.dlq.replay_type"
    DLQ_REPLAY_ATTEMPT = "selfhealing.dlq.replay_attempt"

    # Rate Limit
    RATE_LIMIT_TYPE = "selfhealing.rate_limit.type"
    RATE_LIMIT_CURRENT = "selfhealing.rate_limit.current_count"
    RATE_LIMIT_THRESHOLD = "selfhealing.rate_limit.threshold"
    RATE_LIMIT_WINDOW_SECONDS = "selfhealing.rate_limit.window_seconds"

    # SLO
    SLO_NAME = "selfhealing.slo.name"
    SLO_CURRENT_VALUE = "selfhealing.slo.current_value"
    SLO_THRESHOLD_VALUE = "selfhealing.slo.threshold_value"
    SLO_THRESHOLD_PERCENTAGE = "selfhealing.slo.threshold_percentage"

    # Error Budget
    ERROR_BUDGET_REMAINING_PERCENT = "selfhealing.error_budget.remaining_percent"
    ERROR_BUDGET_REMAINING_MINUTES = "selfhealing.error_budget.remaining_minutes"
    ERROR_BUDGET_CONSUMED_PERCENT = "selfhealing.error_budget.consumed_percent"
    BURN_RATE_1H = "selfhealing.error_budget.burn_rate_1h"
    BURN_RATE_6H = "selfhealing.error_budget.burn_rate_6h"

    # Deployment Freeze
    FREEZE_STATUS = "selfhealing.deployment.freeze_status"
    FREEZE_DECISION_TYPE = "selfhealing.deployment.decision_type"
    FREEZE_DECIDED_BY = "selfhealing.deployment.decided_by"
    FREEZE_JUSTIFICATION = "selfhealing.deployment.justification"
    FREEZE_OVERRIDE_TYPE = "selfhealing.deployment.override_type"
    DEPLOYMENT_ID = "selfhealing.deployment.id"
    DEPLOYMENT_NAME = "selfhealing.deployment.name"

    # Policy
    POLICY_NAME = "selfhealing.policy.name"
    POLICY_OUTCOME = "selfhealing.policy.outcome"
    POLICY_IS_AUTOMATIC = "selfhealing.policy.is_automatic"

    # Decision
    DECISION_TYPE = "selfhealing.decision.type"
    DECISION_OUTCOME = "selfhealing.decision.outcome"
    DECISION_DURATION_MS = "selfhealing.decision.duration_ms"

    # Operator
    OPERATOR_ID = "selfhealing.operator.id"
    OPERATOR_ACTION = "selfhealing.operator.action"

    # Timing
    TIMESTAMP = "selfhealing.timestamp"


# =============================================================================
# Event Builder
# =============================================================================


def build_event_attributes(
    event_type: SelfHealingEventType,
    service_name: str,
    environment: str,
    domain: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Build standardized event attributes.

    Args:
        event_type: Type of self-healing event
        service_name: Service identifier
        environment: Deployment environment
        domain: Business domain (optional)
        **kwargs: Additional event-specific attributes

    Returns:
        Dictionary of OTel-compatible attributes
    """
    attributes = {
        EventAttribute.SERVICE_NAME: service_name,
        EventAttribute.ENVIRONMENT: environment,
        EventAttribute.TIMESTAMP: datetime.utcnow().isoformat() + "Z",
    }

    if domain:
        attributes[EventAttribute.DOMAIN] = domain

    # Add event-specific attributes with sanitization
    for key, value in kwargs.items():
        if value is not None:
            # Convert to OTel-compatible types
            if isinstance(value, Enum):
                attributes[key] = value.value
            elif isinstance(value, datetime):
                attributes[key] = value.isoformat() + "Z"
            elif isinstance(value, (str, int, float, bool)):
                attributes[key] = value
            elif isinstance(value, (list, tuple)):
                # OTel supports homogeneous arrays
                attributes[key] = list(value)
            else:
                # Convert complex types to string
                attributes[key] = str(value)

    return attributes


# =============================================================================
# Convenience Event Emission (uses global adapter)
# =============================================================================


def emit_selfhealing_event(
    event_type: SelfHealingEventType,
    attributes: Optional[Dict[str, Any]] = None,
    domain: Optional[str] = None,
) -> None:
    """
    Emit a self-healing event using the global adapter.

    This is a convenience function for emitting events without
    direct adapter access. If no adapter is configured or enabled,
    this is a NO-OP.

    Args:
        event_type: Type of self-healing event
        attributes: Event-specific attributes
        domain: Business domain (optional)

    Example:
        emit_selfhealing_event(
            SelfHealingEventType.CIRCUIT_BREAKER_OPENED,
            attributes={
                EventAttribute.CB_STATE_FROM: "closed",
                EventAttribute.CB_STATE_TO: "open",
                EventAttribute.CB_REASON: "failure_threshold_exceeded",
            },
            domain="payment",
        )
    """
    from selfhealing.adapters.observability.opentelemetry.adapter import (
        get_opentelemetry_adapter,
    )

    adapter = get_opentelemetry_adapter()
    adapter.emit_event(
        event_type=event_type.value if isinstance(event_type, Enum) else event_type,
        attributes=attributes,
        domain=domain,
    )
