"""
Circuit Breaker for Hash Chain Operations.

Prevents cascading failures by stopping requests to a failing
Redis instance and allowing recovery time.
"""

from __future__ import annotations

import structlog
import threading
import time
from typing import TYPE_CHECKING, Any

from .enums import HashChainCircuitBreakerConfig, CircuitState

if TYPE_CHECKING:
    from .degradation_manager import HashChainDegradationManager


logger = structlog.get_logger()


class HashChainCircuitBreaker:
    """
    Circuit breaker for hash chain Redis operations.

    Prevents cascading failures by stopping requests to a failing
    Redis instance and allowing recovery time.

    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Failures exceeded threshold, requests rejected
    - HALF_OPEN: Testing if Redis has recovered

    Pattern source:
        services/circuit_breaker/service.py

    Usage:
        cb = HashChainCircuitBreaker()

        if cb.can_execute():
            try:
                result = redis_operation()
                cb.record_success()
            except Exception as e:
                cb.record_failure()
                raise
        else:
            # Use fallback
            result = fallback_operation()
    """

    def __init__(
        self,
        name: str = "hash_chain_redis",
        config: HashChainCircuitBreakerConfig | None = None,
        degradation_manager: HashChainDegradationManager | None = None,
    ):
        """
        Initialize circuit breaker.

        Args:
            name: Circuit breaker name
            config: Configuration
            degradation_manager: Optional degradation manager for integration
        """
        self._name = name
        self._config = config or HashChainCircuitBreakerConfig()
        self._degradation_manager = degradation_manager
        self._lock = threading.RLock()

        # State
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float | None = None
        self._half_open_requests = 0

        # Stats
        self._total_requests = 0
        self._total_failures = 0
        self._total_successes = 0
        self._state_changes = 0

    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._state

    def can_execute(self) -> bool:
        """
        Check if request can be executed.

        Returns:
            True if circuit allows execution
        """
        with self._lock:
            self._total_requests += 1
            self._maybe_transition_to_half_open()

            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_requests < self._config.half_open_requests:
                    self._half_open_requests += 1
                    return True
                return False

            # OPEN
            return False

    def record_success(self) -> None:
        """Record successful operation."""
        with self._lock:
            self._total_successes += 1

            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self._config.success_threshold:
                    self._transition_to_closed()
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0  # Reset on success

    def record_failure(self, error: Exception | None = None) -> None:
        """Record failed operation."""
        with self._lock:
            self._failure_count += 1
            self._total_failures += 1
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                self._transition_to_open()
            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self._config.failure_threshold:
                    self._transition_to_open()

            # Notify degradation manager
            if self._degradation_manager and self._state == CircuitState.OPEN:
                self._degradation_manager.on_redis_failure(error)

    def _maybe_transition_to_half_open(self) -> None:
        """Transition from OPEN to HALF_OPEN if timeout expired."""
        if self._state != CircuitState.OPEN:
            return

        if self._last_failure_time is None:
            return

        elapsed = time.monotonic() - self._last_failure_time
        if elapsed >= self._config.recovery_timeout_seconds:
            self._state = CircuitState.HALF_OPEN
            self._half_open_requests = 0
            self._success_count = 0
            self._state_changes += 1
            logger.info(
                "circuitbreaker_open",
                self=self._name,
            )

    def _transition_to_open(self) -> None:
        """Transition to OPEN state."""
        self._state = CircuitState.OPEN
        self._state_changes += 1
        logger.warning(
            "circuitbreaker_open_failures",
            self=self._name,
            self_1=self._failure_count,
        )

    def _transition_to_closed(self) -> None:
        """Transition to CLOSED state."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_requests = 0
        self._state_changes += 1
        logger.info(
            "circuitbreaker_closed_recovered",
            self=self._name,
        )

        # Notify degradation manager
        if self._degradation_manager:
            self._degradation_manager.on_redis_recovery()

    def force_open(self) -> None:
        """Force circuit to OPEN state (for testing/manual intervention)."""
        with self._lock:
            self._transition_to_open()

    def force_closed(self) -> None:
        """Force circuit to CLOSED state (for testing/manual intervention)."""
        with self._lock:
            self._transition_to_closed()

    def get_stats(self) -> dict[str, Any]:
        """Get circuit breaker statistics."""
        with self._lock:
            return {
                "name": self._name,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "total_requests": self._total_requests,
                "total_failures": self._total_failures,
                "total_successes": self._total_successes,
                "state_changes": self._state_changes,
                "config": {
                    "failure_threshold": self._config.failure_threshold,
                    "recovery_timeout_seconds": self._config.recovery_timeout_seconds,
                    "half_open_requests": self._config.half_open_requests,
                    "success_threshold": self._config.success_threshold,
                },
            }


__all__ = ["HashChainCircuitBreaker"]
