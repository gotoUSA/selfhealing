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

# ForensicContext, ForensicContextBuilder, etc. removed - forensic.py deleted
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
from selfhealing.core.execution_mode import (
    ExecutionModeType,
    ExecutionMode,
    get_execution_mode,
    set_execution_mode,
    clear_execution_mode_override,
)
from selfhealing.core.action_executor import (
    Action,
    ActionResult,
    ActionExecutor,
    get_action_executor,
    execute_action,
)
from selfhealing.core.state_cache import CBStateCache
from selfhealing.core.degraded_mode_handler import DegradedModeHandler
from selfhealing.core.adaptive_jitter import AdaptiveJitter
from selfhealing.core.test_mode_context import (
    TestModeContext,
    is_synthetic_context,
    get_synthetic_session_id,
    synthetic_context,
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
    # Execution Mode (Shadow/Evaluation Mode Support)
    "ExecutionModeType",
    "ExecutionMode",
    "get_execution_mode",
    "set_execution_mode",
    "clear_execution_mode_override",
    # Action Executor (Central Execution Point)
    "Action",
    "ActionResult",
    "ActionExecutor",
    "get_action_executor",
    "execute_action",
    # Platinum SLA Optimization
    "CBStateCache",
    "DegradedModeHandler",
    "AdaptiveJitter",
    # Test Mode Context (X-Test-Mode, Chaos)
    "TestModeContext",
    "is_synthetic_context",
    "get_synthetic_session_id",
    "synthetic_context",
]
