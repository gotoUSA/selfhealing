"""Self-Healing library exception hierarchy.

All library exceptions inherit from SelfHealingError.
Callers can use ``except SelfHealingError`` to catch any library error.
"""

from __future__ import annotations

from typing import Any


class SelfHealingError(Exception):
    """Base exception for all self-healing library errors."""

    def __init__(self, message: str = "", *, code: str = ""):
        super().__init__(message)
        self.code = code

    def extra_context(self) -> dict[str, Any]:
        """Return structlog-bindable context. Override in subclasses."""
        return {"error_code": self.code} if self.code else {}


# ── Adapter errors ───────────────────────────────────────────


class AdapterError(SelfHealingError):
    """Base exception for adapter-related errors."""

    pass


class AdapterNotFoundError(AdapterError):
    """Raised when a requested adapter is not registered in ProviderRegistry."""

    pass


class AdapterInitializationError(AdapterError):
    """Raised when an adapter fails to initialize."""

    pass


class AdapterConnectionError(AdapterError):
    """Raised when an external system connection fails."""

    pass


# ── Circuit Breaker errors ───────────────────────────────────


class CircuitBreakerError(SelfHealingError):
    """Base exception for circuit breaker errors."""

    pass


class CircuitBreakerTransitionError(CircuitBreakerError):
    """Raised when a circuit breaker state transition fails."""

    pass


# ── DLQ errors ───────────────────────────────────────────────


class DLQError(SelfHealingError):
    """Base exception for DLQ (Dead Letter Queue) errors."""

    pass


class DLQEntryNotFoundError(DLQError):
    """Raised when a DLQ entry is not found."""

    pass


class DLQReplayError(DLQError):
    """Raised when a DLQ replay operation fails."""

    pass


# ── Resilience errors ────────────────────────────────────────


class ResilienceError(SelfHealingError):
    """Base exception for resilience pattern errors (bulkhead, hedging, retry)."""

    pass


class RetryExhaustedError(ResilienceError):
    """Raised when all retry attempts are exhausted."""

    pass


# ── IPC errors ───────────────────────────────────────────────
# Note: IPCError hierarchy lives in adapters/ipc/exceptions.py
# and will be migrated to inherit from SelfHealingError separately.


# ── Runbook errors ───────────────────────────────────────────


class RunbookError(SelfHealingError):
    """Base exception for runbook errors."""

    pass


# ── Configuration errors ─────────────────────────────────────


class ConfigurationError(SelfHealingError):
    """Base exception for configuration and settings errors."""

    pass


class SettingsValidationError(ConfigurationError):
    """Raised when settings validation fails."""

    pass
