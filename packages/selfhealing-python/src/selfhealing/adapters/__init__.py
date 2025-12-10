"""
Adapters for different frameworks and storage backends.

This module contains framework-specific implementations of the
repository interfaces and integration adapters.

Available Adapters:
- django: Django ORM models, repositories, and admin
- celery: Celery tasks for background processing

Usage:
    # Django adapter (import directly from submodule)
    from selfhealing.adapters.django import (
        FailedOperation,
        CircuitBreakerState,
        SecurityIncident,
        DjangoFailedOperationRepository,
        DjangoCircuitBreakerStateRepository,
        DjangoSecurityIncidentRepository,
    )

    # Celery adapter (import directly from submodule)
    from selfhealing.adapters.celery import (
        check_circuit_breaker_recovery,
        replay_single_dlq_entry,
        CELERY_BEAT_SCHEDULE,
    )

Note:
    Adapters are NOT imported at package level to avoid framework
    initialization issues. Import directly from submodules when needed.
"""

# Adapters require framework initialization before import.
# Do NOT import django or celery submodules here to avoid:
# - Django AppRegistryNotReady errors
# - Celery app configuration issues
#
# Users should import directly from submodules:
#   from selfhealing.adapters.django import ...
#   from selfhealing.adapters.celery import ...

__all__: list[str] = []


def __getattr__(name: str):
    """Lazy import of adapter submodules."""
    if name == "django":
        from selfhealing.adapters import django as _django

        return _django
    elif name == "celery":
        from selfhealing.adapters import celery as _celery

        return _celery
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
