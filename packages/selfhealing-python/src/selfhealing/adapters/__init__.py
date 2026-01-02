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

Reference: docs/PLUGGABLE_ARCHITECTURE.md

NOTE: Django and SQLAlchemy adapters have been removed in v2.0.0.
      Use Redis adapters (with ResilientStorageBackend fallback) instead.
      See docs/self_healing/middleware_system/06_REDIS_MIGRATION.md
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
# Repository Adapters - InMemory (Testing)
# =============================================================================
from selfhealing.adapters.memory import (
    InMemoryFailedOperationRepository,
    InMemoryCircuitBreakerStateRepository,
    InMemorySecurityIncidentRepository,
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

# =============================================================================
# Observability Adapters (Optional Extensions)
# =============================================================================
# These adapters are optional and gracefully degrade to NO-OP when not available
try:
    from selfhealing.adapters.observability import (
        OpenTelemetryAdapter,
        OpenTelemetryConfig,
        get_opentelemetry_adapter,
        emit_selfhealing_event,
        start_decision_span,
        end_decision_span,
    )

    OPENTELEMETRY_ADAPTER_AVAILABLE = True
except ImportError:
    # OpenTelemetry adapter not available (missing dependencies)
    OPENTELEMETRY_ADAPTER_AVAILABLE = False
    OpenTelemetryAdapter = None
    OpenTelemetryConfig = None
    get_opentelemetry_adapter = None
    emit_selfhealing_event = None
    start_decision_span = None
    end_decision_span = None

# =============================================================================
# Health Checker Adapters (Platinum SLA Optimization)
# =============================================================================
from selfhealing.adapters.health_checker import (
    HealthCheckStrategy,
    TTLCacheStrategy,
    LinuxTCPInfoStrategy,
    PortableHealthChecker,
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
    # Observability Adapters (Optional)
    # =========================================================================
    "OPENTELEMETRY_ADAPTER_AVAILABLE",
    "OpenTelemetryAdapter",
    "OpenTelemetryConfig",
    "get_opentelemetry_adapter",
    "emit_selfhealing_event",
    "start_decision_span",
    "end_decision_span",
    # =========================================================================
    # Health Checker Adapters (Platinum SLA Optimization)
    # =========================================================================
    "HealthCheckStrategy",
    "TTLCacheStrategy",
    "LinuxTCPInfoStrategy",
    "PortableHealthChecker",
]
