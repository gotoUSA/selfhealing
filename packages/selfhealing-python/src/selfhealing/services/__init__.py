"""
L3 Self-Healing Services

.. deprecated:: 0.1.0
    This module structure is deprecated. Use direct imports from selfhealing.

    Before (old Django app pattern):
        from myapp.services.self_healing import CircuitBreakerService

    After:
        from selfhealing.services import CircuitBreakerService

    See: packages/selfhealing-python/docs/MIGRATION.md

This module provides self-healing capabilities for applications.
Includes retry logic, backoff calculation, idempotency checking, DLQ management,
replay functionality, circuit breaker management, and observability metrics.

Reference: docs/L3_SELF_HEALING_ARCHITECTURE.md (§7, §8)
Reference: docs/L3_SELF_HEALING_OPERATIONS.md (§1, §2, §7, §9)

Phase 3 Refactoring (2026-01-04):
- Reduced from 182 exports to 63 exports (66% reduction)
- Only symbols actually used by tests and shopping app are exported
- Unused symbols can still be imported directly from submodules
"""

import warnings

# Emit deprecation warning on module import
warnings.warn(
    "Importing from old module paths is deprecated and will be "
    "removed in a future version. Please migrate to 'selfhealing' package. "
    "See packages/selfhealing-python/docs/MIGRATION.md for details.",
    DeprecationWarning,
    stacklevel=2,
)

# =============================================================================
# Configuration (only get_sla_thresholds is used externally)
# =============================================================================
from ..core.config import get_sla_thresholds

# =============================================================================
# Retry (used by tests)
# =============================================================================
from .retry_handler import (
    RetryHandler,
    RetryConfig,
    RetryResult,
    RetryAction,
    MaxRetriesExceededError,
)

# =============================================================================
# Idempotency (used by tests)
# =============================================================================
from .idempotency_service import (
    IdempotencyService,
    IdempotencyKey,
    IdempotencyDomain,
    get_idempotency_service,
)

# =============================================================================
# Forensic (used by shopping)
# =============================================================================
from .forensic_context import ForensicContext

# =============================================================================
# Control API (used by tests)
# =============================================================================
from .control_api_service import (
    ControlAPIService,
    ControlRequest,
    ControlResponse,
)

# =============================================================================
# DLQ Service (widely used)
# =============================================================================
from .dlq_service import (
    DLQService,
    DLQConfig,
    DLQEntryResult,
    get_dlq_service,
)

# =============================================================================
# Replay Service (widely used)
# =============================================================================
from .replay_service import (
    ReplayService,
    ReplayResult,
    BatchReplayResult,
    get_replay_service,
)

# =============================================================================
# Circuit Breaker Service (most used)
# =============================================================================
from .circuit_breaker_service import (
    CircuitBreakerService,
    CircuitBreakerConfig,
    CircuitBreakerResult,
    CircuitState,
    get_circuit_breaker_service,
    should_allow_request,
    force_open_circuit,
    force_close_circuit,
    # Rate Limit / Self-DDoS Protection
    RateLimitTracker,
    get_rate_limit_tracker,
    record_rate_limit,
    should_allow_with_protection,
    get_protection_status,
)

# Module alias for tests
from . import circuit_breaker_service

# =============================================================================
# Metrics (selectively used)
# =============================================================================
from .metrics import (
    # Constants
    ALERTING_RULES,
    DEFAULT_DOMAINS,  # Used as DOMAINS by shopping
    # Recording functions
    record_dlq_item_created,
    record_retry_attempt,
    record_recovery_time,
    record_sla_breach,
    record_circuit_breaker_state_change,
    record_circuit_breaker_open_duration,
    record_replay_attempt,
    # Aggregation
    collect_all_metrics,
    # Context managers
    track_recovery_time,
)

# Alias for backward compatibility (shopping uses DOMAINS)
DOMAINS = DEFAULT_DOMAINS

# =============================================================================
# Security Violation Service (used by shopping and tests)
# =============================================================================
from .security_violation_service import (
    SecurityViolationService,
    SecurityViolationResult,
    SecurityConfig,
    ViolationType,
    Severity,
    SEVERITY_BY_VIOLATION_TYPE,
    get_security_violation_service,
    handle_security_violation,
)

# =============================================================================
# Security Notification Service (used by shopping and tests)
# =============================================================================
from .security_notification_service import (
    SecurityNotificationService,
    SecurityNotificationResult,
    NotificationResult,
    NotificationConfig,
    NotificationChannel,
    get_security_notification_service,
    notify_security_incident,
)


__all__ = [
    # === Config ===
    "get_sla_thresholds",

    # === Retry ===
    "RetryHandler",
    "RetryConfig",
    "RetryResult",
    "RetryAction",
    "MaxRetriesExceededError",

    # === Idempotency ===
    "IdempotencyService",
    "IdempotencyKey",
    "IdempotencyDomain",
    "get_idempotency_service",

    # === Forensic ===
    "ForensicContext",

    # === Control API ===
    "ControlAPIService",
    "ControlRequest",
    "ControlResponse",

    # === DLQ ===
    "DLQService",
    "DLQConfig",
    "DLQEntryResult",
    "get_dlq_service",

    # === Replay ===
    "ReplayService",
    "ReplayResult",
    "BatchReplayResult",
    "get_replay_service",

    # === Circuit Breaker ===
    "CircuitBreakerService",
    "CircuitBreakerConfig",
    "CircuitBreakerResult",
    "CircuitState",
    "get_circuit_breaker_service",
    "should_allow_request",
    "force_open_circuit",
    "force_close_circuit",
    "circuit_breaker_service",  # module alias

    # === Rate Limit ===
    "RateLimitTracker",
    "get_rate_limit_tracker",
    "record_rate_limit",
    "should_allow_with_protection",
    "get_protection_status",

    # === Metrics ===
    "ALERTING_RULES",
    "DOMAINS",  # alias for DEFAULT_DOMAINS
    "collect_all_metrics",
    "record_dlq_item_created",
    "record_retry_attempt",
    "record_recovery_time",
    "record_sla_breach",
    "record_circuit_breaker_state_change",
    "record_circuit_breaker_open_duration",
    "record_replay_attempt",
    "track_recovery_time",

    # === Security Violation ===
    "SecurityViolationService",
    "SecurityViolationResult",
    "SecurityConfig",
    "ViolationType",
    "Severity",
    "SEVERITY_BY_VIOLATION_TYPE",
    "get_security_violation_service",
    "handle_security_violation",

    # === Security Notification ===
    "SecurityNotificationService",
    "SecurityNotificationResult",
    "NotificationResult",
    "NotificationConfig",
    "NotificationChannel",
    "get_security_notification_service",
    "notify_security_incident",
]
