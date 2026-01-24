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
"""

# 핵심 설정 (5)
from selfhealing.settings.circuit_breaker import (
    CircuitBreakerSettings,
    get_circuit_breaker_settings,
    reset_circuit_breaker_settings,
)
from selfhealing.settings.circuit_breaker_advanced import (
    CircuitBreakerAdvancedSettings,
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

# 확장 설정 (12)
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
    get_replay_automation_settings,
    reset_replay_automation_settings,
)

# Week 1 CRITICAL Settings (92_CONFIG_IMPLEMENTATION_GUIDE.md)
from selfhealing.settings.recovery_circuit_breaker import (
    RecoveryCircuitBreakerSettings,
    get_recovery_circuit_breaker_settings,
    reset_recovery_circuit_breaker_settings,
)
from selfhealing.settings.redis_key_guard import (
    RedisKeyGuardSettings,
    get_redis_key_guard_settings,
    reset_redis_key_guard_settings,
)
from selfhealing.settings.recovery_shutdown import (
    RecoveryShutdownSettings,
    get_recovery_shutdown_settings,
    reset_recovery_shutdown_settings,
)
from selfhealing.settings.resilient_recorder import (
    ResilientRecorderSettings,
    get_resilient_recorder_settings,
    reset_resilient_recorder_settings,
)

# Week 2 HIGH Settings (92_CONFIG_IMPLEMENTATION_GUIDE.md)
from selfhealing.settings.error_budget_propagation import (
    ErrorBudgetPropagationSettings,
    get_error_budget_propagation_settings,
    reset_error_budget_propagation_settings,
)
from selfhealing.settings.anti_flapping import (
    AntiFlappingSettings,
    get_anti_flapping_settings,
    reset_anti_flapping_settings,
)
from selfhealing.settings.throttle import (
    ThrottleSettings,
    get_throttle_settings,
    reset_throttle_settings,
)
from selfhealing.settings.critical_worker import (
    CriticalWorkerSettings,
    get_critical_worker_settings,
    reset_critical_worker_settings,
)

# 고급 기능
from selfhealing.settings.layered_provider import (
    get_layered_settings,
    set_request_override,
    get_request_override,
    clear_request_overrides,
    get_all_request_overrides,
    detect_config_source,
    get_config_with_sources,
    RequestOverrideContext,
    get_circuit_breaker_layered,
    get_retry_layered,
    get_dlq_layered,
    get_rate_limit_layered,
)
from selfhealing.settings.secrets import (
    SecretsSettings,
    get_secrets,
    reset_secrets,
)

# Root Settings (SelfHealingSettings)
from selfhealing.settings.root import (
    SelfHealingSettings,
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

__all__ = [
    # Root Settings
    "SelfHealingSettings",
    "get_config",
    "set_config",
    "reset_config",
    "reload_config",
    "configure",
    # 핵심 설정 (5)
    # Circuit Breaker
    "CircuitBreakerSettings",
    "get_circuit_breaker_settings",
    "reset_circuit_breaker_settings",
    # Circuit Breaker Advanced
    "CircuitBreakerAdvancedSettings",
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
    # 확장 설정 (12)
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
    "get_replay_automation_settings",
    "reset_replay_automation_settings",
    # 계층형 Provider
    "get_layered_settings",
    "set_request_override",
    "get_request_override",
    "clear_request_overrides",
    "get_all_request_overrides",
    "detect_config_source",
    "get_config_with_sources",
    "RequestOverrideContext",
    "get_circuit_breaker_layered",
    "get_retry_layered",
    "get_dlq_layered",
    "get_rate_limit_layered",
    # 보안 설정
    "SecretsSettings",
    "get_secrets",
    "reset_secrets",
    # Week 1 CRITICAL Settings (92_CONFIG_IMPLEMENTATION_GUIDE.md)
    # Recovery Circuit Breaker
    "RecoveryCircuitBreakerSettings",
    "get_recovery_circuit_breaker_settings",
    "reset_recovery_circuit_breaker_settings",
    # Redis Key Guard
    "RedisKeyGuardSettings",
    "get_redis_key_guard_settings",
    "reset_redis_key_guard_settings",
    # Recovery Shutdown
    "RecoveryShutdownSettings",
    "get_recovery_shutdown_settings",
    "reset_recovery_shutdown_settings",
    # Resilient Recorder
    "ResilientRecorderSettings",
    "get_resilient_recorder_settings",
    "reset_resilient_recorder_settings",
    # Week 2 HIGH Settings (92_CONFIG_IMPLEMENTATION_GUIDE.md)
    # Error Budget Propagation
    "ErrorBudgetPropagationSettings",
    "get_error_budget_propagation_settings",
    "reset_error_budget_propagation_settings",
    # Anti-Flapping
    "AntiFlappingSettings",
    "get_anti_flapping_settings",
    "reset_anti_flapping_settings",
    # Throttle
    "ThrottleSettings",
    "get_throttle_settings",
    "reset_throttle_settings",
    # Critical Worker
    "CriticalWorkerSettings",
    "get_critical_worker_settings",
    "reset_critical_worker_settings",
]
