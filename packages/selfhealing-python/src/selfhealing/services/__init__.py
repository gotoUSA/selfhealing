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

from ..core.config import (
    SelfHealingConfig,
    SLAConfig as SLAThresholds,  # Legacy alias
    IdempotencyConfig,
    SecurityConfig as SecurityThresholds,  # Legacy alias
    NotificationConfig as NotificationLimits,  # Legacy alias
    RetryConfig as RetrySettings,  # Legacy alias
    CircuitBreakerConfig as CircuitBreakerSettings,  # Legacy alias
    DLQConfig as DLQSettings,  # Legacy alias
    ForensicConfig as ForensicSettings,  # Legacy alias
    get_config,
    reload_config,
    get_sla_thresholds,
    get_security_thresholds,
    get_retry_settings,
    get_circuit_breaker_settings,
    get_dlq_settings,
    get_forensic_settings,
)


# Compatibility aliases for removed configs
def get_idempotency_config():
    """Get idempotency configuration."""
    return get_config().idempotency


def get_notification_limits():
    """Get notification limits configuration."""
    return get_config().notification


def get_slack_channels():
    """Get slack channels from notification config."""
    config = get_config().notification
    return {
        "critical": config.critical_channel,
        "high": config.high_channel,
        "medium": config.medium_channel,
    }


# Legacy alias
SlackChannels = dict
from .retry_handler import (
    RetryHandler,
    RetryConfig,
    RetryResult,
    RetryAction,  # Enum for retry decisions
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
    IdempotencyDomain,
    get_idempotency_service,
)
from .forensic_context import (
    ForensicContext,
    capture_forensic_context,
)
from .forensic_advisor import (
    AdvisoryLevel,
    RecommendedAction,
    FailurePattern,
    ForensicAdvisory,
    ForensicAdvisorService,
    KNOWN_PATTERNS,
    get_forensic_advisor,
    analyze_failed_operation,
    analyze_and_update_operation,
)
from .chaos_context import (
    ChaosExperimentType,
    ChaosExperimentStatus,
    ChaosExperimentContext,
    is_chaos_experiment,
    get_chaos_context,
    attach_chaos_context,
    resolve_chaos_experiment,
    create_chaos_context,
)
from .control_api_service import (
    ControlAPIService,
    ControlRequest,
    ControlResponse,
    get_control_api_service,
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
    DefaultReplayHandler,
    get_replay_handler,
    register_replay_handler,
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
    # Rate Limit / Self-DDoS Protection
    RateLimitTracker,
    get_rate_limit_tracker,
    record_rate_limit,
    should_allow_with_protection,
    should_allow_with_protection as should_allow_with_ddos_protection,  # Alias
    get_protection_status,
)
from .rate_limit_coordinator import (
    RateLimitCoordinator,
    RateLimitResult,
    get_rate_limit_coordinator,
)
from selfhealing.core.config import RateLimitConfig
from .metrics import (
    # Domain registry (dynamic)
    register_domain,
    get_registered_domains,
    # Constants
    ALERTING_RULES,
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
    Severity,
    SEVERITY_BY_VIOLATION_TYPE,
    get_security_violation_service,
    handle_security_violation,
)
from .security_notification_service import (
    SecurityNotificationService,
    SecurityNotificationResult,
    NotificationResult,
    NotificationConfig,
    NotificationChannel,
    get_security_notification_service,
    notify_security_incident_by_id,
    notify_security_incident,  # Legacy API for incident objects
    send_alert,  # General-purpose alert function
)
from .unified_notification import (
    UnifiedNotificationManager,
    NotificationPayload,
    NotificationPriority,
    NotificationCategory,
    RoutingPolicy,
    get_unified_notification_manager,
    notify,
    notify_security,
    notify_sla,
    notify_error,
)
from .runtime_config import (
    RuntimeConfigManager,
    get_runtime_config_manager,
)
from .health_check import (
    HealthCheckService,
    HealthStatus,
    ReadinessStatus,
    PoolHealthStatus,
    DatabaseCheck,
    PoolInfo,
    get_health_check_service,
)
from .system_control import (
    SystemControlManager,
    SystemState,
    get_system_control,
    is_selfhealing_enabled,
    is_dry_run,
    should_execute_action,
)
from .governance_checks import (
    # Result types
    BlockReason,
    GovernanceCheckResult,
    # Check functions
    is_system_enabled,
    is_emergency_blocking,
    is_error_budget_blocking,
    check_all_governance,
    invalidate_governance_cache,
    # Decorators
    require_system_enabled,
    require_not_emergency,
    require_error_budget,
    require_governance,
    # Mixin
    GovernanceCheckMixin,
    # Cache
    TTLCache,
)
from .governance_service import (
    GovernanceService,
    ExpiryCheckResult,
    NotificationResult as GovernanceNotificationResult,
    get_governance_service,
)
from .execution_services import (
    # Chaos Execution
    ChaosExecutionService,
    ExperimentExecutionResult,
    DailyReportResult,
    ApprovalCleanupResult,
    PendingApprovalCheckResult,
    get_chaos_execution_service,
    # Config Apply
    ConfigApplyService,
    get_config_apply_service,
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
    "RetryAction",
    "MaxRetriesExceededError",
    # Backoff
    "BackoffCalculator",
    "calculate_backoff",
    # Idempotency
    "IdempotencyService",
    "IdempotencyKey",
    "IdempotencyResult",
    "IdempotencyDomain",
    "get_idempotency_service",
    # Forensic
    "ForensicContext",
    "capture_forensic_context",
    # Forensic Advisor (Human-in-the-loop decision support)
    "AdvisoryLevel",
    "RecommendedAction",
    "FailurePattern",
    "ForensicAdvisory",
    "ForensicAdvisorService",
    "KNOWN_PATTERNS",
    "get_forensic_advisor",
    "analyze_failed_operation",
    "analyze_and_update_operation",
    # Chaos Context (Chaos engineering experiment tracking)
    "ChaosExperimentType",
    "ChaosExperimentStatus",
    "ChaosExperimentContext",
    "is_chaos_experiment",
    "get_chaos_context",
    "attach_chaos_context",
    "resolve_chaos_experiment",
    "create_chaos_context",
    # Control API Service
    "ControlAPIService",
    "ControlRequest",
    "ControlResponse",
    "get_control_api_service",
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
    # Rate Limit / Self-DDoS Protection
    "RateLimitTracker",
    "get_rate_limit_tracker",
    "record_rate_limit",
    "should_allow_with_protection",
    "should_allow_with_ddos_protection",  # Alias
    "get_protection_status",
    # Rate Limit Coordinator (Distributed Self-DDoS Prevention)
    "RateLimitCoordinator",
    "RateLimitConfig",
    "RateLimitResult",
    "get_rate_limit_coordinator",
    # Metrics (Phase 5 Observability)
    "register_domain",
    "get_registered_domains",
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
    # Security Violation Service (Phase 6)
    "SecurityViolationService",
    "SecurityViolationResult",
    "SecurityConfig",
    "ViolationType",
    "Severity",
    "SEVERITY_BY_VIOLATION_TYPE",
    "get_security_violation_service",
    "handle_security_violation",
    # Security Notification Service (Phase 6)
    "SecurityNotificationService",
    "SecurityNotificationResult",
    "NotificationResult",
    "NotificationConfig",
    "NotificationChannel",
    "get_security_notification_service",
    "notify_security_incident",
    "send_alert",  # General-purpose alert function
    # Unified Notification Manager (Centralized Notifications)
    "UnifiedNotificationManager",
    "NotificationPayload",
    "NotificationPriority",
    "NotificationCategory",
    "RoutingPolicy",
    "get_unified_notification_manager",
    "notify",
    "notify_security",
    "notify_sla",
    "notify_error",
    # Runtime Config (Runtime Configuration API)
    "RuntimeConfigManager",
    "get_runtime_config_manager",
    # Health Check Service (Phase 3)
    "HealthCheckService",
    "HealthStatus",
    "ReadinessStatus",
    "PoolHealthStatus",
    "DatabaseCheck",
    "PoolInfo",
    "get_health_check_service",
    # System Control Service (Phase 4)
    "SystemControlManager",
    "SystemState",
    "get_system_control",
    "is_selfhealing_enabled",
    "is_dry_run",
    "should_execute_action",
    # Governance Checks (Thin Task, Fat Service support)
    "BlockReason",
    "GovernanceCheckResult",
    "is_system_enabled",
    "is_emergency_blocking",
    "is_error_budget_blocking",
    "check_all_governance",
    "invalidate_governance_cache",
    "require_system_enabled",
    "require_not_emergency",
    "require_error_budget",
    "require_governance",
    "GovernanceCheckMixin",
    "TTLCache",
    # Governance Service (Emergency Mode Management)
    "GovernanceService",
    "ExpiryCheckResult",
    "GovernanceNotificationResult",
    "get_governance_service",
    # Chaos Execution Service
    "ChaosExecutionService",
    "ExperimentExecutionResult",
    "DailyReportResult",
    "ApprovalCleanupResult",
    "PendingApprovalCheckResult",
    "get_chaos_execution_service",
    # Config Apply Service
    "ConfigApplyService",
    "get_config_apply_service",
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
