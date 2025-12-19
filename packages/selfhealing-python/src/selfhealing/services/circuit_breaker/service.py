"""
Circuit Breaker Service

Provides toggle-based circuit breaker management for external service protection.
Supports manual force open/close controls and conditional replay on recovery.

Features:
- Toggle-based circuit breaker (not automatic failure counting)
- Manual force open/close by operators
- Conditional replay trigger when circuit breaker closes
- Admin integration for operational control
- Rate limit cascade detection (auto-open CB on 429 storm)
- Self-DDoS protection (prevent retry amplification)

Reference: docs/L3_SELF_HEALING_OPERATIONS.md §9 (Runbook: Circuit Breaker)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from selfhealing.core.timezone import now

from .config import CircuitBreakerConfig, CircuitBreakerResult, CircuitState
from .manual_control import ManualControlMixin
from .protection import ProtectionMixin

if TYPE_CHECKING:
    from selfhealing.interfaces.repositories import (
        CircuitBreakerStateRepository,
        CircuitBreakerStateData,
    )

logger = logging.getLogger(__name__)


class CircuitBreakerService(ProtectionMixin, ManualControlMixin):
    """
    Circuit Breaker Service.

    Provides management operations for circuit breaker states.
    Designed for manual (toggle-based) control by operators.

    Usage:
        service = CircuitBreakerService()

        # Force open (block requests)
        result = service.force_open(
            service_name="external_api",
            reason="External service maintenance",
            controlled_by=admin_user
        )

        # Force close (allow requests)
        result = service.force_close(
            service_name="external_api",
            reason="Service recovered",
            controlled_by=admin_user,
            trigger_replay=True
        )

        # Check if requests should be allowed
        if service.should_allow("external_api"):
            # proceed with request

    For testing with mock repository:
        mock_repo = Mock(spec=CircuitBreakerStateRepository)
        service = CircuitBreakerService(repository=mock_repo)
    """

    def __init__(
        self,
        config: CircuitBreakerConfig | None = None,
        repository: "CircuitBreakerStateRepository | None" = None,
    ):
        """
        Initialize the circuit breaker service.

        Args:
            config: Optional configuration, loads from settings if None
            repository: Optional repository for DI, uses Django adapter if None
        """
        self.config = config or CircuitBreakerConfig.from_settings()
        self._repository = repository

    @property
    def repository(self) -> "CircuitBreakerStateRepository":
        """Get the repository, creating default adapter if needed."""
        if self._repository is None:
            # Try to use ProviderRegistry from selfhealing package first
            try:
                from selfhealing.factory import ProviderRegistry

                self._repository = ProviderRegistry.get_circuit_breaker_repo()
            except (ImportError, ValueError):
                # Fallback to local Django adapter
                from .adapters.django_repositories import (
                    DjangoCircuitBreakerStateRepository,
                )

                self._repository = DjangoCircuitBreakerStateRepository()
        return self._repository

    @property
    def is_enabled(self) -> bool:
        """Check if circuit breaker is enabled."""
        return self.config.enabled

    # =========================================================================
    # State Query Operations
    # =========================================================================

    def get_or_create_state(self, service_name: str) -> "CircuitBreakerStateData":
        """
        Get or create a circuit breaker state for a service.

        Args:
            service_name: Name of the external service

        Returns:
            CircuitBreakerStateData instance
        """
        return self.repository.get_or_create(service_name)

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
                elapsed = (now() - state.opened_at).total_seconds()
                if elapsed >= self.config.recovery_timeout:
                    # Transition to half-open via repository
                    self.repository.update_state(
                        service_name=service_name,
                        state=CircuitState.HALF_OPEN,
                        success_count=0,
                    )
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
        states = self.repository.get_all_states()
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

        # Use repository to record failure (handles atomic update)
        updated_state = self.repository.record_failure(service_name)

        # Check if threshold exceeded and circuit should open
        if updated_state.failure_count >= self.config.failure_threshold and updated_state.state == "closed":
            # Need to open the circuit
            self.repository.update_state(
                service_name=service_name,
                state="open",
                opened_at=now(),
            )
            logger.warning(
                f"[CircuitBreaker] Circuit auto-opened for '{service_name}' " f"(failures: {updated_state.failure_count})"
            )

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

        circuit_closed = False

        if state.state == "half_open":
            # Use repository to record success
            updated_state = self.repository.record_success(service_name)

            if updated_state.success_count >= self.config.success_threshold:
                # Close the circuit - use atomic operation
                self.repository.update_state(
                    service_name=service_name,
                    state="closed",
                    failure_count=0,
                    success_count=0,
                    opened_at=None,
                )
                circuit_closed = True

        elif state.state == "closed":
            # Reset failure count on success in closed state
            self.repository.update_state(
                service_name=service_name,
                state="closed",
                failure_count=0,
            )

        if circuit_closed:
            logger.info(
                f"[CircuitBreaker] Circuit auto-closed for '{service_name}' " f"(successes: {self.config.success_threshold})"
            )
            # Trigger conditional replay on auto-close
            self._trigger_conditional_replay(service_name)
