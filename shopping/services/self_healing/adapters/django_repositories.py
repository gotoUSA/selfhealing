"""
Django ORM Repository Adapters - DEPRECATED

⚠️  DEPRECATION NOTICE:
    This module is deprecated and will be removed in a future version.
    Please migrate to the selfhealing package:
    
    Before (deprecated):
        from shopping.services.self_healing.adapters.django_repositories import (
            DjangoFailedOperationRepository,
        )
    
    After (recommended):
        from selfhealing.adapters.django import (
            DjangoFailedOperationRepository,
        )

Migration Guide: packages/selfhealing-python/docs/MIGRATION.md
"""

import warnings

warnings.warn(
    "Importing from 'shopping.services.self_healing.adapters.django_repositories' "
    "is deprecated. Please migrate to 'selfhealing.adapters.django.repositories'. "
    "See packages/selfhealing-python/docs/MIGRATION.md for details.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export everything from the selfhealing package
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
