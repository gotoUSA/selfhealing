"""
Self-Healing Adapters Module

Concrete implementations of pluggable interfaces.
These adapters bridge the abstract interfaces with specific frameworks,
services, and libraries.

Available Adapters:
    Repository Adapters:
        - RedisCircuitBreakerStateRepository (Production default)
        - RedisDLQRepository (Production default)
        - InMemoryFailedOperationRepository (Testing)
        - InMemoryCircuitBreakerStateRepository (Testing)

    Cache Adapters:
        - RedisCacheAdapter (Redis)
        - InMemoryCacheAdapter (Testing)

    Task Queue Adapters:
        - CeleryTaskAdapter (Celery)
        - SyncTaskAdapter (Testing - synchronous execution)

Usage:
    from selfhealing.adapters import (
        # Repositories (Production - Redis)
        RedisCircuitBreakerStateRepository,
        RedisDLQRepository,
        # Repositories (Testing - InMemory)
        InMemoryFailedOperationRepository,
        InMemoryCircuitBreakerStateRepository,
        # Cache
        RedisCacheAdapter,
        InMemoryCacheAdapter,
        # Task Queues
        CeleryTaskAdapter,
        SyncTaskAdapter,
    )

플러거블 인터페이스의 구체적 구현체 모음.

NOTE: Django and SQLAlchemy adapters have been removed in v2.0.0.
      Use Redis adapters (with ResilientStorageBackend fallback) instead.
"""

# =============================================================================
# Repository Adapters - Redis (Production)
# =============================================================================
try:
    from selfhealing.adapters.redis import (
        RedisCircuitBreakerStateRepository,
        RedisDLQRepository,
    )
except ImportError:
    RedisCircuitBreakerStateRepository = None
    RedisDLQRepository = None

# =============================================================================
# Cache Adapters
# =============================================================================
from selfhealing.adapters.cache import (
    InMemoryCacheAdapter,
    RedisCacheAdapter,
)

# =============================================================================
# Health Checker Adapters (Platinum SLA Optimization)
# =============================================================================
from selfhealing.adapters.health_checker import (
    HealthCheckStrategy,
    LinuxTCPInfoStrategy,
    PortableHealthChecker,
    TTLCacheStrategy,
)

# =============================================================================
# Repository Adapters - InMemory (Testing)
# =============================================================================
from selfhealing.adapters.memory import (
    InMemoryCircuitBreakerStateRepository,
    InMemoryFailedOperationRepository,
    InMemorySecurityIncidentRepository,
)

# =============================================================================
# Task Queue Adapters
# =============================================================================
from selfhealing.adapters.queues import (
    CeleryTaskAdapter,
    SyncTaskAdapter,
)

__all__ = [
    # =========================================================================
    # Repository Adapters - Redis (Production)
    # =========================================================================
    "RedisCircuitBreakerStateRepository",
    "RedisDLQRepository",
    # =========================================================================
    # Repository Adapters - InMemory (Testing)
    # =========================================================================
    "InMemoryFailedOperationRepository",
    "InMemoryCircuitBreakerStateRepository",
    "InMemorySecurityIncidentRepository",
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
    # =========================================================================
    # Health Checker Adapters (Platinum SLA Optimization)
    # =========================================================================
    "HealthCheckStrategy",
    "TTLCacheStrategy",
    "LinuxTCPInfoStrategy",
    "PortableHealthChecker",
]
