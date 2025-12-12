"""
Circuit Breaker Convenience Functions

Module-level convenience functions for common circuit breaker operations.
These provide a simpler API for common use cases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .config import CircuitBreakerResult
from .service import CircuitBreakerService

if TYPE_CHECKING:
    pass


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
    controlled_by: Any = None,
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
    controlled_by: Any = None,
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


def record_rate_limit(service_name: str) -> CircuitBreakerResult | None:
    """
    Convenience function to record a 429 rate limit response.

    Call this when receiving a 429 response from an external service.
    If a rate limit cascade is detected, the circuit breaker will auto-open.

    Args:
        service_name: Name of the external service

    Returns:
        CircuitBreakerResult if circuit was opened, None otherwise
    """
    return get_circuit_breaker_service().record_rate_limit_response(service_name)


def should_allow_with_protection(service_name: str) -> tuple[bool, float]:
    """
    Convenience function to check if request should be allowed with self-DDoS protection.

    Args:
        service_name: Name of the external service

    Returns:
        Tuple of (should_allow, suggested_backoff_seconds)
    """
    return get_circuit_breaker_service().should_allow_with_ddos_protection(service_name)


def get_protection_status(service_name: str) -> dict[str, Any]:
    """
    Convenience function to get comprehensive protection status.

    Args:
        service_name: Name of the external service

    Returns:
        Dictionary with protection status details
    """
    return get_circuit_breaker_service().get_protection_status(service_name)
