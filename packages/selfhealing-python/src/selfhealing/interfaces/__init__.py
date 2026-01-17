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
# Repository Interfaces
# =============================================================================
from selfhealing.interfaces.repositories import (
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
# Cache Provider Interface
# =============================================================================
from selfhealing.interfaces.cache_provider import (
    # Lock interface
    DistributedLock,
    # Exceptions
    LockAcquisitionError,
    LockNotOwnedError,
    # Interface
    CacheProviderInterface,
)

# =============================================================================
# Task Queue Interface
# =============================================================================
from selfhealing.interfaces.task_queue import (
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
# Web Framework Interface
# =============================================================================
from selfhealing.interfaces.web_framework import (
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

# =============================================================================
# Configuration Provider Interface
# =============================================================================
from selfhealing.interfaces.config_provider import (
    # Interface
    ConfigProviderInterface,
    # Default implementations
    DictConfigProvider,
    EnvConfigProvider,
)

# =============================================================================
# Rate Limit Storage Interface (Distributed Self-DDoS Prevention)
# =============================================================================
from selfhealing.interfaces.rate_limit_storage import (
    # Enums
    RateLimitStorageType,
    # Data Classes
    RateLimitState,
    # Interface
    RateLimitStorageInterface,
    # Exceptions
    RateLimitStorageError,
    RateLimitStorageUnavailableError,
)

# =============================================================================
# Audit Log Adapter Interface (Non-invasive audit logging)
# =============================================================================
from selfhealing.interfaces.audit_adapter import (
    # Enums
    AuditAction,
    # Data Classes
    AuditEntry,
    # Interface
    AuditLogAdapter,
)

# =============================================================================
# Alert Adapter Interface (Non-invasive alerting)
# =============================================================================
from selfhealing.interfaces.alert_adapter import (
    # Enums
    AlertSeverity,
    AlertCategory,
    # Data Classes
    Alert,
    # Interface
    AlertAdapter,
)

# =============================================================================
# Statistics Repository Interface (Hybrid Storage - v2.3.0)
# =============================================================================
from selfhealing.interfaces.statistics import (
    # Data Classes
    StatusCounts,
    DomainDistribution,
    FailureTypeDistribution,
    RecentActivity,
    CleanupStats,
    PaginatedResult,
    CircuitBreakerSummary,
    CircuitBreakerInfo,
    # Audit Trail DTOs (The Master Trail - v2.4.0)
    AuditTrailEntry,
    EntityAuditTrail,
    # Interface
    StatisticsRepositoryInterface,
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
]
