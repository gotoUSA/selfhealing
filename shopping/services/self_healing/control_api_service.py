"""
Self-Healing Control API Service

Provides the core business logic for the Self-Healing Control API.
Based on: docs/self_healing/5_CONTROL_API/

Reference documents:
- CONTROL_API_INTERFACE.md: API contract
- CONTROL_API_EXECUTION.md: Execution behavior
- CONTROL_API_SECURITY_GOVERNANCE.md: Authorization & risk
- CONTROL_API_TEST_REQUIREMENTS.md: Test specifications
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from shopping.serializers.self_healing_serializers import (
    ControlAPIActions,
    ControlAPIEnvironments,
    RiskLevels,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Reason Classification
# =============================================================================


class ReasonClassification(str, Enum):
    """AI/system assigned reason classifications."""

    EXTERNAL_DEPENDENCY_FAILURE = "external-dependency-failure"
    INTERNAL_SERVICE_ERROR = "internal-service-error"
    MAINTENANCE_WINDOW = "maintenance-window"
    SLA_BREACH_MITIGATION = "sla-breach-mitigation"
    CHAOS_EXPERIMENT = "chaos-experiment"
    MANUAL_INTERVENTION = "manual-intervention"
    RECOVERY_PROCEDURE = "recovery-procedure"
    SECURITY_INCIDENT = "security-incident"
    UNKNOWN = "unknown"


def classify_reason(reason: str) -> str:
    """
    Classify the provided reason string.

    Args:
        reason: Human-provided reason text

    Returns:
        Classification string
    """
    reason_lower = reason.lower()

    # Pattern matching for classification (order matters - more specific first)
    patterns = [
        (ReasonClassification.MAINTENANCE_WINDOW, ["maintenance", "scheduled", "upgrade", "deploy"]),
        (ReasonClassification.SLA_BREACH_MITIGATION, ["sla", "breach", "violation", "threshold"]),
        (ReasonClassification.CHAOS_EXPERIMENT, ["chaos", "experiment", "resilience"]),
        (ReasonClassification.RECOVERY_PROCEDURE, ["recovery", "recovered", "restored", "fixed"]),
        (ReasonClassification.SECURITY_INCIDENT, ["security", "attack", "ddos", "vulnerability"]),
        (
            ReasonClassification.EXTERNAL_DEPENDENCY_FAILURE,
            ["external", "pg", "payment gateway", "api down", "timeout", "latency"],
        ),
        (ReasonClassification.INTERNAL_SERVICE_ERROR, ["internal", "service", "error", "bug"]),
    ]

    for classification, keywords in patterns:
        if any(kw in reason_lower for kw in keywords):
            return classification.value

    return ReasonClassification.MANUAL_INTERVENTION.value


# =============================================================================
# Risk Assessment
# =============================================================================


def assess_risk_level(action: str, environment: str) -> str:
    """
    Assess the risk level for an action in an environment.

    Based on: CONTROL_API_SECURITY_GOVERNANCE.md §4 (Risk Classification)

    Args:
        action: Action type
        environment: Environment type

    Returns:
        Risk level string
    """
    risk_matrix = {
        (ControlAPIActions.ALLOW, ControlAPIEnvironments.TEST): RiskLevels.INFO,
        (ControlAPIActions.ALLOW, ControlAPIEnvironments.CHAOS): RiskLevels.INFO,
        (ControlAPIActions.ALLOW, ControlAPIEnvironments.OPS): RiskLevels.WARNING,
        (ControlAPIActions.BLOCK, ControlAPIEnvironments.TEST): RiskLevels.INFO,
        (ControlAPIActions.BLOCK, ControlAPIEnvironments.CHAOS): RiskLevels.WARNING,
        (ControlAPIActions.BLOCK, ControlAPIEnvironments.OPS): RiskLevels.HIGH,
        (ControlAPIActions.OVERRIDE, ControlAPIEnvironments.TEST): RiskLevels.WARNING,
        (ControlAPIActions.OVERRIDE, ControlAPIEnvironments.CHAOS): RiskLevels.HIGH,
        (ControlAPIActions.OVERRIDE, ControlAPIEnvironments.OPS): RiskLevels.CRITICAL,
        (ControlAPIActions.RESET, ControlAPIEnvironments.TEST): RiskLevels.INFO,
        (ControlAPIActions.RESET, ControlAPIEnvironments.CHAOS): RiskLevels.WARNING,
        (ControlAPIActions.RESET, ControlAPIEnvironments.OPS): RiskLevels.WARNING,
        (ControlAPIActions.INJECT_FAILURE, ControlAPIEnvironments.TEST): RiskLevels.INFO,
        (ControlAPIActions.INJECT_FAILURE, ControlAPIEnvironments.CHAOS): RiskLevels.HIGH,
        (ControlAPIActions.INJECT_FAILURE, ControlAPIEnvironments.OPS): RiskLevels.FORBIDDEN,
        (ControlAPIActions.INJECT_SUCCESS, ControlAPIEnvironments.TEST): RiskLevels.INFO,
        (ControlAPIActions.INJECT_SUCCESS, ControlAPIEnvironments.CHAOS): RiskLevels.INFO,
        (ControlAPIActions.INJECT_SUCCESS, ControlAPIEnvironments.OPS): RiskLevels.FORBIDDEN,
    }

    return risk_matrix.get((action, environment), RiskLevels.WARNING)


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class ControlRequest:
    """Internal representation of a control API request."""

    service_name: str
    action: str
    reason: str
    environment: str
    ttl_minutes: int | None = None
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: dict = field(default_factory=dict)
    actor: str = "system"
    actor_role: str = "automation"


@dataclass
class ControlResponse:
    """Internal representation of a control API response."""

    status: str
    action_applied: str
    system_state: str = ""
    effective_until: str | None = None
    reason_classification: str = ""
    evidence: dict = field(default_factory=dict)
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    error_code: str = ""
    error_message: str = ""
    risk_level: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        result = {
            "status": self.status,
            "action_applied": self.action_applied,
            "correlation_id": self.correlation_id,
        }

        if self.system_state:
            result["system_state"] = self.system_state
        if self.effective_until:
            result["effective_until"] = self.effective_until
        if self.reason_classification:
            result["reason_classification"] = self.reason_classification
        if self.evidence:
            result["evidence"] = self.evidence
        if self.error_code:
            result["error_code"] = self.error_code
        if self.error_message:
            result["error_message"] = self.error_message
        if self.risk_level:
            result["risk_level"] = self.risk_level

        return result


# =============================================================================
# Control API Service
# =============================================================================


class ControlAPIService:
    """
    Self-Healing Control API Service.

    Provides a unified, auditable, reversible, and governed control surface
    to manage reliability behaviors across testing, chaos experimentation,
    and real production operations.

    Usage:
        service = ControlAPIService()

        # Execute control action
        response = service.execute(ControlRequest(
            service_name="payment",
            action="allow",
            reason="PG recovered",
            environment="ops"
        ))

        # Get current status
        status = service.get_status(environment="ops")

        # Get audit logs
        logs = service.get_audit_logs(service_name="payment")
    """

    def __init__(self):
        """Initialize the Control API Service."""
        from shopping.services.self_healing.circuit_breaker_service import CircuitBreakerService
        from shopping.services.self_healing.replay_service import ReplayService

        self.circuit_breaker = CircuitBreakerService()
        self.replay_service = ReplayService()

        # Failure injection state (in-memory for chaos/test)
        self._failure_injections: dict[str, dict] = {}

    # =========================================================================
    # Main Execution
    # =========================================================================

    def execute(self, request: ControlRequest) -> ControlResponse:
        """
        Execute a control API action.

        Based on: CONTROL_API_EXECUTION.md §2 (Execution Lifecycle)

        Args:
            request: Control request

        Returns:
            ControlResponse with outcome
        """
        # 1. Pre-execution validation
        validation_error = self._validate_request(request)
        if validation_error:
            return validation_error

        # 2. Assess risk
        risk_level = assess_risk_level(request.action, request.environment)

        # 3. Execute action
        try:
            if request.action == ControlAPIActions.ALLOW:
                response = self._execute_allow(request)
            elif request.action == ControlAPIActions.BLOCK:
                response = self._execute_block(request)
            elif request.action == ControlAPIActions.OVERRIDE:
                response = self._execute_override(request)
            elif request.action == ControlAPIActions.RESET:
                response = self._execute_reset(request)
            elif request.action == ControlAPIActions.INJECT_FAILURE:
                response = self._execute_inject_failure(request)
            elif request.action == ControlAPIActions.INJECT_SUCCESS:
                response = self._execute_inject_success(request)
            else:
                response = ControlResponse(
                    status="error",
                    action_applied=request.action,
                    error_code="UNKNOWN_ACTION",
                    error_message=f"Unknown action: {request.action}",
                )
        except Exception as e:
            logger.exception(f"[ControlAPI] Error executing action: {e}")
            response = ControlResponse(
                status="error", action_applied=request.action, error_code="EXECUTION_ERROR", error_message=str(e)
            )

        # 4. Add metadata
        response.reason_classification = classify_reason(request.reason)
        response.risk_level = risk_level
        response.correlation_id = request.request_id

        # 5. Record audit (best-effort)
        self._record_audit(request, response)

        return response

    # =========================================================================
    # Action Implementations
    # =========================================================================

    def _execute_allow(self, request: ControlRequest) -> ControlResponse:
        """
        Execute allow action - enable service operations.

        Maps to: Circuit Breaker → CLOSED state
        """
        result = self.circuit_breaker.force_close(
            service_name=request.service_name,
            reason=request.reason,
            controlled_by=None,  # Will be set from request.actor
            trigger_replay=request.metadata.get("trigger_replay", False),
        )

        if result.success:
            return ControlResponse(
                status="success",
                action_applied="allow",
                system_state="allow",
                evidence=self._gather_evidence(request.service_name),
            )
        else:
            return ControlResponse(
                status="error",
                action_applied="allow",
                error_code="CIRCUIT_BREAKER_ERROR",
                error_message=result.error or "Failed to close circuit breaker",
            )

    def _execute_block(self, request: ControlRequest) -> ControlResponse:
        """
        Execute block action - disable service operations.

        Maps to: Circuit Breaker → OPEN state
        """
        result = self.circuit_breaker.force_open(service_name=request.service_name, reason=request.reason, controlled_by=None)

        # Calculate effective_until
        effective_until = None
        if request.ttl_minutes:
            effective_until = (timezone.now() + timedelta(minutes=request.ttl_minutes)).isoformat()
        elif request.environment == ControlAPIEnvironments.OPS:
            # Default 90 minutes in ops
            effective_until = (timezone.now() + timedelta(minutes=90)).isoformat()

        if result.success:
            return ControlResponse(
                status="success",
                action_applied="block",
                system_state="block",
                effective_until=effective_until,
                evidence=self._gather_evidence(request.service_name),
            )
        else:
            return ControlResponse(
                status="error",
                action_applied="block",
                error_code="CIRCUIT_BREAKER_ERROR",
                error_message=result.error or "Failed to open circuit breaker",
            )

    def _execute_override(self, request: ControlRequest) -> ControlResponse:
        """
        Execute override action - temporarily bypass rules.

        Allows operations even when normal rules would block them.
        """
        # For override, we force close (allow) with a TTL
        result = self.circuit_breaker.force_close(
            service_name=request.service_name, reason=f"OVERRIDE: {request.reason}", controlled_by=None
        )

        effective_until = None
        if request.ttl_minutes:
            effective_until = (timezone.now() + timedelta(minutes=request.ttl_minutes)).isoformat()

        if result.success:
            return ControlResponse(
                status="success",
                action_applied="override",
                system_state="allow",
                effective_until=effective_until,
                evidence=self._gather_evidence(request.service_name),
            )
        else:
            return ControlResponse(
                status="error",
                action_applied="override",
                error_code="OVERRIDE_ERROR",
                error_message=result.error or "Failed to apply override",
            )

    def _execute_reset(self, request: ControlRequest) -> ControlResponse:
        """
        Execute reset action - revert to default configuration.

        Clears all manual overrides and returns to policy defaults.
        """
        try:
            result = self.circuit_breaker.reset_to_default(request.service_name)

            return ControlResponse(
                status="success",
                action_applied="reset",
                system_state="allow",  # Default state is allow
                evidence=self._gather_evidence(request.service_name),
            )
        except AttributeError:
            # Fallback if reset_to_default doesn't exist
            result = self.circuit_breaker.force_close(
                service_name=request.service_name, reason=f"RESET: {request.reason}", controlled_by=None
            )

            return ControlResponse(
                status="success",
                action_applied="reset",
                system_state="allow",
                evidence=self._gather_evidence(request.service_name),
            )

    def _execute_inject_failure(self, request: ControlRequest) -> ControlResponse:
        """
        Execute inject_failure action - simulate failures.

        Only allowed in test and chaos environments.

        Supports two modes:
        1. Configuration mode: Sets up failure injection config for future requests
        2. Trigger CB mode: Immediately records N failures to trigger Circuit Breaker
           - Use metadata: {"trigger_cb_failures": 5} to record 5 failures immediately
           - This will naturally open the CB without setting manually_controlled=True
        """
        # Check for immediate CB trigger mode
        trigger_cb_failures = request.metadata.get("trigger_cb_failures", 0)

        if trigger_cb_failures > 0:
            # Import here to avoid circular dependency
            from shopping.services.self_healing.circuit_breaker_service import (
                get_circuit_breaker_service,
            )

            cb_service = get_circuit_breaker_service()

            # Record failures to naturally trigger CB OPEN
            for i in range(trigger_cb_failures):
                cb_service.record_failure(request.service_name)

            # Get the resulting state
            state = cb_service.get_or_create_state(request.service_name)

            logger.info(
                f"[ControlAPI] Triggered {trigger_cb_failures} failures for '{request.service_name}': "
                f"state={state.state}, failure_count={state.failure_count}"
            )

            return ControlResponse(
                status="success",
                action_applied="inject_failure",
                system_state="block" if state.state == "open" else "allow",
                evidence={
                    "failures_triggered": trigger_cb_failures,
                    "cb_state": state.state,
                    "failure_count": state.failure_count,
                    "manually_controlled": state.manually_controlled,
                },
            )

        # Original configuration mode: Store failure injection config
        failure_config = {
            "enabled": True,
            "failure_rate": request.metadata.get("failure_rate", 1.0),
            "simulate_latency_ms": request.metadata.get("simulate_latency_ms", 0),
            "failure_type": request.metadata.get("failure_type", "exception"),
            "expires_at": None,
        }

        if request.ttl_minutes:
            failure_config["expires_at"] = timezone.now() + timedelta(minutes=request.ttl_minutes)

        self._failure_injections[request.service_name] = failure_config

        logger.info(
            f"[ControlAPI] Failure injection enabled for '{request.service_name}': "
            f"rate={failure_config['failure_rate']}, type={failure_config['failure_type']}"
        )

        effective_until = None
        if failure_config["expires_at"]:
            effective_until = failure_config["expires_at"].isoformat()

        return ControlResponse(
            status="success",
            action_applied="inject_failure",
            system_state="block",  # Failures being injected
            effective_until=effective_until,
            evidence={"failure_rate": failure_config["failure_rate"], "failure_type": failure_config["failure_type"]},
        )

    def _execute_inject_success(self, request: ControlRequest) -> ControlResponse:
        """
        Execute inject_success action - simulate successful requests.

        Only allowed in test environments.

        Used to transition Circuit Breaker from HALF_OPEN to CLOSED state.
        Use metadata: {"success_count": 2} to record 2 successes immediately.
        This will naturally close the CB when success_threshold is met.
        """
        success_count = request.metadata.get("success_count", 1)

        # Import here to avoid circular dependency
        from shopping.services.self_healing.circuit_breaker_service import (
            get_circuit_breaker_service,
        )

        cb_service = get_circuit_breaker_service()

        # Get initial state
        initial_state = cb_service.get_or_create_state(request.service_name)
        initial_state_name = initial_state.state

        # Record successes to naturally trigger CB CLOSED (from HALF_OPEN)
        for i in range(success_count):
            cb_service.record_success(request.service_name)

        # Get the resulting state
        final_state = cb_service.get_or_create_state(request.service_name)

        logger.info(
            f"[ControlAPI] Triggered {success_count} successes for '{request.service_name}': "
            f"state={initial_state_name} -> {final_state.state}, "
            f"success_count={final_state.success_count}"
        )

        return ControlResponse(
            status="success",
            action_applied="inject_success",
            system_state="allow" if final_state.state == "closed" else "block",
            evidence={
                "successes_triggered": success_count,
                "initial_state": initial_state_name,
                "final_state": final_state.state,
                "success_count": final_state.success_count,
                "manually_controlled": final_state.manually_controlled,
            },
        )

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _validate_request(self, request: ControlRequest) -> ControlResponse | None:
        """
        Validate the request before execution.

        Returns ControlResponse with error if validation fails, None if valid.
        """
        # inject_failure forbidden in ops
        if request.action == ControlAPIActions.INJECT_FAILURE:
            if request.environment == ControlAPIEnvironments.OPS:
                return ControlResponse(
                    status="rejected",
                    action_applied=request.action,
                    error_code="ACTION_FORBIDDEN_IN_ENVIRONMENT",
                    error_message="inject_failure is forbidden in ops environment",
                )

        # inject_success forbidden in ops (test only)
        if request.action == ControlAPIActions.INJECT_SUCCESS:
            if request.environment == ControlAPIEnvironments.OPS:
                return ControlResponse(
                    status="rejected",
                    action_applied=request.action,
                    error_code="ACTION_FORBIDDEN_IN_ENVIRONMENT",
                    error_message="inject_success is forbidden in ops environment",
                )

        # override in ops requires TTL (max 60)
        if request.action == ControlAPIActions.OVERRIDE:
            if request.environment == ControlAPIEnvironments.OPS:
                if not request.ttl_minutes:
                    return ControlResponse(
                        status="rejected",
                        action_applied=request.action,
                        error_code="TTL_REQUIRED_FOR_OPS_OVERRIDE",
                        error_message="TTL is required for override action in ops environment",
                    )
                if request.ttl_minutes > 60:
                    return ControlResponse(
                        status="rejected",
                        action_applied=request.action,
                        error_code="TTL_EXCEEDS_OPS_LIMIT",
                        error_message=f"TTL cannot exceed 60 minutes in ops (got: {request.ttl_minutes})",
                    )

        return None

    def _gather_evidence(self, service_name: str) -> dict:
        """
        Gather evidence metrics for a service.

        Returns dict with recent metrics.
        """
        try:
            state = self.circuit_breaker.get_or_create_state(service_name)
            return {
                "failure_count": state.failure_count,
                "success_count": state.success_count,
                "last_failure_at": state.last_failure_at.isoformat() if state.last_failure_at else None,
            }
        except Exception as e:
            logger.warning(f"[ControlAPI] Failed to gather evidence: {e}")
            return {}

    def _record_audit(self, request: ControlRequest, response: ControlResponse):
        """
        Record the action in audit log.

        Best-effort - never blocks the response.
        """
        try:
            logger.info(
                f"[ControlAPI][AUDIT] "
                f"action={request.action} "
                f"service={request.service_name} "
                f"env={request.environment} "
                f"status={response.status} "
                f"actor={request.actor} "
                f"risk={response.risk_level} "
                f"reason='{request.reason}'"
            )
        except Exception as e:
            logger.warning(f"[ControlAPI] Failed to record audit: {e}")

    # =========================================================================
    # Query Methods
    # =========================================================================

    def get_status(self, environment: str = "ops") -> dict:
        """
        Get the current status of all services.

        Args:
            environment: Current environment context

        Returns:
            Status dictionary with all service states
        """
        states = self.circuit_breaker.get_all_states()

        return {"services": states, "environment": environment, "timestamp": timezone.now().isoformat()}

    def get_service_status(self, service_name: str) -> dict:
        """
        Get the status of a specific service.

        This also triggers should_allow() check which handles
        automatic OPEN → HALF_OPEN transition after recovery_timeout.

        Args:
            service_name: Service to check

        Returns:
            Service state dictionary
        """
        # First, trigger should_allow() which handles automatic HALF_OPEN transition
        # This is important for testing - checking status can trigger state transitions
        is_allowed = self.circuit_breaker.should_allow(service_name)

        # Now get the (potentially updated) state
        state = self.circuit_breaker.get_or_create_state(service_name)

        return {
            "service_name": service_name,
            "state": state.state,
            "failure_count": state.failure_count,
            "success_count": state.success_count,
            "last_failure_at": state.last_failure_at,
            "manually_controlled": state.manually_controlled,
            "control_reason": state.control_reason,
            "is_allowed": is_allowed,  # Whether requests are currently allowed
        }

    def is_failure_injection_active(self, service_name: str) -> bool:
        """
        Check if failure injection is active for a service.

        Args:
            service_name: Service to check

        Returns:
            True if failures should be injected
        """
        config = self._failure_injections.get(service_name)
        if not config or not config.get("enabled"):
            return False

        # Check expiration
        if config.get("expires_at") and timezone.now() > config["expires_at"]:
            del self._failure_injections[service_name]
            return False

        return True

    def get_failure_injection_config(self, service_name: str) -> dict | None:
        """
        Get failure injection configuration for a service.

        Args:
            service_name: Service to check

        Returns:
            Configuration dict or None
        """
        if not self.is_failure_injection_active(service_name):
            return None
        return self._failure_injections.get(service_name)

    def get_metrics(self) -> dict:
        """
        Collect comprehensive self-healing metrics for trend analysis.

        Returns operational metrics for dashboards, AI agents, and monitoring.
        Unlike status (point-in-time snapshot), metrics provide trend data.

        **Consumers:**
        - Admin UI: Dashboard visualization
        - AI Agent: Automated decision making
        - Prometheus/Grafana: Metrics scraping
        - External Monitoring: Alerting integration

        Returns:
            Dictionary with comprehensive metrics data
        """
        import time

        start_time = time.time()

        from django.db.models import Avg, Count, Q
        from django.utils import timezone
        from shopping.models.failed_operation import FailedOperation
        from shopping.models.failed_payment import CircuitBreakerState
        from shopping.services.self_healing.metrics import (
            DOMAINS,
            update_dlq_pending_gauges,
            update_retry_success_rates,
        )

        now = timezone.now()
        five_min_ago = now - timedelta(minutes=5)
        twenty_four_h_ago = now - timedelta(hours=24)

        # Collect DLQ pending counts
        dlq_pending = update_dlq_pending_gauges()
        total_dlq_pending = sum(dlq_pending.values())

        # Collect retry success rates
        retry_rates = update_retry_success_rates()

        # Get circuit breaker states
        cb_states = {}
        try:
            for cb in CircuitBreakerState.objects.all():
                cb_states[cb.service_name] = cb.state
        except Exception:
            pass

        # Calculate aggregate service counts
        total_services = len(set(list(dlq_pending.keys()) + list(cb_states.keys()) + DOMAINS))
        healthy_services = sum(1 for s in cb_states.values() if s == "closed")
        degraded_services = sum(1 for s in cb_states.values() if s in ("open", "half_open"))

        # Calculate 5-minute failure rate
        try:
            recent_total = FailedOperation.objects.filter(created_at__gte=five_min_ago).count()
            recent_failed = FailedOperation.objects.filter(
                created_at__gte=five_min_ago, status=FailedOperation.Status.PENDING
            ).count()
            last_5m_failure_rate = recent_failed / max(recent_total, 1)
            last_5m_request_count = recent_total
        except Exception:
            last_5m_failure_rate = 0.0
            last_5m_request_count = 0

        # Calculate average time to recovery (last 24h)
        try:
            resolved = FailedOperation.objects.filter(
                status=FailedOperation.Status.RESOLVED, updated_at__gte=twenty_four_h_ago
            ).exclude(created_at__isnull=True)

            if resolved.exists():
                # Calculate duration manually since DB may not support F() subtraction
                durations = []
                for op in resolved[:100]:  # Sample max 100
                    if op.updated_at and op.created_at:
                        durations.append((op.updated_at - op.created_at).total_seconds())
                avg_time_to_recovery = sum(durations) / len(durations) if durations else None
            else:
                avg_time_to_recovery = None
        except Exception:
            avg_time_to_recovery = None

        # Count auto-allowed/blocked in last 24h
        # Note: SelfHealingLog model is not implemented yet.
        # Audit logs are currently recorded via logger.info in _record_audit().
        # TODO: Implement persistent SelfHealingLog model for audit trail querying.
        auto_allowed = 0
        auto_blocked = 0

        # Build per-service metrics
        services_metrics = []
        for domain in DOMAINS:
            service_metric = {
                "service_name": domain,
                "failure_rate_5m": 0.0,
                "retry_success_rate": retry_rates.get(domain, 100.0),
                "dlq_count": dlq_pending.get(domain, 0),
                "circuit_state": cb_states.get(domain, "closed"),
                "avg_recovery_time_seconds": None,
            }

            # Calculate per-service 5m failure rate
            try:
                domain_total = FailedOperation.objects.filter(domain=domain, created_at__gte=five_min_ago).count()
                domain_failed = FailedOperation.objects.filter(
                    domain=domain, created_at__gte=five_min_ago, status=FailedOperation.Status.PENDING
                ).count()
                if domain_total > 0:
                    service_metric["failure_rate_5m"] = domain_failed / domain_total
            except Exception:
                pass

            services_metrics.append(service_metric)

        collection_duration_ms = int((time.time() - start_time) * 1000)

        return {
            "total_services": total_services,
            "healthy_services": healthy_services,
            "degraded_services": degraded_services,
            "last_5m_failure_rate": last_5m_failure_rate,
            "last_5m_request_count": last_5m_request_count,
            "avg_time_to_recovery": avg_time_to_recovery,
            "auto_allowed_count_24h": auto_allowed,
            "auto_blocked_count_24h": auto_blocked,
            "total_dlq_pending": total_dlq_pending,
            "dlq_by_service": dlq_pending,
            "services": services_metrics,
            "timestamp": now,
            "collection_duration_ms": collection_duration_ms,
        }


# =============================================================================
# Singleton instance
# =============================================================================


_control_api_service: ControlAPIService | None = None


def get_control_api_service() -> ControlAPIService:
    """Get or create the singleton Control API Service instance."""
    global _control_api_service
    if _control_api_service is None:
        _control_api_service = ControlAPIService()
    return _control_api_service
