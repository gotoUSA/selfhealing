"""
L3 Self-Healing Services

.. deprecated:: 0.1.0
    This module is deprecated. Please migrate to the standalone `selfhealing` package.

    Before:
        from shopping.services.self_healing import CircuitBreakerService

    After:
        from selfhealing.services import CircuitBreakerService

    See: packages/selfhealing-python/docs/MIGRATION.md

This module provides self-healing capabilities for the shopping application.
Includes retry logic, backoff calculation, idempotency checking, DLQ management,
replay functionality, circuit breaker management, and observability metrics.

Reference: docs/L3_SELF_HEALING_ARCHITECTURE.md (§7, §8)
Reference: docs/L3_SELF_HEALING_OPERATIONS.md (§1, §2, §7, §9)
"""

import warnings

# Emit deprecation warning on module import
warnings.warn(
    "Importing from 'shopping.services.self_healing' is deprecated and will be "
    "removed in a future version. Please migrate to 'selfhealing' package. "
    "See packages/selfhealing-python/docs/MIGRATION.md for details.",
    DeprecationWarning,
    stacklevel=2,
)

from .config import (
    SelfHealingConfig,
    SLAThresholds,
    IdempotencyConfig,
    SecurityThresholds,
    NotificationLimits,
    SlackChannels,
    RetrySettings,
    CircuitBreakerSettings,
    DLQSettings,
    ForensicSettings,
    get_config,
    reload_config,
    get_sla_thresholds,
    get_idempotency_config,
    get_security_thresholds,
    get_notification_limits,
    get_slack_channels,
    get_retry_settings,
    get_circuit_breaker_settings,
    get_dlq_settings,
    get_forensic_settings,
)
from .retry_handler import (
    RetryHandler,
    RetryConfig,
    RetryResult,
    MaxRetriesExceededError,
)
from .backoff_calculator import (
    BackoffCalculator,
    calculate_backoff,
)
from .idempotency_service import (
    IdempotencyService,
    IdempotencyKey,
    IdempotencyResult,
)
from .forensic_context import (
    ForensicContext,
    capture_forensic_context,
)
from .dlq_service import (
    DLQService,
    DLQConfig,
    DLQEntryResult,
    get_dlq_service,
    store_to_dlq,
)
from .replay_service import (
    ReplayService,
    ReplayResult,
    BatchReplayResult,
    ReplayHandler,
    get_replay_handler,
    get_replay_service,
    replay_failed_operation,
    batch_replay_by_failure_type,
)
from .circuit_breaker_service import (
    CircuitBreakerService,
    CircuitBreakerConfig,
    CircuitBreakerResult,
    CircuitState,
    get_circuit_breaker_service,
    should_allow_request,
    force_open_circuit,
    force_close_circuit,
)
from .metrics import (
    # Constants
    DOMAINS,
    # Recording functions
    record_dlq_item_created,
    record_retry_attempt,
    record_recovery_time,
    record_sla_breach,
    record_circuit_breaker_state_change,
    record_circuit_breaker_open_duration,
    record_replay_attempt,
    # Gauge update functions
    update_dlq_pending_gauges,
    update_dlq_status_gauges,
    update_circuit_breaker_gauges,
    update_retry_success_rates,
    collect_all_metrics,
    # Context managers/decorators
    track_recovery_time,
    track_replay,
)
from .security_violation_service import (
    SecurityViolationService,
    SecurityViolationResult,
    SecurityConfig,
    ViolationType,
    SEVERITY_BY_VIOLATION_TYPE,
    get_security_violation_service,
    handle_security_violation,
)
from .security_notification_service import (
    SecurityNotificationService,
    SecurityNotificationResult,
    NotificationConfig,
    NotificationChannel,
    get_security_notification_service,
    notify_security_incident,
)
from .factory import (
    # Repository factory functions
    create_failed_operation_repository,
    create_circuit_breaker_repository,
    create_security_incident_repository,
    # Service factory functions
    create_dlq_service,
    create_replay_service,
    create_circuit_breaker_service,
    create_security_violation_service,
    # DI-enabled singleton accessors
    get_dlq_service_with_di,
    get_replay_service_with_di,
    get_circuit_breaker_service_with_di,
    get_security_violation_service_with_di,
    # Utility
    reset_service_singletons,
)

__all__ = [
    # Configuration (centralized constants)
    "SelfHealingConfig",
    "SLAThresholds",
    "IdempotencyConfig",
    "SecurityThresholds",
    "NotificationLimits",
    "SlackChannels",
    "RetrySettings",
    "CircuitBreakerSettings",
    "DLQSettings",
    "ForensicSettings",
    "get_config",
    "reload_config",
    "get_sla_thresholds",
    "get_idempotency_config",
    "get_security_thresholds",
    "get_notification_limits",
    "get_slack_channels",
    "get_retry_settings",
    "get_circuit_breaker_settings",
    "get_dlq_settings",
    "get_forensic_settings",
    # Retry
    "RetryHandler",
    "RetryConfig",
    "RetryResult",
    "MaxRetriesExceededError",
    # Backoff
    "BackoffCalculator",
    "calculate_backoff",
    # Idempotency
    "IdempotencyService",
    "IdempotencyKey",
    "IdempotencyResult",
    # Forensic
    "ForensicContext",
    "capture_forensic_context",
    # DLQ Service
    "DLQService",
    "DLQConfig",
    "DLQEntryResult",
    "get_dlq_service",
    "store_to_dlq",
    # Replay Service
    "ReplayService",
    "ReplayResult",
    "BatchReplayResult",
    "ReplayHandler",
    "get_replay_handler",
    "get_replay_service",
    "replay_failed_operation",
    "batch_replay_by_failure_type",
    # Circuit Breaker Service
    "CircuitBreakerService",
    "CircuitBreakerConfig",
    "CircuitBreakerResult",
    "CircuitState",
    "get_circuit_breaker_service",
    "should_allow_request",
    "force_open_circuit",
    "force_close_circuit",
    # Metrics (Phase 5 Observability)
    "DOMAINS",
    "record_dlq_item_created",
    "record_retry_attempt",
    "record_recovery_time",
    "record_sla_breach",
    "record_circuit_breaker_state_change",
    "record_circuit_breaker_open_duration",
    "record_replay_attempt",
    "update_dlq_pending_gauges",
    "update_dlq_status_gauges",
    "update_circuit_breaker_gauges",
    "update_retry_success_rates",
    "collect_all_metrics",
    "track_recovery_time",
    "track_replay",
    # Security Violation Service (Phase 6)
    "SecurityViolationService",
    "SecurityViolationResult",
    "SecurityConfig",
    "ViolationType",
    "SEVERITY_BY_VIOLATION_TYPE",
    "get_security_violation_service",
    "handle_security_violation",
    # Security Notification Service (Phase 6)
    "SecurityNotificationService",
    "SecurityNotificationResult",
    "NotificationConfig",
    "NotificationChannel",
    "get_security_notification_service",
    "notify_security_incident",
    # Factory (for DI and testing)
    "create_failed_operation_repository",
    "create_circuit_breaker_repository",
    "create_security_incident_repository",
    "create_dlq_service",
    "create_replay_service",
    "create_circuit_breaker_service",
    "create_security_violation_service",
    "get_dlq_service_with_di",
    "get_replay_service_with_di",
    "get_circuit_breaker_service_with_di",
    "get_security_violation_service_with_di",
    "reset_service_singletons",
]
