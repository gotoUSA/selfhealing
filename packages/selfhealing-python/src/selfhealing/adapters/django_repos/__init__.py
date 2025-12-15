"""
Django ORM Repository Adapters (Shopping App Integration)

⚠️ LEGACY/REFERENCE ADAPTER MODULE

These adapters require the shopping app models and are maintained for:
1. Backward compatibility with existing shopping app integrations
2. Reference implementation demonstrating selfhealing integration patterns
3. Integration testing with a real application

For standalone selfhealing deployments WITHOUT the shopping app, use:
    selfhealing.adapters.django.repositories

which uses selfhealing's own Django models (selfhealing.adapters.django.models).

Structure:
    - failed_operation.py: Uses shopping.models.failed_operation
    - circuit_breaker.py: Uses shopping.models.failed_payment
    - security_incident.py: Uses shopping.models.security_incident
    - rate_limit.py: Uses shopping.models.rate_limit_state

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
