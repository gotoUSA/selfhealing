"""
Self-Healing Adapters Module

Concrete implementations of pluggable interfaces.
These adapters bridge the abstract interfaces with specific frameworks,
services, and libraries.

Available Adapters:
    Repository Adapters:
        - DjangoFailedOperationRepository
        - DjangoCircuitBreakerStateRepository
        - DjangoSecurityIncidentRepository

    Payment Adapters:
        - TossPaymentAdapter (Toss Payments - Korean PG)
        - MockPaymentAdapter (Testing)

    Cache Adapters:
        - RedisCacheAdapter (Redis)
        - InMemoryCacheAdapter (Testing)

    Task Queue Adapters:
        - CeleryTaskAdapter (Celery)
        - SyncTaskAdapter (Testing - synchronous execution)

Usage:
    from selfhealing.adapters import (
        # Repositories
        DjangoFailedOperationRepository,
        DjangoCircuitBreakerStateRepository,
        DjangoSecurityIncidentRepository,
        # Payments
        TossPaymentAdapter,
        MockPaymentAdapter,
        # Cache
        RedisCacheAdapter,
        InMemoryCacheAdapter,
        # Task Queues
        CeleryTaskAdapter,
        SyncTaskAdapter,
    )

Reference: docs/PLUGGABLE_ARCHITECTURE.md
"""

# =============================================================================
# Repository Adapters (Phase 0)
# =============================================================================
from selfhealing.adapters.django_repositories import (
    DjangoFailedOperationRepository,
    DjangoCircuitBreakerStateRepository,
    DjangoSecurityIncidentRepository,
)

# =============================================================================
# Payment Adapters (Phase 2)
# =============================================================================
from selfhealing.adapters.payments import (
    TossPaymentAdapter,
    MockPaymentAdapter,
)

# =============================================================================
# Cache Adapters (Phase 2)
# =============================================================================
from selfhealing.adapters.cache import (
    RedisCacheAdapter,
    InMemoryCacheAdapter,
)

# =============================================================================
# Task Queue Adapters (Phase 2)
# =============================================================================
from selfhealing.adapters.queues import (
    CeleryTaskAdapter,
    SyncTaskAdapter,
)


__all__ = [
    # =========================================================================
    # Repository Adapters
    # =========================================================================
    "DjangoFailedOperationRepository",
    "DjangoCircuitBreakerStateRepository",
    "DjangoSecurityIncidentRepository",
    # =========================================================================
    # Payment Adapters
    # =========================================================================
    "TossPaymentAdapter",
    "MockPaymentAdapter",
    # =========================================================================
    # Cache Adapters
    # =========================================================================
    "RedisCacheAdapter",
    "InMemoryCacheAdapter",
    # =========================================================================
    # Task Queue Adapters
    # =========================================================================
    "CeleryTaskAdapter",
    "SyncTaskAdapter",
]
