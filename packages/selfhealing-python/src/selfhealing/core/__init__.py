"""
Core module - Framework-agnostic business logic

This module contains pure Python implementations without any framework dependencies.

Backoff API:
    - BackoffCalculator: Simple config-based calculator (legacy interface)
      Usage: calc = BackoffCalculator(LegacyBackoffConfig()); calc.calculate(attempt)

    - ExponentialBackoff, LinearBackoff, etc.: Strategy pattern implementations
      Usage: strategy = ExponentialBackoff(base=2); strategy.calculate(attempt)
"""

from selfhealing.core.action_executor import (
    Action,
    ActionExecutor,
    ActionResult,
    execute_action,
    get_action_executor,
)
from selfhealing.core.adaptive_jitter import AdaptiveJitter
from selfhealing.core.backoff import (
    BackoffStrategy,
    ConstantBackoff,
    DecorrelatedJitterBackoff,
    ExponentialBackoff,
    LegacyBackoffConfig,
    LinearBackoff,
    calculate_backoff,
    get_backoff_calculator,
)
from selfhealing.core.backoff import (
    LegacyBackoffCalculator as BackoffCalculator,  # Strategy pattern implementations (advanced); Simple config-based interface (recommended for most use cases); Config-based calculator
)
from selfhealing.core.cert_monitor import (
    CertificateAlertManager,
    CertificateExpiryMonitor,
    CertificateInfo,
    CertificateStatus,
)
from selfhealing.core.connection_health import (
    ConnectionHealth,
    ConnectionHealthMonitor,
    ConnectionStatus,
    ConnectionType,
    DefaultConnectionHealthMonitor,
    PartitionState,
)
from selfhealing.core.decision_logger import (
    DecisionBoundaryEventType,
    DecisionLogger,
    ReasonCode,
    log_enter_pre_decision_zone,
    log_exit_pre_decision_zone,
    log_intervention_evaluated,
)
from selfhealing.core.degraded_mode_handler import DegradedModeHandler
from selfhealing.core.execution_mode import (
    ExecutionMode,
    ExecutionModeType,
    clear_execution_mode_override,
    get_execution_mode,
    set_execution_mode,
)
from selfhealing.core.fallback_strategy import (
    CacheFirstFallback,
    FallbackMode,
    FallbackResult,
    FallbackStrategy,
    PartitionAwareFallback,
    SimpleFallback,
)

# ForensicContext, ForensicContextBuilder, etc. removed - forensic.py deleted
from selfhealing.core.pool_monitor import (
    ConnectionInfo,
    ConnectionPoolMonitor,
    LeakReport,
    PoolHealthStatus,
    PoolStats,
    PoolStatsProvider,
)
from selfhealing.core.pool_watchdog import (
    PoolRecoveryAction,
    PoolRecoveryHandler,
    PoolRecoveryResult,
    PoolWatchdog,
)
from selfhealing.core.request_context import (
    RequestLifecycleContext,
    track_request,
)
from selfhealing.core.shutdown_coordinator import (
    GracefulShutdownCoordinator,
    RequestState,
    RequestTracker,
    ShutdownHandler,
    ShutdownPhase,
    ShutdownStats,
    TrackedRequest,
)
from selfhealing.core.state_cache import CBStateCache
from selfhealing.core.test_mode_context import (
    TestModeContext,
    get_synthetic_session_id,
    is_synthetic_context,
    synthetic_context,
)
from selfhealing.core.time_provider import (
    FrozenTime,
    MockTimeProvider,
    SystemTimeProvider,
    TimeProvider,
    get_time_provider,
    is_within_clock_skew,
    reset_time_provider,
)
from selfhealing.core.time_provider import set_time_provider as set_global_time_provider
from selfhealing.core.tls_handler import (
    SimpleTLSResilientClient,
    TLSErrorClassifier,
    TLSErrorInfo,
    TLSErrorSeverity,
    TLSErrorType,
    TLSResilientClient,
)
from selfhealing.interfaces.repositories import (
    CircuitBreakerStateData,
    FailedOperationData,
)
from selfhealing.interfaces.repositories import (
    CircuitBreakerStateEnum as CircuitState,
)

__all__ = [
    # Types
    "CircuitState",
    "FailedOperationData",
    "CircuitBreakerStateData",
    # Backoff - Strategy implementations
    "BackoffStrategy",  # ABC for all backoff strategies
    "ExponentialBackoff",
    "LinearBackoff",
    "ConstantBackoff",
    "DecorrelatedJitterBackoff",
    "get_backoff_calculator",
    # Backoff - Simple config-based interface
    "LegacyBackoffConfig",
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
    "PoolRecoveryAction",
    "PoolRecoveryResult",
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
    "RequestLifecycleContext",
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
    "DecisionBoundaryEventType",
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
