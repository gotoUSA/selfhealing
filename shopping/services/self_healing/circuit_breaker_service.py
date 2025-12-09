"""
Circuit Breaker Service

Provides toggle-based circuit breaker management for external service protection.
Supports manual force open/close controls and conditional replay on recovery.

Features:
- Toggle-based circuit breaker (not automatic failure counting)
- Manual force open/close by operators
- Conditional replay trigger when circuit breaker closes
- Admin integration for operational control

Reference: docs/L3_SELF_HEALING_OPERATIONS.md §9 (Runbook: Circuit Breaker)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

if TYPE_CHECKING:
    from shopping.models.failed_payment import CircuitBreakerState
    from shopping.models.user import User

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker operations."""

    enabled: bool = False
    failure_threshold: int = 5
    recovery_timeout: int = 60  # seconds
    success_threshold: int = 2
    # Governance parameters
    manual_override_ttl_minutes: int = 90  # Default 90 min, max recommended 180
    half_open_request_limit: int = 10  # Max requests allowed in half-open state
    max_pending_duration_hours: int = 4  # SLA for pending DLQ items
    max_retry_lifetime_hours: int = 24  # Max time to attempt retries

    @classmethod
    def from_settings(cls) -> "CircuitBreakerConfig":
        """Load configuration from Django settings via centralized config."""
        from shopping.services.self_healing.config import get_circuit_breaker_settings

        cb_settings = get_circuit_breaker_settings()
        return cls(
            enabled=cb_settings.enabled,
            failure_threshold=cb_settings.failure_threshold,
            recovery_timeout=cb_settings.recovery_timeout,
            success_threshold=cb_settings.success_threshold,
            manual_override_ttl_minutes=cb_settings.manual_override_ttl_minutes,
            half_open_request_limit=cb_settings.half_open_request_limit,
            max_pending_duration_hours=cb_settings.max_pending_duration_hours,
            max_retry_lifetime_hours=cb_settings.max_retry_lifetime_hours,
        )


# =============================================================================
# Circuit Breaker State Enum
# =============================================================================


class CircuitState:
    """Circuit breaker state constants."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


# =============================================================================
# Circuit Breaker Result
# =============================================================================


@dataclass
class CircuitBreakerResult:
    """Result of a circuit breaker operation."""

    success: bool
    service_name: str
    previous_state: str = ""
    new_state: str = ""
    message: str = ""
    error: str | None = None

    @classmethod
    def succeeded(
        cls,
        service_name: str,
        previous_state: str,
        new_state: str,
        message: str = "",
    ) -> "CircuitBreakerResult":
        """Factory for successful operation."""
        return cls(
            success=True,
            service_name=service_name,
            previous_state=previous_state,
            new_state=new_state,
            message=message,
        )

    @classmethod
    def failed(cls, service_name: str, error: str) -> "CircuitBreakerResult":
        """Factory for failed operation."""
        return cls(
            success=False,
            service_name=service_name,
            error=error,
        )


# =============================================================================
# Circuit Breaker Service
# =============================================================================


class CircuitBreakerService:
    """
    Circuit Breaker Service.

    Provides management operations for circuit breaker states.
    Designed for manual (toggle-based) control by operators.

    Usage:
        service = CircuitBreakerService()

        # Force open (block requests)
        result = service.force_open(
            service_name="toss_payment",
            reason="PG maintenance window",
            controlled_by=admin_user
        )

        # Force close (allow requests)
        result = service.force_close(
            service_name="toss_payment",
            reason="PG recovered",
            controlled_by=admin_user,
            trigger_replay=True
        )

        # Check if requests should be allowed
        if service.should_allow("toss_payment"):
            # proceed with request
    """

    def __init__(self, config: CircuitBreakerConfig | None = None):
        """
        Initialize the circuit breaker service.

        Args:
            config: Optional configuration, loads from settings if None
        """
        self.config = config or CircuitBreakerConfig.from_settings()

    @property
    def is_enabled(self) -> bool:
        """Check if circuit breaker is enabled."""
        return self.config.enabled

    # =========================================================================
    # State Query Operations
    # =========================================================================

    def get_or_create_state(self, service_name: str) -> "CircuitBreakerState":
        """
        Get or create a circuit breaker state for a service.

        Args:
            service_name: Name of the external service

        Returns:
            CircuitBreakerState instance
        """
        from shopping.models.failed_payment import CircuitBreakerState

        state, _ = CircuitBreakerState.objects.get_or_create(
            service_name=service_name,
            defaults={
                "state": CircuitState.CLOSED,
                "failure_count": 0,
                "success_count": 0,
            },
        )
        return state

    def get_state(self, service_name: str) -> str:
        """
        Get the current state of a circuit breaker.

        Args:
            service_name: Name of the external service

        Returns:
            Current state (closed, open, half_open)
        """
        state = self.get_or_create_state(service_name)
        return state.state

    def should_allow(self, service_name: str) -> bool:
        """
        Check if requests should be allowed through the circuit breaker.

        Args:
            service_name: Name of the external service

        Returns:
            True if requests should be allowed, False if blocked
        """
        if not self.is_enabled:
            return True

        state = self.get_or_create_state(service_name)

        if state.state == CircuitState.CLOSED:
            return True

        if state.state == CircuitState.OPEN:
            # Check recovery timeout for automatic transition to half-open
            if state.opened_at:
                elapsed = (timezone.now() - state.opened_at).total_seconds()
                if elapsed >= self.config.recovery_timeout:
                    # Transition to half-open
                    state.state = CircuitState.HALF_OPEN
                    state.success_count = 0
                    state.save(update_fields=["state", "success_count", "updated_at"])
                    return True
            return False

        # half_open state: allow limited requests for testing
        return True

    def get_all_states(self) -> list[dict[str, Any]]:
        """
        Get all circuit breaker states.

        Returns:
            List of state dictionaries
        """
        from shopping.models.failed_payment import CircuitBreakerState

        states = CircuitBreakerState.objects.all()
        return [
            {
                "service_name": s.service_name,
                "state": s.state,
                "failure_count": s.failure_count,
                "success_count": s.success_count,
                "last_failure_at": s.last_failure_at,
                "opened_at": s.opened_at,
                "manually_controlled": s.manually_controlled,
                "controlled_by_id": s.controlled_by_id,
                "control_reason": s.control_reason,
            }
            for s in states
        ]

    # =========================================================================
    # Manual Control Operations
    # =========================================================================

    @transaction.atomic
    def force_open(
        self,
        service_name: str,
        reason: str = "",
        controlled_by: "User | None" = None,
    ) -> CircuitBreakerResult:
        """
        Force the circuit breaker to OPEN state (block all requests).

        Use this when an external service is detected as down
        and you want to stop sending requests.

        Args:
            service_name: Name of the external service
            reason: Reason for opening (for audit)
            controlled_by: User who initiated the change

        Returns:
            CircuitBreakerResult with operation outcome
        """
        from shopping.models.failed_payment import CircuitBreakerState

        try:
            state = CircuitBreakerState.objects.select_for_update().get(service_name=service_name)
            previous_state = state.state
        except CircuitBreakerState.DoesNotExist:
            # Create new state in OPEN with TTL
            from datetime import timedelta

            ttl_minutes = self.config.manual_override_ttl_minutes
            state = CircuitBreakerState.objects.create(
                service_name=service_name,
                state=CircuitState.OPEN,
                opened_at=timezone.now(),
                manually_controlled=True,
                manual_override_expires_at=timezone.now() + timedelta(minutes=ttl_minutes),
                controlled_by=controlled_by,
                control_reason=reason,
            )
            logger.info(f"[CircuitBreaker] Created and opened circuit for '{service_name}': {reason} (TTL: {ttl_minutes}m)")
            return CircuitBreakerResult.succeeded(
                service_name=service_name,
                previous_state=CircuitState.CLOSED,
                new_state=CircuitState.OPEN,
                message=f"Circuit breaker created and opened for {service_name}",
            )

        if state.state == CircuitState.OPEN:
            logger.info(f"[CircuitBreaker] Circuit '{service_name}' already open")
            return CircuitBreakerResult.succeeded(
                service_name=service_name,
                previous_state=CircuitState.OPEN,
                new_state=CircuitState.OPEN,
                message="Circuit breaker already open",
            )

        state.force_open(controlled_by=controlled_by, reason=reason)

        logger.warning(
            f"[CircuitBreaker] Force opened circuit for '{service_name}': " f"{previous_state} -> open | Reason: {reason}"
        )

        return CircuitBreakerResult.succeeded(
            service_name=service_name,
            previous_state=previous_state,
            new_state=CircuitState.OPEN,
            message=f"Circuit breaker opened for {service_name}",
        )

    @transaction.atomic
    def force_close(
        self,
        service_name: str,
        reason: str = "",
        controlled_by: "User | None" = None,
        trigger_replay: bool = False,
    ) -> CircuitBreakerResult:
        """
        Force the circuit breaker to CLOSED state (allow all requests).

        Use this when an external service has recovered
        and you want to resume normal operations.

        Args:
            service_name: Name of the external service
            reason: Reason for closing (for audit)
            controlled_by: User who initiated the change
            trigger_replay: Whether to trigger conditional replay for queued items

        Returns:
            CircuitBreakerResult with operation outcome
        """
        from shopping.models.failed_payment import CircuitBreakerState

        try:
            state = CircuitBreakerState.objects.select_for_update().get(service_name=service_name)
            previous_state = state.state
        except CircuitBreakerState.DoesNotExist:
            # Create new state in CLOSED (allow is the default state)
            state = CircuitBreakerState.objects.create(
                service_name=service_name,
                state=CircuitState.CLOSED,
                failure_count=0,
                success_count=0,
                manually_controlled=True,
                controlled_by=controlled_by,
                control_reason=reason,
            )
            logger.info(f"[CircuitBreaker] Created circuit for '{service_name}' in CLOSED state: {reason}")
            return CircuitBreakerResult.succeeded(
                service_name=service_name,
                previous_state=CircuitState.CLOSED,
                new_state=CircuitState.CLOSED,
                message=f"Circuit breaker created for {service_name} (already closed)",
            )

        if state.state == CircuitState.CLOSED:
            logger.info(f"[CircuitBreaker] Circuit '{service_name}' already closed")
            return CircuitBreakerResult.succeeded(
                service_name=service_name,
                previous_state=CircuitState.CLOSED,
                new_state=CircuitState.CLOSED,
                message="Circuit breaker already closed",
            )

        state.force_close(controlled_by=controlled_by, reason=reason)

        logger.info(
            f"[CircuitBreaker] Force closed circuit for '{service_name}': " f"{previous_state} -> closed | Reason: {reason}"
        )

        result = CircuitBreakerResult.succeeded(
            service_name=service_name,
            previous_state=previous_state,
            new_state=CircuitState.CLOSED,
            message=f"Circuit breaker closed for {service_name}",
        )

        # Trigger conditional replay if requested
        if trigger_replay:
            self._trigger_conditional_replay(service_name)

        return result

    # =========================================================================
    # Conditional Replay
    # =========================================================================

    def _trigger_conditional_replay(self, service_name: str) -> None:
        """
        Trigger conditional replay when circuit breaker closes.

        Queues a Celery task to replay DLQ entries related to the
        recovered service.

        Args:
            service_name: Name of the service that recovered
        """
        try:
            from shopping.tasks.self_healing_tasks import conditional_replay_on_circuit_close

            task = conditional_replay_on_circuit_close.delay(service_name=service_name)

            logger.info(f"[CircuitBreaker] Triggered conditional replay for '{service_name}': " f"task_id={task.id}")
        except ImportError:
            # Task not yet defined, log warning
            logger.warning(f"[CircuitBreaker] Cannot trigger replay for '{service_name}': " "Celery task not available")
        except Exception as e:
            # Non-critical error, log but don't fail
            logger.error(f"[CircuitBreaker] Failed to trigger replay for '{service_name}': {e}")

    # =========================================================================
    # Failure/Success Recording (for automatic mode)
    # =========================================================================

    def record_failure(self, service_name: str) -> None:
        """
        Record a failure for a service.

        This is used for automatic circuit breaker mode.
        If the threshold is exceeded, the circuit opens automatically.

        Args:
            service_name: Name of the external service
        """
        if not self.is_enabled:
            return

        state = self.get_or_create_state(service_name)

        # Skip if manually controlled
        if state.manually_controlled:
            logger.debug(f"[CircuitBreaker] Skipping failure recording for '{service_name}': " "manually controlled")
            return

        # Increment failure count
        state.failure_count += 1
        state.last_failure_at = timezone.now()
        state.success_count = 0  # Reset success counter

        # Check if threshold exceeded and circuit should open
        if state.failure_count >= self.config.failure_threshold and state.state == CircuitState.CLOSED:
            state.state = CircuitState.OPEN
            state.opened_at = timezone.now()
            logger.warning(f"[CircuitBreaker] Circuit auto-opened for '{service_name}' " f"(failures: {state.failure_count})")

        state.save()

    def record_success(self, service_name: str) -> None:
        """
        Record a success for a service.

        This is used for automatic circuit breaker mode.
        In half-open state, enough successes will close the circuit.

        Args:
            service_name: Name of the external service
        """
        if not self.is_enabled:
            return

        state = self.get_or_create_state(service_name)

        # Skip if manually controlled
        if state.manually_controlled:
            logger.debug(f"[CircuitBreaker] Skipping success recording for '{service_name}': " "manually controlled")
            return

        previous_state = state.state
        circuit_closed = False

        if state.state == CircuitState.HALF_OPEN:
            state.success_count += 1

            if state.success_count >= self.config.success_threshold:
                # Close the circuit
                state.state = CircuitState.CLOSED
                state.failure_count = 0
                state.success_count = 0
                state.opened_at = None
                circuit_closed = True

        elif state.state == CircuitState.CLOSED:
            # Reset failure count on success in closed state
            state.failure_count = 0

        state.save()

        if circuit_closed:
            logger.info(
                f"[CircuitBreaker] Circuit auto-closed for '{service_name}' " f"(successes: {self.config.success_threshold})"
            )
            # Trigger conditional replay on auto-close
            self._trigger_conditional_replay(service_name)

    # =========================================================================
    # Reset Operations
    # =========================================================================

    @transaction.atomic
    def reset(
        self,
        service_name: str,
        controlled_by: "User | None" = None,
        reason: str = "",
    ) -> CircuitBreakerResult:
        """
        Reset a circuit breaker to initial state.

        Clears all counters and sets state to CLOSED.

        Args:
            service_name: Name of the external service
            controlled_by: User who initiated the reset
            reason: Reason for reset (for audit)

        Returns:
            CircuitBreakerResult with operation outcome
        """
        from shopping.models.failed_payment import CircuitBreakerState

        try:
            state = CircuitBreakerState.objects.select_for_update().get(service_name=service_name)
            previous_state = state.state
        except CircuitBreakerState.DoesNotExist:
            return CircuitBreakerResult.failed(
                service_name=service_name,
                error=f"Circuit breaker for '{service_name}' does not exist",
            )

        # Reset all fields
        state.state = CircuitState.CLOSED
        state.failure_count = 0
        state.success_count = 0
        state.half_open_request_count = 0
        state.opened_at = None
        state.last_failure_at = None
        state.manually_controlled = False
        state.manual_override_expires_at = None
        state.controlled_by = controlled_by
        state.control_reason = reason
        state.save()

        logger.info(f"[CircuitBreaker] Reset circuit for '{service_name}': " f"{previous_state} -> closed | Reason: {reason}")

        return CircuitBreakerResult.succeeded(
            service_name=service_name,
            previous_state=previous_state,
            new_state=CircuitState.CLOSED,
            message=f"Circuit breaker reset for {service_name}",
        )

    # =========================================================================
    # Manual Override TTL Management
    # =========================================================================

    def check_and_expire_manual_overrides(self) -> list[str]:
        """
        Check all circuit breakers for expired manual overrides.

        Manual overrides have a TTL to prevent "forgotten" blocks.
        When expired, circuits transition from OPEN to HALF_OPEN
        for gradual recovery testing.

        Returns:
            List of service names that had their overrides expired
        """
        from shopping.models.failed_payment import CircuitBreakerState

        expired_services = []

        manual_circuits = CircuitBreakerState.objects.filter(
            manually_controlled=True,
            manual_override_expires_at__isnull=False,
            manual_override_expires_at__lte=timezone.now(),
        )

        for circuit in manual_circuits:
            previous_state = circuit.state
            circuit.expire_manual_override()

            expired_services.append(circuit.service_name)
            logger.warning(
                f"[CircuitBreaker] Manual override expired for '{circuit.service_name}': "
                f"{previous_state} -> {circuit.state}"
            )

        return expired_services

    def extend_manual_override(
        self,
        service_name: str,
        additional_minutes: int = 90,
        controlled_by: "User | None" = None,
        reason: str = "",
    ) -> CircuitBreakerResult:
        """
        Extend the TTL of an existing manual override.

        Use this when an operator needs more time to resolve an issue.

        Args:
            service_name: Name of the external service
            additional_minutes: Minutes to extend the override
            controlled_by: User who initiated the extension
            reason: Reason for extension

        Returns:
            CircuitBreakerResult with operation outcome
        """
        from datetime import timedelta

        from shopping.models.failed_payment import CircuitBreakerState

        try:
            state = CircuitBreakerState.objects.get(service_name=service_name)
        except CircuitBreakerState.DoesNotExist:
            return CircuitBreakerResult.failed(
                service_name=service_name,
                error=f"Circuit breaker for '{service_name}' does not exist",
            )

        if not state.manually_controlled:
            return CircuitBreakerResult.failed(
                service_name=service_name,
                error="Circuit is not under manual control",
            )

        # Extend TTL
        if state.manual_override_expires_at:
            state.manual_override_expires_at += timedelta(minutes=additional_minutes)
        else:
            state.manual_override_expires_at = timezone.now() + timedelta(minutes=additional_minutes)

        state.controlled_by = controlled_by
        if reason:
            state.control_reason = f"{state.control_reason} | Extended: {reason}"
        state.save()

        logger.info(f"[CircuitBreaker] Extended manual override for '{service_name}' " f"by {additional_minutes} minutes")

        return CircuitBreakerResult.succeeded(
            service_name=service_name,
            previous_state=state.state,
            new_state=state.state,
            message=f"Manual override extended by {additional_minutes} minutes",
        )


# =============================================================================
# Module-level convenience functions
# =============================================================================


_circuit_breaker_service: CircuitBreakerService | None = None


def get_circuit_breaker_service() -> CircuitBreakerService:
    """Get the singleton circuit breaker service instance."""
    global _circuit_breaker_service
    if _circuit_breaker_service is None:
        _circuit_breaker_service = CircuitBreakerService()
    return _circuit_breaker_service


def should_allow_request(service_name: str) -> bool:
    """
    Convenience function to check if requests should be allowed.

    Args:
        service_name: Name of the external service

    Returns:
        True if requests should be allowed
    """
    return get_circuit_breaker_service().should_allow(service_name)


def force_open_circuit(
    service_name: str,
    reason: str = "",
    controlled_by: "User | None" = None,
) -> CircuitBreakerResult:
    """
    Convenience function to force open a circuit breaker.

    Args:
        service_name: Name of the external service
        reason: Reason for opening
        controlled_by: User who initiated the change

    Returns:
        CircuitBreakerResult with operation outcome
    """
    return get_circuit_breaker_service().force_open(
        service_name=service_name,
        reason=reason,
        controlled_by=controlled_by,
    )


def force_close_circuit(
    service_name: str,
    reason: str = "",
    controlled_by: "User | None" = None,
    trigger_replay: bool = False,
) -> CircuitBreakerResult:
    """
    Convenience function to force close a circuit breaker.

    Args:
        service_name: Name of the external service
        reason: Reason for closing
        controlled_by: User who initiated the change
        trigger_replay: Whether to trigger conditional replay

    Returns:
        CircuitBreakerResult with operation outcome
    """
    return get_circuit_breaker_service().force_close(
        service_name=service_name,
        reason=reason,
        controlled_by=controlled_by,
        trigger_replay=trigger_replay,
    )
