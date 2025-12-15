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
        - PaymentProviderAdapter (Base for payment integrations)
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
]
