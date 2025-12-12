"""
Singleton Service Accessors.

Provides singleton pattern for service instances with DI support.
Use these for production. For testing, use factory functions directly.
"""

from __future__ import annotations

import logging

from .service import (
    create_dlq_service,
    create_replay_service,
    create_circuit_breaker_service,
    create_security_violation_service,
)

logger = logging.getLogger(__name__)


# Service singletons - use these for production
_dlq_service_instance = None
_replay_service_instance = None
_circuit_breaker_service_instance = None
_security_violation_service_instance = None


def get_dlq_service_with_di():
    """
    Get or create the DLQ service singleton with DI.

    For production use. For testing, use create_dlq_service() directly.

    Returns:
        DLQService instance
    """
    global _dlq_service_instance
    if _dlq_service_instance is None:
        _dlq_service_instance = create_dlq_service()
    return _dlq_service_instance


def get_replay_service_with_di():
    """
    Get or create the Replay service singleton with DI.

    Returns:
        ReplayService instance
    """
    global _replay_service_instance
    if _replay_service_instance is None:
        _replay_service_instance = create_replay_service()
    return _replay_service_instance


def get_circuit_breaker_service_with_di():
    """
    Get or create the CircuitBreaker service singleton with DI.

    Returns:
        CircuitBreakerService instance
    """
    global _circuit_breaker_service_instance
    if _circuit_breaker_service_instance is None:
        _circuit_breaker_service_instance = create_circuit_breaker_service()
    return _circuit_breaker_service_instance


def get_security_violation_service_with_di():
    """
    Get or create the SecurityViolation service singleton with DI.

    Returns:
        SecurityViolationService instance
    """
    global _security_violation_service_instance
    if _security_violation_service_instance is None:
        _security_violation_service_instance = create_security_violation_service()
    return _security_violation_service_instance


def reset_service_singletons():
    """
    Reset all service singletons.

    Use this in tests to ensure clean state between test cases.
    """
    global _dlq_service_instance
    global _replay_service_instance
    global _circuit_breaker_service_instance
    global _security_violation_service_instance

    _dlq_service_instance = None
    _replay_service_instance = None
    _circuit_breaker_service_instance = None
    _security_violation_service_instance = None

    logger.debug("[Factory] Reset all service singletons")
