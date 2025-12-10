"""
Django adapter for the self-healing system.

This module provides Django-specific implementations including:
- Django ORM repository implementations
- Django models
- Django admin integration
- Django REST Framework views and serializers

Usage:
    After Django is fully configured, import directly:

    from selfhealing.adapters.django.models import (
        FailedOperation,
        CircuitBreakerState,
        SecurityIncident,
    )
    from selfhealing.adapters.django.repositories import (
        DjangoFailedOperationRepository,
        DjangoCircuitBreakerStateRepository,
        DjangoSecurityIncidentRepository,
    )

Note:
    Do NOT import models at module level in Django apps.
    This avoids AppRegistryNotReady errors during Django setup.
"""

# Django requires apps to be fully loaded before models can be imported.
# To avoid AppRegistryNotReady errors, we use lazy imports.
# Users should import directly from submodules:
#   from selfhealing.adapters.django.models import FailedOperation
#   from selfhealing.adapters.django.repositories import DjangoFailedOperationRepository

__all__ = [
    # Models (lazy)
    "FailedOperation",
    "CircuitBreakerState",
    "SecurityIncident",
    # Repositories (lazy)
    "DjangoFailedOperationRepository",
    "DjangoCircuitBreakerStateRepository",
    "DjangoSecurityIncidentRepository",
]


def __getattr__(name: str):
    """Lazy import to avoid AppRegistryNotReady errors."""
    if name in ("FailedOperation", "CircuitBreakerState", "SecurityIncident"):
        from selfhealing.adapters.django import models

        return getattr(models, name)
    elif name in (
        "DjangoFailedOperationRepository",
        "DjangoCircuitBreakerStateRepository",
        "DjangoSecurityIncidentRepository",
    ):
        from selfhealing.adapters.django import repositories

        return getattr(repositories, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
