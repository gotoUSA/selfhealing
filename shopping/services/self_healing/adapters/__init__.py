"""
Self-Healing Adapters Module

Concrete implementations of repository interfaces.
These adapters bridge the abstract interfaces with specific frameworks.

Available Adapters:
- Django ORM adapters (django_repositories.py)

Usage:
    from shopping.services.self_healing.adapters import (
        DjangoFailedOperationRepository,
        DjangoCircuitBreakerStateRepository,
        DjangoSecurityIncidentRepository,
    )
"""

from shopping.services.self_healing.adapters.django_repositories import (
    DjangoFailedOperationRepository,
    DjangoCircuitBreakerStateRepository,
    DjangoSecurityIncidentRepository,
)

__all__ = [
    "DjangoFailedOperationRepository",
    "DjangoCircuitBreakerStateRepository",
    "DjangoSecurityIncidentRepository",
]
