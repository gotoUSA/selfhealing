"""
Django ORM Repository Adapters

Concrete implementations of repository interfaces using Django ORM.
These adapters translate between the abstract interface methods and
Django model operations.

Design:
- Each adapter wraps a Django model
- Methods return data classes, not Django model instances
- All database operations are encapsulated here

Reference: docs/SELF_HEALING_EXTRACTION_PLAN.md Phase 1.2

NOTE: This file re-exports classes from the django adapter module
for backward compatibility.
"""

from selfhealing.adapters.django.repositories import (
    DjangoFailedOperationRepository,
    DjangoCircuitBreakerStateRepository,
    DjangoSecurityIncidentRepository,
)

__all__ = [
    "DjangoFailedOperationRepository",
    "DjangoCircuitBreakerStateRepository",
    "DjangoSecurityIncidentRepository",
]
