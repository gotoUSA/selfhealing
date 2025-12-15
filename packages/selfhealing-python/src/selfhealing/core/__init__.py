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
from selfhealing.core.time_provider import (
    TimeProvider,
    SystemTimeProvider,
    MockTimeProvider,
    FrozenTime,
    get_time_provider,
    set_time_provider as set_global_time_provider,
    reset_time_provider,
    is_within_clock_skew,
)
from selfhealing.core.connection_health import (
    ConnectionType,
    ConnectionStatus,
    ConnectionHealth,
    PartitionState,
    ConnectionHealthMonitor,
    DefaultConnectionHealthMonitor,
)
from selfhealing.core.fallback_strategy import (
    FallbackMode,
    FallbackResult,
    FallbackStrategy,
    SimpleFallback,
    PartitionAwareFallback,
    CacheFirstFallback,
)
from selfhealing.core.tls_handler import (
    TLSErrorType,
    TLSErrorSeverity,
    TLSErrorInfo,
    TLSErrorClassifier,
    TLSResilientClient,
    SimpleTLSResilientClient,
)
from selfhealing.core.cert_monitor import (
    CertificateStatus,
    CertificateInfo,
    CertificateExpiryMonitor,
    CertificateAlertManager,
)
from selfhealing.core.decision_logger import (
    ReasonCode,
    EventType,
    DecisionLogger,
    log_enter_pre_decision_zone,
    log_intervention_evaluated,
    log_exit_pre_decision_zone,
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
    # Time Provider (Stage 23 - Clock Skew)
    "TimeProvider",
    "SystemTimeProvider",
    "MockTimeProvider",
    "FrozenTime",
    "get_time_provider",
    "set_global_time_provider",
    "reset_time_provider",
    "is_within_clock_skew",
    # Connection Health (Stage 24 - Partial Partition)
    "ConnectionType",
    "ConnectionStatus",
    "ConnectionHealth",
    "PartitionState",
    "ConnectionHealthMonitor",
    "DefaultConnectionHealthMonitor",
    # Fallback Strategy (Stage 24 - Partial Partition)
    "FallbackMode",
    "FallbackResult",
    "FallbackStrategy",
    "SimpleFallback",
    "PartitionAwareFallback",
    "CacheFirstFallback",
    # TLS Handler (Stage 25 - TLS Failure)
    "TLSErrorType",
    "TLSErrorSeverity",
    "TLSErrorInfo",
    "TLSErrorClassifier",
    "TLSResilientClient",
    "SimpleTLSResilientClient",
    # Certificate Monitor (Stage 25 - TLS Failure)
    "CertificateStatus",
    "CertificateInfo",
    "CertificateExpiryMonitor",
    "CertificateAlertManager",
    # Decision Logger (Skeleton - Observability)
    "ReasonCode",
    "EventType",
    "DecisionLogger",
    "log_enter_pre_decision_zone",
    "log_intervention_evaluated",
    "log_exit_pre_decision_zone",
]
