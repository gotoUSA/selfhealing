"""
Django ORM Repository Adapters

Concrete implementations of repository interfaces using Django ORM.
These adapters translate between the abstract interface methods and
Django model operations.

Structure:
    - failed_operation.py: DjangoFailedOperationRepository
    - circuit_breaker.py: DjangoCircuitBreakerStateRepository
    - security_incident.py: DjangoSecurityIncidentRepository
    - rate_limit.py: DjangoRateLimitStateRepository

Reference: docs/SELF_HEALING_EXTRACTION_PLAN.md Phase 1.2
"""

from selfhealing.adapters.django_repos.failed_operation import (
    DjangoFailedOperationRepository,
)
from selfhealing.adapters.django_repos.circuit_breaker import (
    DjangoCircuitBreakerStateRepository,
)
from selfhealing.adapters.django_repos.security_incident import (
    DjangoSecurityIncidentRepository,
)
from selfhealing.adapters.django_repos.rate_limit import (
    DjangoRateLimitStateRepository,
)

__all__ = [
    "DjangoFailedOperationRepository",
    "DjangoCircuitBreakerStateRepository",
    "DjangoSecurityIncidentRepository",
    "DjangoRateLimitStateRepository",
]
