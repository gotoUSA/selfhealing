"""
Core module - Framework-agnostic business logic

This module contains pure Python implementations without any framework dependencies.
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
    BackoffCalculator,
    ExponentialBackoff,
    LinearBackoff,
    ConstantBackoff,
    DecorrelatedJitterBackoff,
    get_backoff_calculator,
)
from selfhealing.core.config import (
    SelfHealingConfig,
    CircuitBreakerConfig,
    DLQConfig,
    RetryConfig,
    SLAConfig,
    IdempotencyConfig,
    SecurityConfig,
    ForensicConfig,
    MetricsConfig,
    NotificationConfig,
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
    # Backoff
    "BackoffCalculator",
    "ExponentialBackoff",
    "LinearBackoff",
    "ConstantBackoff",
    "DecorrelatedJitterBackoff",
    "get_backoff_calculator",
    # Config
    "SelfHealingConfig",
    "CircuitBreakerConfig",
    "DLQConfig",
    "RetryConfig",
    "SLAConfig",
    "IdempotencyConfig",
    "SecurityConfig",
    "ForensicConfig",
    "MetricsConfig",
    "NotificationConfig",
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
]
