"""
Circuit Breaker for Audit Backends.

Provides CircuitBreakerBase (shared state machine) and CircuitBreaker
(sync implementation for external audit backends).

Import order note:
    CircuitBreakerBase is defined before importing CircuitState from
    graceful_degradation.enums to avoid circular import. With
    `from __future__ import annotations`, type annotations are strings
    and method bodies are not executed at class definition time.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass
class AuditCircuitBreakerConfig:
    """Configuration for audit circuit breaker."""

    failure_threshold: int = 3  # Failures before opening
    success_threshold: int = 2  # Successes to close from half-open
    timeout_seconds: float = 30.0  # Time before trying half-open
    call_timeout_seconds: float = 5.0  # Timeout for individual calls


class CircuitBreakerBase(ABC):
    """Audit Circuit Breaker shared state machine.

    Design principles:
    - State transition logic (_*_impl) and concurrency control (lock) are separated
      so that async subclasses can reuse logic by swapping only the lock.
    - Timeout calculation uses time.monotonic() uniformly (immune to NTP/leap seconds).
    - datetime is used only for observation/logging/stats.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int,
        success_threshold: int,
        timeout_seconds: float,
    ):
        self._name = name
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._total_requests = 0
        self._total_failures = 0
        self._total_successes = 0
        self._state_changes = 0

        self._failure_threshold = failure_threshold
        self._success_threshold = success_threshold
        self._timeout_seconds = timeout_seconds

        # DR-1: Monotonic time for duration calculation (NTP/leap-second immune)
        self._last_failure_mono: float = 0.0
        # Observation/logging absolute time (used only in stats API and log output)
        self._last_failure_time: datetime | None = None

    @property
    def name(self) -> str:
        return self._name

    # --- State transition logic (lock-free pure logic) ---

    def _can_execute_impl(self) -> bool:
        self._total_requests += 1
        self._check_timeout_impl()

        if self._state == CircuitState.CLOSED:
            return True

        if self._state == CircuitState.HALF_OPEN:
            return self._can_attempt_half_open()

        # OPEN
        return False

    def _record_success_impl(self) -> None:
        self._total_successes += 1

        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self._success_threshold:
                self._transition_to(CircuitState.CLOSED)
        elif self._state == CircuitState.CLOSED:
            self._failure_count = 0

    def _record_failure_impl(self) -> None:
        self._failure_count += 1
        self._total_failures += 1
        self._last_failure_mono = time.monotonic()
        self._last_failure_time = datetime.now(timezone.utc)

        if self._state == CircuitState.HALF_OPEN:
            self._transition_to(CircuitState.OPEN)
            self._on_open()
        elif self._state == CircuitState.CLOSED:
            if self._failure_count >= self._failure_threshold:
                self._transition_to(CircuitState.OPEN)
                self._on_open()

    def _check_timeout_impl(self) -> None:
        if self._state != CircuitState.OPEN:
            return
        if self._last_failure_mono == 0.0:
            return
        if self._get_elapsed_seconds() >= self._timeout_seconds:
            self._transition_to(CircuitState.HALF_OPEN)

    # --- Subclass required: concurrency control wrapping ---

    @abstractmethod
    def can_execute(self) -> bool: ...

    @abstractmethod
    def record_success(self) -> None: ...

    @abstractmethod
    def record_failure(self, error: Exception | None = None) -> None: ...

    # --- Common internal methods ---

    def _get_elapsed_seconds(self) -> float:
        """DR-1: Concrete method — time.monotonic() based, not abstract."""
        return time.monotonic() - self._last_failure_mono

    def _transition_to(self, new_state: CircuitState) -> None:
        old_state = self._state
        self._state = new_state
        self._state_changes += 1

        if new_state == CircuitState.CLOSED:
            self._failure_count = 0
            self._success_count = 0
        elif new_state == CircuitState.HALF_OPEN:
            self._success_count = 0

        logger.warning(
            "circuitbreaker_state_transition",
            name=self._name,
            old_state=old_state.value,
            new_state=new_state.value,
        )

        # DR-6: Observability hook on state transition
        self._on_state_changed(old_state, new_state)

    def force_open(self) -> None:
        """Force circuit to OPEN state."""
        ...  # Subclass provides lock wrapper

    def reset(self) -> None:
        """Reset circuit breaker to CLOSED state."""
        ...

    def get_stats(self) -> dict[str, Any]:
        """Get circuit breaker statistics (base fields)."""
        return {
            "name": self._name,
            "state": self._state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "total_requests": self._total_requests,
            "total_failures": self._total_failures,
            "total_successes": self._total_successes,
            "state_changes": self._state_changes,
            "last_failure_time": (
                self._last_failure_time.isoformat() if self._last_failure_time else None
            ),
        }

    # --- Subclass hooks (optional override) ---

    def _on_open(self) -> None:
        """Called on OPEN transition (subclass override)."""

    def _on_close(self) -> None:
        """Called on CLOSED transition (subclass override)."""

    def _can_attempt_half_open(self) -> bool:
        """Whether to allow requests in HALF_OPEN (subclass override)."""
        return True  # Default: unlimited

    def _on_state_changed(self, old: CircuitState, new: CircuitState) -> None:
        """DR-6: State transition metrics hook (subclass override).
        Default is no-op. Subclass integrates with AuditMetrics."""


# ---------------------------------------------------------------------------
# CircuitState import — placed after CircuitBreakerBase to break circular
# import chain (resilience.circuit_breaker → graceful_degradation.enums →
# graceful_degradation.__init__ → graceful_degradation.circuit_breaker →
# resilience.circuit_breaker.CircuitBreakerBase).
# By this point CircuitBreakerBase is already defined and importable.
# ---------------------------------------------------------------------------
from selfhealing.audit.graceful_degradation.enums import (
    CircuitState,  # noqa: E402, F401
)


@dataclass
class CircuitBreakerSnapshot:
    """Snapshot of a circuit breaker's current state."""

    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: datetime | None = None
    last_state_change: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    total_failures: int = 0
    total_successes: int = 0


class CircuitBreaker(CircuitBreakerBase):
    """Circuit Breaker for external audit backends (Sync)."""

    def __init__(
        self,
        name: str,
        config: AuditCircuitBreakerConfig | None = None,
    ):
        cfg = config or AuditCircuitBreakerConfig()
        super().__init__(
            name=name,
            failure_threshold=cfg.failure_threshold,
            success_threshold=cfg.success_threshold,
            timeout_seconds=cfg.timeout_seconds,
        )
        self.config = cfg
        # DR-5: Sync-only lock
        self._lock = threading.RLock()

        # Backward-compatible snapshot for last_state_change
        self._last_state_change = datetime.now(timezone.utc)

    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        with self._lock:
            self._check_timeout_impl()
            return self._state

    # DR-5: Concurrency control — threading.RLock wrapping
    def can_execute(self) -> bool:
        with self._lock:
            return self._can_execute_impl()

    def record_success(self) -> None:
        with self._lock:
            self._record_success_impl()

    def record_failure(self, error: Exception | None = None) -> None:
        with self._lock:
            self._record_failure_impl()

    def reset(self) -> None:
        with self._lock:
            self._transition_to(CircuitState.CLOSED)
            logger.info(
                "circuitbreaker_manually_reset",
                name=self._name,
            )

    def force_open(self) -> None:
        with self._lock:
            self._transition_to(CircuitState.OPEN)
            logger.warning(
                "circuitbreaker_manually_opened",
                name=self._name,
            )

    def _transition_to(self, new_state: CircuitState) -> None:
        super()._transition_to(new_state)
        self._last_state_change = datetime.now(timezone.utc)

    # DR-6: Observability hook — AuditMetrics integration
    def _on_state_changed(self, old: CircuitState, new: CircuitState) -> None:
        from .metrics import AuditMetrics

        AuditMetrics.get_instance().set_circuit_state(self._name, new.value)

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            stats = super().get_stats()
            stats["last_state_change"] = self._last_state_change.isoformat()
            stats["config"] = {
                "failure_threshold": self.config.failure_threshold,
                "success_threshold": self.config.success_threshold,
                "timeout_seconds": self.config.timeout_seconds,
                "call_timeout_seconds": self.config.call_timeout_seconds,
            }
            return stats


class CircuitBreakerRegistry:
    """Registry for managing multiple circuit breakers."""

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
        config: AuditCircuitBreakerConfig | None = None,
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
    "AuditCircuitBreakerConfig",
    "CircuitBreakerSnapshot",
    "CircuitBreakerBase",
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "get_circuit_breaker",
]
