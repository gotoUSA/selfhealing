"""
Self-Healing Services Module

Provides high-level services for self-healing functionality.
This module re-exports services from the shopping application
during the migration period.

Usage:
    from selfhealing.services import CircuitBreakerService, DLQService
    from selfhealing.services import get_circuit_breaker_service, get_dlq_service

Note:
    This module bridges the migration from shopping.services.self_healing
    to the standalone selfhealing package.
"""

# Re-export from shopping.services.self_healing during migration
# This allows new code to use the new import path while maintaining compatibility

from shopping.services.self_healing.circuit_breaker_service import (
    CircuitBreakerService,
    CircuitBreakerConfig,
    CircuitBreakerResult,
    CircuitState,
    get_circuit_breaker_service,
    should_allow_request,
    force_open_circuit,
    force_close_circuit,
    RateLimitTracker,
)

from shopping.services.self_healing.dlq_service import (
    DLQService,
    DLQConfig,
    DLQEntryResult,
    get_dlq_service,
    store_to_dlq,
)

from shopping.services.self_healing.replay_service import (
    ReplayService,
    ReplayResult,
    BatchReplayResult,
    ReplayHandler,
    get_replay_handler,
    get_replay_service,
    replay_failed_operation,
    batch_replay_by_failure_type,
)

from shopping.services.self_healing.retry_handler import (
    RetryHandler,
    RetryConfig,
    RetryResult,
    MaxRetriesExceededError,
)

from shopping.services.self_healing.idempotency_service import (
    IdempotencyService,
    IdempotencyKey,
    IdempotencyResult,
)

from shopping.services.self_healing.forensic_context import (
    ForensicContext,
    capture_forensic_context,
)

from shopping.services.self_healing.security_violation_service import (
    SecurityViolationService,
    SecurityViolationResult,
    SecurityConfig,
    ViolationType,
    Severity,
    SEVERITY_BY_VIOLATION_TYPE,
    get_security_violation_service,
    handle_security_violation,
)

from shopping.services.self_healing.security_notification_service import (
    SecurityNotificationService,
    SecurityNotificationResult,
    NotificationConfig,
    NotificationChannel,
    get_security_notification_service,
    notify_security_incident,
)

from shopping.services.self_healing.control_api_service import (
    ControlAPIService,
    ControlRequest,
    ControlResponse,
    get_control_api_service,
)

from shopping.services.self_healing.metrics import (
    DOMAINS,
    ALERTING_RULES,
    record_dlq_item_created,
    record_retry_attempt,
    record_recovery_time,
    record_sla_breach,
    record_circuit_breaker_state_change,
    record_circuit_breaker_open_duration,
    record_replay_attempt,
    update_dlq_pending_gauges,
    update_dlq_status_gauges,
    update_circuit_breaker_gauges,
    update_retry_success_rates,
    collect_all_metrics,
    track_recovery_time,
    track_replay,
)

from shopping.services.self_healing.config import (
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

from shopping.services.self_healing.factory import (
    create_failed_operation_repository,
    create_circuit_breaker_repository,
    create_security_incident_repository,
    create_dlq_service,
    create_replay_service,
    create_circuit_breaker_service,
    create_security_violation_service,
    get_dlq_service_with_di,
    get_replay_service_with_di,
    get_circuit_breaker_service_with_di,
    get_security_violation_service_with_di,
    reset_service_singletons,
)

__all__ = [
    # Circuit Breaker Service
    "CircuitBreakerService",
    "CircuitBreakerConfig",
    "CircuitBreakerResult",
    "CircuitState",
    "get_circuit_breaker_service",
    "should_allow_request",
    "force_open_circuit",
    "force_close_circuit",
    "RateLimitTracker",
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
    # Retry Handler
    "RetryHandler",
    "RetryConfig",
    "RetryResult",
    "MaxRetriesExceededError",
    # Idempotency Service
    "IdempotencyService",
    "IdempotencyKey",
    "IdempotencyResult",
    # Forensic Context
    "ForensicContext",
    "capture_forensic_context",
    # Security Violation Service
    "SecurityViolationService",
    "SecurityViolationResult",
    "SecurityConfig",
    "ViolationType",
    "Severity",
    "SEVERITY_BY_VIOLATION_TYPE",
    "get_security_violation_service",
    "handle_security_violation",
    # Security Notification Service
    "SecurityNotificationService",
    "SecurityNotificationResult",
    "NotificationConfig",
    "NotificationChannel",
    "get_security_notification_service",
    "notify_security_incident",
    # Control API Service
    "ControlAPIService",
    "ControlRequest",
    "ControlResponse",
    "get_control_api_service",
    # Metrics
    "DOMAINS",
    "ALERTING_RULES",
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
    # Configuration
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
    # Factory
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
