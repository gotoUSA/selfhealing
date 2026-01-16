"""
Pydantic Settings Module for Self-Healing Configuration.

Single Source of Truth for all configuration:
- Default values
- Type definitions
- Validation rules
- Environment variable loading

Replaces:
- core/config.py (dataclass definitions)
- core/safe_defaults.py (SAFE_DEFAULTS, VALIDATION_RULES)

Reference:
- docs/self_healing/middleware_system/40_PYDANTIC_CONFIG_MIGRATION.md
"""

# Phase 1: Core Settings (5)
from selfhealing.settings.circuit_breaker import (
    CircuitBreakerSettings,
    get_circuit_breaker_settings,
    reset_circuit_breaker_settings,
)
from selfhealing.settings.circuit_breaker_advanced import (
    CircuitBreakerAdvancedSettings,
    CircuitBreakerAdvancedConfig,  # Legacy alias
    get_circuit_breaker_advanced_settings,
    reset_circuit_breaker_advanced_settings,
)
from selfhealing.settings.dlq import (
    DLQSettings,
    get_dlq_settings,
    reset_dlq_settings,
)
from selfhealing.settings.retry import (
    RetrySettings,
    get_retry_settings,
    reset_retry_settings,
)
from selfhealing.settings.rate_limit import (
    RateLimitSettings,
    get_rate_limit_settings,
    reset_rate_limit_settings,
)
from selfhealing.settings.security import (
    SecuritySettings,
    get_security_settings,
    reset_security_settings,
)

# Phase 2: Additional Settings (12)
from selfhealing.settings.sla import (
    SLASettings,
    get_sla_settings,
    reset_sla_settings,
)
from selfhealing.settings.slo import (
    SLOSettings,
    get_slo_settings,
    reset_slo_settings,
)
from selfhealing.settings.idempotency import (
    IdempotencySettings,
    get_idempotency_settings,
    reset_idempotency_settings,
)
from selfhealing.settings.forensic import (
    ForensicSettings,
    get_forensic_settings,
    reset_forensic_settings,
)
from selfhealing.settings.logging_config import (
    LoggingSettings,
    get_logging_settings,
    reset_logging_settings,
)
from selfhealing.settings.metrics import (
    MetricsSettings,
    get_metrics_settings,
    reset_metrics_settings,
)
from selfhealing.settings.notification import (
    NotificationSettings,
    get_notification_settings,
    reset_notification_settings,
)
from selfhealing.settings.error_budget import (
    ErrorBudgetSettings,
    get_error_budget_settings,
    reset_error_budget_settings,
)
from selfhealing.settings.governance import (
    GovernanceSettings,
    get_governance_settings,
    reset_governance_settings,
)
from selfhealing.settings.chaos import (
    ChaosSettings,
    get_chaos_settings,
    reset_chaos_settings,
)
from selfhealing.settings.drift_threshold import (
    DriftThresholdSettings,
    get_drift_threshold_settings,
    reset_drift_threshold_settings,
)
from selfhealing.settings.l2_storage import (
    L2StorageSettings,
    get_l2_storage_settings,
    reset_l2_storage_settings,
)
from selfhealing.settings.replay_automation import (
    ReplayAutomationSettings,
    ReplayAutomationConfig,  # Legacy alias
    get_replay_automation_settings,
    reset_replay_automation_settings,
)

# Root Settings (SelfHealingSettings)
from selfhealing.settings.root import (
    SelfHealingSettings,
    SelfHealingConfig,  # Legacy alias
    get_config,
    set_config,
    reset_config,
    reload_config,
    configure,
    # Convenience getters
    get_circuit_breaker_config,
    get_dlq_config,
    get_retry_config,
    get_sla_thresholds,
    get_security_thresholds,
    get_forensic_config,
    get_notification_config,
    get_rate_limit_config,
    # Legacy function aliases
    get_dlq_settings,
    get_retry_settings,
    get_forensic_settings,
    get_notification_settings,
    get_rate_limit_settings,
)

# Re-export get_circuit_breaker_advanced_settings from circuit_breaker_advanced module
from selfhealing.settings.circuit_breaker_advanced import (
    get_circuit_breaker_advanced_settings,
)

# =============================================================================
# Backward Compatibility Aliases
# Legacy dataclass aliases - keeping for external packages
# =============================================================================

CircuitBreakerConfig = CircuitBreakerSettings
DLQConfig = DLQSettings
RetryConfig = RetrySettings
RateLimitConfig = RateLimitSettings
SecurityConfig = SecuritySettings
SLAConfig = SLASettings
IdempotencyConfig = IdempotencySettings
ForensicConfig = ForensicSettings
LoggingConfig = LoggingSettings
MetricsConfig = MetricsSettings
NotificationConfig = NotificationSettings
ErrorBudgetConfig = ErrorBudgetSettings
GovernanceConfig = GovernanceSettings
ChaosConfig = ChaosSettings
DriftThresholdConfig = DriftThresholdSettings
L2StorageConfig = L2StorageSettings

__all__ = [
    # Root Settings
    "SelfHealingSettings",
    "SelfHealingConfig",  # Legacy alias
    "get_config",
    "set_config",
    "reset_config",
    "reload_config",
    "configure",
    # Phase 1: Core Settings (5)
    # Circuit Breaker
    "CircuitBreakerSettings",
    "get_circuit_breaker_settings",
    "reset_circuit_breaker_settings",
    # Circuit Breaker Advanced
    "CircuitBreakerAdvancedSettings",
    "CircuitBreakerAdvancedConfig",  # Legacy alias
    "get_circuit_breaker_advanced_settings",
    "reset_circuit_breaker_advanced_settings",
    # DLQ
    "DLQSettings",
    "get_dlq_settings",
    "reset_dlq_settings",
    # Retry
    "RetrySettings",
    "get_retry_settings",
    "reset_retry_settings",
    # Rate Limit
    "RateLimitSettings",
    "get_rate_limit_settings",
    "reset_rate_limit_settings",
    # Security
    "SecuritySettings",
    "get_security_settings",
    "reset_security_settings",
    # Phase 2: Additional Settings (12)
    # SLA
    "SLASettings",
    "get_sla_settings",
    "reset_sla_settings",
    # SLO
    "SLOSettings",
    "get_slo_settings",
    "reset_slo_settings",
    # Idempotency
    "IdempotencySettings",
    "get_idempotency_settings",
    "reset_idempotency_settings",
    # Forensic
    "ForensicSettings",
    "get_forensic_settings",
    "reset_forensic_settings",
    # Logging
    "LoggingSettings",
    "get_logging_settings",
    "reset_logging_settings",
    # Metrics
    "MetricsSettings",
    "get_metrics_settings",
    "reset_metrics_settings",
    # Notification
    "NotificationSettings",
    "get_notification_settings",
    "reset_notification_settings",
    # Error Budget
    "ErrorBudgetSettings",
    "get_error_budget_settings",
    "reset_error_budget_settings",
    # Governance
    "GovernanceSettings",
    "get_governance_settings",
    "reset_governance_settings",
    # Chaos
    "ChaosSettings",
    "get_chaos_settings",
    "reset_chaos_settings",
    # Drift Threshold
    "DriftThresholdSettings",
    "get_drift_threshold_settings",
    "reset_drift_threshold_settings",
    # L2 Storage
    "L2StorageSettings",
    "get_l2_storage_settings",
    "reset_l2_storage_settings",
    # Replay Automation
    "ReplayAutomationSettings",
    "ReplayAutomationConfig",  # Legacy alias
    "get_replay_automation_settings",
    "reset_replay_automation_settings",
    # Legacy aliases (deprecated)
    "CircuitBreakerConfig",
    "DLQConfig",
    "RetryConfig",
    "RateLimitConfig",
    "SecurityConfig",
    "SLAConfig",
    "IdempotencyConfig",
    "ForensicConfig",
    "LoggingConfig",
    "MetricsConfig",
    "NotificationConfig",
    "ErrorBudgetConfig",
    "GovernanceConfig",
    "ChaosConfig",
    "DriftThresholdConfig",
    "L2StorageConfig",
]
