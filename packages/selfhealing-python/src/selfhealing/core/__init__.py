"""
Core module - Framework-agnostic business logic

This module contains pure Python implementations without any framework dependencies.

Backoff API:
    - BackoffCalculator: Simple config-based calculator (legacy interface)
      Usage: calc = BackoffCalculator(BackoffConfig()); calc.calculate(attempt)

    - ExponentialBackoff, LinearBackoff, etc.: Strategy pattern implementations
      Usage: strategy = ExponentialBackoff(base=2); strategy.calculate(attempt)
"""

from selfhealing.core.types import (
    FailureType,
    OperationStatus,
    CircuitState,
    DomainType,
    FailedOperationData,
    CircuitBreakerStateData,
    SecurityIncidentData,
    RetryContext,
    MetricsSnapshot,
)
from selfhealing.core.backoff import (
    # Strategy pattern implementations (advanced)
    ExponentialBackoff,
    LinearBackoff,
    ConstantBackoff,
    DecorrelatedJitterBackoff,
    get_backoff_calculator,
    # Simple config-based interface (recommended for most use cases)
    BackoffConfig,
    LegacyBackoffCalculator as BackoffCalculator,  # Config-based calculator
    calculate_backoff,
)
from selfhealing.core.config import (
    SelfHealingConfig,
    CircuitBreakerConfig,
    DLQConfig,
    RetryConfig,
    SLAConfig,
    SLAConfig as SLAThresholds,  # Legacy alias
    IdempotencyConfig,
    SecurityConfig,
    SecurityConfig as SecurityThresholds,  # Legacy alias
    ForensicConfig,
    MetricsConfig,
    NotificationConfig,
    NotificationConfig as NotificationLimits,  # Legacy alias
    get_config,
    set_config,
    configure,
    reload_config,
    get_circuit_breaker_settings,
    get_dlq_settings,
    get_retry_settings,
    get_sla_thresholds,
    get_security_thresholds,
    get_forensic_settings,
    get_notification_settings,
)
from selfhealing.core.forensic import (
    ForensicContext,
    ForensicContextBuilder,
    StateSnapshot,
    RetryAttempt,
    capture_forensic_context,
    create_snapshot_data,
    set_time_provider,
)
from selfhealing.core.pool_monitor import (
    PoolHealthStatus,
    PoolStats,
    ConnectionInfo,
    LeakReport,
    PoolStatsProvider,
    ConnectionPoolMonitor,
)
from selfhealing.core.pool_watchdog import (
    RecoveryAction,
    RecoveryResult,
    PoolRecoveryHandler,
    PoolWatchdog,
)
from selfhealing.core.shutdown_coordinator import (
    ShutdownPhase,
    RequestState,
    TrackedRequest,
    ShutdownStats,
    ShutdownHandler,
    RequestTracker,
    GracefulShutdownCoordinator,
)
from selfhealing.core.request_context import (
    RequestContext,
    track_request,
)

__all__ = [
    # Types
    "FailureType",
    "OperationStatus",
    "CircuitState",
    "DomainType",
    "FailedOperationData",
    "CircuitBreakerStateData",
    "SecurityIncidentData",
    "RetryContext",
    "MetricsSnapshot",
    # Backoff - Strategy implementations
    "ExponentialBackoff",
    "LinearBackoff",
    "ConstantBackoff",
    "DecorrelatedJitterBackoff",
    "get_backoff_calculator",
    # Backoff - Simple config-based interface
    "BackoffConfig",
    "BackoffCalculator",  # = LegacyBackoffCalculator, config-based
    "calculate_backoff",
    # Config
    "SelfHealingConfig",
    "CircuitBreakerConfig",
    "DLQConfig",
    "RetryConfig",
    "SLAConfig",
    "SLAThresholds",  # Legacy alias
    "IdempotencyConfig",
    "SecurityConfig",
    "SecurityThresholds",  # Legacy alias
    "ForensicConfig",
    "MetricsConfig",
    "NotificationConfig",
    "NotificationLimits",  # Legacy alias
    "get_config",
    "set_config",
    "configure",
    "reload_config",
    "get_circuit_breaker_settings",
    "get_dlq_settings",
    "get_retry_settings",
    "get_sla_thresholds",
    "get_security_thresholds",
    "get_forensic_settings",
    "get_notification_settings",
    # Forensic
    "ForensicContext",
    "ForensicContextBuilder",
    "StateSnapshot",
    "RetryAttempt",
    "capture_forensic_context",
    "create_snapshot_data",
    "set_time_provider",
    # Pool Monitor (Stage 26)
    "PoolHealthStatus",
    "PoolStats",
    "ConnectionInfo",
    "LeakReport",
    "PoolStatsProvider",
    "ConnectionPoolMonitor",
    # Pool Watchdog (Stage 26)
    "RecoveryAction",
    "RecoveryResult",
    "PoolRecoveryHandler",
    "PoolWatchdog",
    # Shutdown Coordinator (Stage 27)
    "ShutdownPhase",
    "RequestState",
    "TrackedRequest",
    "ShutdownStats",
    "ShutdownHandler",
    "RequestTracker",
    "GracefulShutdownCoordinator",
    # Request Context (Stage 27)
    "RequestContext",
    "track_request",
]
