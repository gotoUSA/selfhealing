"""
Self-Healing Interfaces Module

Abstract interfaces for the pluggable self-healing architecture.
These interfaces decouple the self-healing core logic from external
dependencies (Django, Redis, Celery, etc.), enabling:
- Framework migration (Django -> FastAPI, Flask)
- Cache backend switching (Redis -> Memcached, DynamoDB)
- Task queue switching (Celery -> RQ, Dramatiq)

Usage:
    from selfhealing.interfaces import (
        # Repository interfaces
        FailedOperationRepository,
        CircuitBreakerStateRepository,
        SecurityIncidentRepository,
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
"""

# =============================================================================
# Alert Adapter Interface (Non-invasive alerting)
# =============================================================================
from selfhealing.interfaces.alert_adapter import (  # Enums; Data Classes; Interface
    Alert,
    AlertAdapter,
    AlertCategory,
    AlertSeverity,
)

# =============================================================================
# Audit Log Adapter Interface (Non-invasive audit logging)
# =============================================================================
from selfhealing.interfaces.audit_adapter import (  # Enums; Data Classes; Interface
    AuditAction,
    AuditEntry,
    AuditLogAdapter,
)

# =============================================================================
# Traffic Routing Adapter Interface (Multi-Region Failover)
# =============================================================================
from selfhealing.interfaces.traffic_routing import (  # Data Classes; Interface
    RoutingChange,
    TrafficRoutingAdapter,
)

# =============================================================================
# Cache Provider Interface
# =============================================================================
from selfhealing.interfaces.cache_provider import (  # Lock interface; Exceptions; Interface
    CacheProviderInterface,
    DistributedLock,
    LockAcquisitionError,
    LockNotOwnedError,
)

# =============================================================================
# Configuration Provider Interface
# =============================================================================
from selfhealing.interfaces.config_provider import (  # Interface; Default implementations
    ConfigProviderInterface,
    DictConfigProvider,
    EnvConfigProvider,
)

# =============================================================================
# Rate Limit Storage Interface (Distributed Self-DDoS Prevention)
# =============================================================================
from selfhealing.interfaces.rate_limit_storage import (  # Enums; Data Classes; Interface; Exceptions
    RateLimitState,
    RateLimitStorageError,
    RateLimitStorageInterface,
    RateLimitStorageType,
    RateLimitStorageUnavailableError,
)

# =============================================================================
# Repository Interfaces
# =============================================================================
from selfhealing.interfaces.repositories import (  # Enums; Data Classes; Repository Interfaces
    CircuitBreakerStateData,
    CircuitBreakerStateEnum,
    CircuitBreakerStateRepository,
    FailedOperationData,
    FailedOperationDomain,
    FailedOperationRepository,
    FailedOperationStatus,
    SecurityIncidentData,
    SecurityIncidentRepository,
    SecurityIncidentStatus,
    SecurityIncidentType,
    SecuritySeverity,
)

# =============================================================================
# Statistics Repository Interface (Hybrid Storage - v2.3.0)
# =============================================================================
from selfhealing.interfaces.statistics import (  # Data Classes; Audit Trail DTOs (The Master Trail - v2.4.0); Interface
    AuditTrailEntry,
    CircuitBreakerInfo,
    CircuitBreakerSummary,
    CleanupStats,
    DomainDistribution,
    EntityAuditTrail,
    FailureTypeDistribution,
    PaginatedResult,
    RecentActivity,
    StatisticsRepositoryInterface,
    StatusCounts,
)

# =============================================================================
# Task Queue Interface
# =============================================================================
from selfhealing.interfaces.task_queue import (  # Enums; DTOs; Exceptions; Interface
    ScheduleInfo,
    TaskNotFoundError,
    TaskOptions,
    TaskPriority,
    TaskQueueError,
    TaskQueueInterface,
    TaskResult,
    TaskRevokedError,
    TaskStatus,
    TaskTimeoutError,
)

# =============================================================================
# Resilience Policy Interfaces (Policy Composition)
# =============================================================================
from selfhealing.interfaces.resilience_policy import (  # Enums; DTOs; Protocols
    AsyncResiliencePolicy,
    FailureSink,
    GuardResult,
    PolicyContext,
    PolicyGuard,
    PolicyHook,
    PolicyOutcome,
    PolicyResult,
    ResiliencePolicy,
)

# =============================================================================
# Web Framework Interface
# =============================================================================
from selfhealing.interfaces.web_framework import (  # Enums; DTOs; Exceptions; Interface; Type alias
    AuthenticationError,
    ContentType,
    HandlerFunc,
    HttpMethod,
    PermissionDeniedError,
    RequestContext,
    ResponseContext,
    RouteNotFoundError,
    WebFrameworkError,
    WebFrameworkInterface,
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
    # =========================================================================
    # Configuration Provider Interface
    # =========================================================================
    # Interface
    "ConfigProviderInterface",
    # Default implementations
    "DictConfigProvider",
    "EnvConfigProvider",
    # =========================================================================
    # Rate Limit Storage Interface (Distributed Self-DDoS Prevention)
    # =========================================================================
    # Enums
    "RateLimitStorageType",
    # Data Classes
    "RateLimitState",
    # Interface
    "RateLimitStorageInterface",
    # Exceptions
    "RateLimitStorageError",
    "RateLimitStorageUnavailableError",
    # =========================================================================
    # Audit Log Adapter Interface (Non-invasive audit logging)
    # =========================================================================
    # Enums
    "AuditAction",
    # Data Classes
    "AuditEntry",
    # Interface
    "AuditLogAdapter",
    # =========================================================================
    # Alert Adapter Interface (Non-invasive alerting)
    # =========================================================================
    # Enums
    "AlertSeverity",
    "AlertCategory",
    # Data Classes
    "Alert",
    # Interface
    "AlertAdapter",
    # =========================================================================
    # Traffic Routing Adapter Interface (Multi-Region Failover)
    # =========================================================================
    # Data Classes
    "RoutingChange",
    # Interface
    "TrafficRoutingAdapter",
    # =========================================================================
    # Statistics Repository Interface (Hybrid Storage - v2.3.0)
    # =========================================================================
    # Data Classes
    "StatusCounts",
    "DomainDistribution",
    "FailureTypeDistribution",
    "RecentActivity",
    "CleanupStats",
    "PaginatedResult",
    "CircuitBreakerSummary",
    "CircuitBreakerInfo",
    # Audit Trail DTOs (The Master Trail - v2.4.0)
    "AuditTrailEntry",
    "EntityAuditTrail",
    # Interface
    "StatisticsRepositoryInterface",
    # =========================================================================
    # Resilience Policy Interfaces (Policy Composition)
    # =========================================================================
    # Enums
    "PolicyOutcome",
    # DTOs
    "PolicyResult",
    "PolicyContext",
    "GuardResult",
    # Protocols
    "ResiliencePolicy",
    "AsyncResiliencePolicy",
    "PolicyGuard",
    "PolicyHook",
    "FailureSink",
]
