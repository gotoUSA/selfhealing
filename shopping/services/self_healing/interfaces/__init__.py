"""
Self-Healing Interfaces Module

Abstract interfaces for the pluggable self-healing architecture.
These interfaces decouple the self-healing core logic from external
dependencies (Django, Redis, Celery, payment providers), enabling:
- Framework migration (Django -> FastAPI, Flask)
- Payment provider switching (Toss -> Stripe, Iamport)
- Cache backend switching (Redis -> Memcached, DynamoDB)
- Task queue switching (Celery -> RQ, Dramatiq)

Usage:
    from shopping.services.self_healing.interfaces import (
        # Repository interfaces
        FailedOperationRepository,
        CircuitBreakerStateRepository,
        SecurityIncidentRepository,
        # Payment provider interface
        PaymentProviderInterface,
        PaymentConfirmResult,
        PaymentCancelResult,
        # Cache provider interface
        CacheProviderInterface,
        DistributedLock,
        # Task queue interface
        TaskQueueInterface,
        TaskResult,
        TaskOptions,
        # Web framework interface
        WebFrameworkInterface,
        RequestContext,
        ResponseContext,
    )

Reference: docs/PLUGGABLE_ARCHITECTURE.md
"""

# =============================================================================
# Repository Interfaces (Phase 0 - Already implemented)
# =============================================================================
from shopping.services.self_healing.interfaces.repositories import (
    # Enums
    FailedOperationDomain,
    FailedOperationStatus,
    CircuitBreakerStateEnum,
    SecurityIncidentType,
    SecuritySeverity,
    SecurityIncidentStatus,
    # Data Classes
    FailedOperationData,
    CircuitBreakerStateData,
    SecurityIncidentData,
    # Repository Interfaces
    FailedOperationRepository,
    CircuitBreakerStateRepository,
    SecurityIncidentRepository,
)

# =============================================================================
# Payment Provider Interface (Phase 1)
# =============================================================================
from shopping.services.self_healing.interfaces.payment_provider import (
    # DTOs
    PaymentConfirmResult,
    PaymentCancelResult,
    WebhookVerifyResult,
    PaymentStatusResult,
    # Interface
    PaymentProviderInterface,
)

# =============================================================================
# Cache Provider Interface (Phase 1)
# =============================================================================
from shopping.services.self_healing.interfaces.cache_provider import (
    # Lock interface
    DistributedLock,
    # Exceptions
    LockAcquisitionError,
    LockNotOwnedError,
    # Interface
    CacheProviderInterface,
)

# =============================================================================
# Task Queue Interface (Phase 1)
# =============================================================================
from shopping.services.self_healing.interfaces.task_queue import (
    # Enums
    TaskStatus,
    TaskPriority,
    # DTOs
    TaskResult,
    TaskOptions,
    ScheduleInfo,
    # Exceptions
    TaskQueueError,
    TaskNotFoundError,
    TaskTimeoutError,
    TaskRevokedError,
    # Interface
    TaskQueueInterface,
)

# =============================================================================
# Web Framework Interface (Phase 1)
# =============================================================================
from shopping.services.self_healing.interfaces.web_framework import (
    # Enums
    HttpMethod,
    ContentType,
    # DTOs
    RequestContext,
    ResponseContext,
    # Exceptions
    WebFrameworkError,
    RouteNotFoundError,
    AuthenticationError,
    PermissionDeniedError,
    # Interface
    WebFrameworkInterface,
    # Type alias
    HandlerFunc,
)


__all__ = [
    # =========================================================================
    # Repository Interfaces
    # =========================================================================
    # Enums
    "FailedOperationDomain",
    "FailedOperationStatus",
    "CircuitBreakerStateEnum",
    "SecurityIncidentType",
    "SecuritySeverity",
    "SecurityIncidentStatus",
    # Data Classes
    "FailedOperationData",
    "CircuitBreakerStateData",
    "SecurityIncidentData",
    # Interfaces
    "FailedOperationRepository",
    "CircuitBreakerStateRepository",
    "SecurityIncidentRepository",
    
    # =========================================================================
    # Payment Provider Interface
    # =========================================================================
    # DTOs
    "PaymentConfirmResult",
    "PaymentCancelResult",
    "WebhookVerifyResult",
    "PaymentStatusResult",
    # Interface
    "PaymentProviderInterface",
    
    # =========================================================================
    # Cache Provider Interface
    # =========================================================================
    # Lock
    "DistributedLock",
    # Exceptions
    "LockAcquisitionError",
    "LockNotOwnedError",
    # Interface
    "CacheProviderInterface",
    
    # =========================================================================
    # Task Queue Interface
    # =========================================================================
    # Enums
    "TaskStatus",
    "TaskPriority",
    # DTOs
    "TaskResult",
    "TaskOptions",
    "ScheduleInfo",
    # Exceptions
    "TaskQueueError",
    "TaskNotFoundError",
    "TaskTimeoutError",
    "TaskRevokedError",
    # Interface
    "TaskQueueInterface",
    
    # =========================================================================
    # Web Framework Interface
    # =========================================================================
    # Enums
    "HttpMethod",
    "ContentType",
    # DTOs
    "RequestContext",
    "ResponseContext",
    # Exceptions
    "WebFrameworkError",
    "RouteNotFoundError",
    "AuthenticationError",
    "PermissionDeniedError",
    # Interface
    "WebFrameworkInterface",
    # Type alias
    "HandlerFunc",
]
