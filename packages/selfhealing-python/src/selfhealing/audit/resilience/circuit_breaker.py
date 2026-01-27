"""
Circuit Breaker for Audit Backends.

Prevents slow/failing external services from blocking the main application.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject all calls
    HALF_OPEN = "half_open"  # Testing if backend recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""

    failure_threshold: int = 3  # Failures before opening
    success_threshold: int = 2  # Successes to close from half-open
    timeout_seconds: float = 30.0  # Time before trying half-open
    call_timeout_seconds: float = 5.0  # Timeout for individual calls


@dataclass
class CircuitBreakerState:
    """Current state of a circuit breaker."""

    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: datetime | None = None
    last_state_change: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    total_failures: int = 0
    total_successes: int = 0


class CircuitBreaker:
    """
    Circuit Breaker for audit backends.

    Prevents slow/failing external services from blocking the main application.

    States:
    - CLOSED: Normal operation, calls go through
    - OPEN: Backend is failing, calls are rejected immediately
    - HALF_OPEN: Testing if backend recovered with limited calls

    Usage:
        cb = CircuitBreaker("cloudwatch")

        if cb.can_execute():
            try:
                result = external_call()
                cb.record_success()
            except Exception:
                cb.record_failure()
        else:
            # Use fallback
            local_log(entry)
    """

    def __init__(
        self,
        name: str,
        config: CircuitBreakerConfig | None = None,
    ):
        """
        Initialize circuit breaker.

        Args:
            name: Name of the protected resource
            config: Circuit breaker configuration
        """
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitBreakerState()
        self._lock = threading.RLock()

    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        with self._lock:
            self._check_timeout()
            return self._state.state

    def can_execute(self) -> bool:
        """
        Check if a call can be executed.

        Returns:
            True if call should proceed, False if circuit is open
        """
        with self._lock:
            self._check_timeout()

            if self._state.state == CircuitState.CLOSED:
                return True
            elif self._state.state == CircuitState.HALF_OPEN:
                # Allow limited calls in half-open
                return True
            else:  # OPEN
                return False

    def record_success(self) -> None:
        """Record a successful call."""
        with self._lock:
            self._state.total_successes += 1

            if self._state.state == CircuitState.HALF_OPEN:
                self._state.success_count += 1
                if self._state.success_count >= self.config.success_threshold:
                    self._transition_to(CircuitState.CLOSED)
            elif self._state.state == CircuitState.CLOSED:
                # Reset failure count on success
                self._state.failure_count = 0

    def record_failure(self) -> None:
        """Record a failed call."""
        with self._lock:
            self._state.failure_count += 1
            self._state.total_failures += 1
            self._state.last_failure_time = datetime.now(timezone.utc)

            if self._state.state == CircuitState.HALF_OPEN:
                # Any failure in half-open reopens circuit
                self._transition_to(CircuitState.OPEN)
            elif self._state.state == CircuitState.CLOSED:
                if self._state.failure_count >= self.config.failure_threshold:
                    self._transition_to(CircuitState.OPEN)

    def _check_timeout(self) -> None:
        """Check if open circuit should transition to half-open."""
        if self._state.state == CircuitState.OPEN:
            time_since_change = (
                datetime.now(timezone.utc) - self._state.last_state_change
            ).total_seconds()

            if time_since_change >= self.config.timeout_seconds:
                self._transition_to(CircuitState.HALF_OPEN)

    def _transition_to(self, new_state: CircuitState) -> None:
        """Transition to a new state."""
        old_state = self._state.state
        self._state.state = new_state
        self._state.last_state_change = datetime.now(timezone.utc)

        if new_state == CircuitState.CLOSED:
            self._state.failure_count = 0
            self._state.success_count = 0
        elif new_state == CircuitState.HALF_OPEN:
            self._state.success_count = 0

        logger.warning(
            f"[CircuitBreaker:{self.name}] State transition: {old_state.value} -> {new_state.value}"
        )

    def reset(self) -> None:
        """Manually reset circuit breaker to closed state."""
        with self._lock:
            self._transition_to(CircuitState.CLOSED)
            logger.info(f"[CircuitBreaker:{self.name}] Manually reset")

    def force_open(self) -> None:
        """Manually open circuit breaker."""
        with self._lock:
            self._transition_to(CircuitState.OPEN)
            logger.warning(f"[CircuitBreaker:{self.name}] Manually opened")

    def get_stats(self) -> dict[str, Any]:
        """Get circuit breaker statistics."""
        with self._lock:
            return {
                "name": self.name,
                "state": self._state.state.value,
                "failure_count": self._state.failure_count,
                "success_count": self._state.success_count,
                "total_failures": self._state.total_failures,
                "total_successes": self._state.total_successes,
                "last_failure_time": (
                    self._state.last_failure_time.isoformat()
                    if self._state.last_failure_time
                    else None
                ),
                "last_state_change": self._state.last_state_change.isoformat(),
                "config": {
                    "failure_threshold": self.config.failure_threshold,
                    "success_threshold": self.config.success_threshold,
                    "timeout_seconds": self.config.timeout_seconds,
                },
            }


class CircuitBreakerRegistry:
    """
    Registry for managing multiple circuit breakers.

    Provides centralized access to all audit backend circuit breakers.
    """

    _instance: CircuitBreakerRegistry | None = None
    _lock = threading.Lock()

    def __init__(self):
        self._breakers: dict[str, CircuitBreaker] = {}
        self._registry_lock = threading.RLock()

    @classmethod
    def get_instance(cls) -> CircuitBreakerRegistry:
        """Get singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def get_or_create(
        self,
        name: str,
        config: CircuitBreakerConfig | None = None,
    ) -> CircuitBreaker:
        """Get existing or create new circuit breaker."""
        with self._registry_lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(name, config)
            return self._breakers[name]

    def get(self, name: str) -> CircuitBreaker | None:
        """Get circuit breaker by name."""
        with self._registry_lock:
            return self._breakers.get(name)

    def get_all_stats(self) -> dict[str, dict[str, Any]]:
        """Get statistics for all circuit breakers."""
        with self._registry_lock:
            return {name: cb.get_stats() for name, cb in self._breakers.items()}

    def reset_all(self) -> None:
        """Reset all circuit breakers."""
        with self._registry_lock:
            for cb in self._breakers.values():
                cb.reset()

    def get_open_circuits(self) -> list[str]:
        """Get names of all open circuits."""
        with self._registry_lock:
            return [
                name
                for name, cb in self._breakers.items()
                if cb.state == CircuitState.OPEN
            ]


def get_circuit_breaker(name: str) -> CircuitBreaker:
    """Get or create a circuit breaker by name."""
    return CircuitBreakerRegistry.get_instance().get_or_create(name)


__all__ = [
    "CircuitState",
    "CircuitBreakerConfig",
    "CircuitBreakerState",
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "get_circuit_breaker",
]
