"""
Adapters for different frameworks and storage backends.

This module contains framework-specific implementations of the
repository interfaces and integration adapters.

Available Adapters:
- django: Django ORM models, repositories, and admin
- celery: Celery tasks for background processing
- cache: Cache provider adapters (Redis, In-Memory)
- queues: Task queue adapters (Celery, Sync)
- payments: Payment provider adapters (Mock, Toss, Stripe)

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

    # Cache adapters
    from selfhealing.adapters.cache import (
        RedisCacheAdapter,
        InMemoryCacheAdapter,
    )

    # Task queue adapters
    from selfhealing.adapters.queues import (
        CeleryTaskAdapter,
        SyncTaskAdapter,
    )

    # Payment adapters
    from selfhealing.adapters.payments import (
        MockPaymentAdapter,
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
#   from selfhealing.adapters.cache import ...
#   from selfhealing.adapters.queues import ...
#   from selfhealing.adapters.payments import ...

__all__: list[str] = []


def __getattr__(name: str):
    """Lazy import of adapter submodules."""
    if name == "django":
        from selfhealing.adapters import django as _django

        return _django
    elif name == "celery":
        from selfhealing.adapters import celery as _celery

        return _celery
    elif name == "cache":
        from selfhealing.adapters import cache as _cache

        return _cache
    elif name == "queues":
        from selfhealing.adapters import queues as _queues

        return _queues
    elif name == "payments":
        from selfhealing.adapters import payments as _payments

        return _payments
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
