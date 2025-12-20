"""
Self-Healing Adapters Module - DEPRECATED

⚠️  DEPRECATION NOTICE:
    This module is deprecated and will be removed in a future version.
    Please migrate to the selfhealing package:

    Before (deprecated):
        from shopping.services.self_healing.adapters import (
            DjangoFailedOperationRepository,
            DjangoCircuitBreakerStateRepository,
        )

    After (recommended):
        from selfhealing.adapters.django import (
            DjangoFailedOperationRepository,
            DjangoCircuitBreakerStateRepository,
        )

Migration Guide: packages/selfhealing-python/docs/MIGRATION.md
"""

import warnings

# Show deprecation warning on module import
warnings.warn(
    "Importing from 'shopping.services.self_healing.adapters' is deprecated. "
    "Please migrate to 'selfhealing.adapters.django' for repositories. "
    "See packages/selfhealing-python/docs/MIGRATION.md for details.",
    DeprecationWarning,
    stacklevel=2,
)

# =============================================================================
# Repository Adapters - Re-export from selfhealing package
# =============================================================================
from selfhealing.adapters.django.repositories import (
    DjangoFailedOperationRepository,
    DjangoCircuitBreakerStateRepository,
    DjangoSecurityIncidentRepository,
)

# =============================================================================
# Payment Adapters (shopping-specific, not in selfhealing package)
# These are shopping domain adapters, not general selfhealing infrastructure
# =============================================================================
from shopping.services.self_healing.adapters.payments import (
    TossPaymentAdapter,
    MockPaymentAdapter,
)

# =============================================================================
# Cache Adapters - Re-export from selfhealing package
# =============================================================================
try:
    from selfhealing.adapters.cache import (
        RedisCacheAdapter,
        InMemoryCacheAdapter,
    )
except ImportError:
    # Fallback to local implementation if not available in selfhealing
    from shopping.services.self_healing.adapters.cache import (
        RedisCacheAdapter,
        InMemoryCacheAdapter,
    )

# =============================================================================
# Task Queue Adapters - Re-export from selfhealing package
# =============================================================================
try:
    from selfhealing.adapters.celery import CeleryTaskAdapter
    from selfhealing.adapters.memory import SyncTaskAdapter
except ImportError:
    # Fallback to local implementation if not available in selfhealing
    from shopping.services.self_healing.adapters.queues import (
        CeleryTaskAdapter,
        SyncTaskAdapter,
    )


__all__ = [
    # Repository Adapters (from selfhealing package)
    "DjangoFailedOperationRepository",
    "DjangoCircuitBreakerStateRepository",
    "DjangoSecurityIncidentRepository",
    # Payment Adapters (shopping-specific)
    "TossPaymentAdapter",
    "MockPaymentAdapter",
    # Cache Adapters
    "RedisCacheAdapter",
    "InMemoryCacheAdapter",
    # Task Queue Adapters
    "CeleryTaskAdapter",
    "SyncTaskAdapter",
]
