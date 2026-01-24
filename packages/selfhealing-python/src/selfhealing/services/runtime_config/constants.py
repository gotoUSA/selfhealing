"""
Runtime Configuration Constants.

Storage keys and config class mappings for all configuration types.
"""

from selfhealing.settings import (
    CircuitBreakerSettings,
    DLQSettings,
    RetrySettings,
    SLASettings,
    RateLimitSettings,
    SecuritySettings,
    IdempotencySettings,
    NotificationSettings,
    ForensicSettings,
    LoggingSettings,
    MetricsSettings,
    ErrorBudgetSettings,
    GovernanceSettings,
    DriftThresholdSettings,
    L2StorageSettings,
    ChaosSettings,
    ReplayAutomationSettings,
    # Week 1 CRITICAL Settings (92_CONFIG_IMPLEMENTATION_GUIDE.md)
    RecoveryCircuitBreakerSettings,
    RedisKeyGuardSettings,
    RecoveryShutdownSettings,
    ResilientRecorderSettings,
    # Week 2 HIGH Settings (92_CONFIG_IMPLEMENTATION_GUIDE.md)
    ErrorBudgetPropagationSettings,
    AntiFlappingSettings,
    ThrottleSettings,
    CriticalWorkerSettings,
    # Week 3 MEDIUM Settings (92_CONFIG_IMPLEMENTATION_GUIDE.md)
    ChaosExperimentSettings,
    ChaosBlastRadiusSettings,
    CorruptionShieldSettings,
    NotificationChannelSettings,
    CascadeRetentionSettings,
    DistributedLockSettings,
    # Week 4 LOW Settings (92_CONFIG_IMPLEMENTATION_GUIDE.md)
    DashboardSettings,
    BatchSettings,
    AuditSettings,
    CeleryTaskSettings,
    ApiViewSettings,
    DomainSensitivitySettings,
    SlackChannelSettings,
    AuditIntegritySettings,
    RegionalRecoveryPolicySettings,
)

# Legacy aliases for backward compatibility
CircuitBreakerConfig = CircuitBreakerSettings
DLQConfig = DLQSettings
RetryConfig = RetrySettings
SLAConfig = SLASettings
RateLimitConfig = RateLimitSettings
SecurityConfig = SecuritySettings
IdempotencyConfig = IdempotencySettings
NotificationConfig = NotificationSettings
ForensicConfig = ForensicSettings
LoggingConfig = LoggingSettings
MetricsConfig = MetricsSettings
ErrorBudgetConfig = ErrorBudgetSettings
GovernanceConfig = GovernanceSettings
DriftThresholdConfig = DriftThresholdSettings
L2StorageConfig = L2StorageSettings
ChaosConfig = ChaosSettings
ReplayAutomationConfig = ReplayAutomationSettings
# Week 1 CRITICAL Settings aliases
RecoveryCircuitBreakerConfig = RecoveryCircuitBreakerSettings
RedisKeyGuardConfig = RedisKeyGuardSettings
RecoveryShutdownConfig = RecoveryShutdownSettings
ResilientRecorderConfig = ResilientRecorderSettings
# Week 2 HIGH Settings aliases
ErrorBudgetPropagationConfig = ErrorBudgetPropagationSettings
AntiFlappingConfig = AntiFlappingSettings
ThrottleConfig = ThrottleSettings
CriticalWorkerConfig = CriticalWorkerSettings
# Week 3 MEDIUM Settings aliases
ChaosExperimentConfig = ChaosExperimentSettings
ChaosBlastRadiusConfig = ChaosBlastRadiusSettings
CorruptionShieldConfig = CorruptionShieldSettings
NotificationChannelConfig = NotificationChannelSettings
CascadeRetentionConfig = CascadeRetentionSettings
DistributedLockConfig = DistributedLockSettings
# Week 4 LOW Settings aliases (92_CONFIG_IMPLEMENTATION_GUIDE.md)
DashboardConfig = DashboardSettings
BatchConfig = BatchSettings
AuditConfig = AuditSettings
CeleryTaskConfig = CeleryTaskSettings
ApiViewConfig = ApiViewSettings
DomainSensitivityConfig = DomainSensitivitySettings
SlackChannelConfig = SlackChannelSettings
AuditIntegrityConfig = AuditIntegritySettings
RegionalRecoveryPolicyConfig = RegionalRecoveryPolicySettings

# Storage keys for each config type
STORAGE_KEYS = {
    "circuit_breaker": "runtime_config:circuit_breaker",
    "dlq": "runtime_config:dlq",
    "retry": "runtime_config:retry",
    "sla": "runtime_config:sla",
    "rate_limit": "runtime_config:rate_limit",
    "security": "runtime_config:security",
    "idempotency": "runtime_config:idempotency",
    "notification": "runtime_config:notification",
    "forensic": "runtime_config:forensic",
    "logging": "runtime_config:logging",
    "metrics": "runtime_config:metrics",
    "error_budget": "runtime_config:error_budget",
    "slo": "runtime_config:slo",
    "governance": "runtime_config:governance",
    "drift_threshold": "runtime_config:drift_threshold",
    # L2 Storage, Chaos, 4-Eyes Approval
    "l2_storage": "runtime_config:l2_storage",
    "chaos": "runtime_config:chaos",
    "approval_requests": "runtime_config:approval_requests",
    # Replay Automation
    "replay_automation": "runtime_config:replay_automation",
    # Week 1 CRITICAL Settings (92_CONFIG_IMPLEMENTATION_GUIDE.md)
    "recovery_circuit_breaker": "runtime_config:recovery_circuit_breaker",
    "redis_key_guard": "runtime_config:redis_key_guard",
    "recovery_shutdown": "runtime_config:recovery_shutdown",
    "resilient_recorder": "runtime_config:resilient_recorder",
    # Week 2 HIGH Settings (92_CONFIG_IMPLEMENTATION_GUIDE.md)
    "error_budget_propagation": "runtime_config:error_budget_propagation",
    "anti_flapping": "runtime_config:anti_flapping",
    "throttle": "runtime_config:throttle",
    "critical_worker": "runtime_config:critical_worker",
    # Week 3 MEDIUM Settings (92_CONFIG_IMPLEMENTATION_GUIDE.md)
    "chaos_experiment": "runtime_config:chaos_experiment",
    "chaos_blast_radius": "runtime_config:chaos_blast_radius",
    "corruption_shield": "runtime_config:corruption_shield",
    "notification_channel": "runtime_config:notification_channel",
    "cascade_retention": "runtime_config:cascade_retention",
    "distributed_lock": "runtime_config:distributed_lock",
    # Week 4 LOW Settings (92_CONFIG_IMPLEMENTATION_GUIDE.md)
    "dashboard": "runtime_config:dashboard",
    "batch": "runtime_config:batch",
    "audit": "runtime_config:audit",
    "celery_task": "runtime_config:celery_task",
    "api_view": "runtime_config:api_view",
    "domain_sensitivity": "runtime_config:domain_sensitivity",
    "slack_channel": "runtime_config:slack_channel",
    "audit_integrity": "runtime_config:audit_integrity",
    "regional_recovery_policy": "runtime_config:regional_recovery_policy",
}

# Default config classes
CONFIG_CLASSES = {
    "circuit_breaker": CircuitBreakerConfig,
    "dlq": DLQConfig,
    "retry": RetryConfig,
    "sla": SLAConfig,
    "rate_limit": RateLimitConfig,
    "security": SecurityConfig,
    "idempotency": IdempotencyConfig,
    "notification": NotificationConfig,
    "forensic": ForensicConfig,
    "logging": LoggingConfig,
    "metrics": MetricsConfig,
    "error_budget": ErrorBudgetConfig,
    "slo": None,  # SLO는 별도 처리 (SLOConfigRuntime)
    "governance": GovernanceConfig,
    "drift_threshold": DriftThresholdConfig,
    "l2_storage": L2StorageConfig,
    "chaos": ChaosConfig,
    "approval_requests": None,  # ApprovalRequest는 리스트로 저장
    # Replay Automation
    "replay_automation": ReplayAutomationConfig,
    # Week 1 CRITICAL Settings (92_CONFIG_IMPLEMENTATION_GUIDE.md)
    "recovery_circuit_breaker": RecoveryCircuitBreakerConfig,
    "redis_key_guard": RedisKeyGuardConfig,
    "recovery_shutdown": RecoveryShutdownConfig,
    "resilient_recorder": ResilientRecorderConfig,
    # Week 2 HIGH Settings (92_CONFIG_IMPLEMENTATION_GUIDE.md)
    "error_budget_propagation": ErrorBudgetPropagationConfig,
    "anti_flapping": AntiFlappingConfig,
    "throttle": ThrottleConfig,
    "critical_worker": CriticalWorkerConfig,
    # Week 3 MEDIUM Settings (92_CONFIG_IMPLEMENTATION_GUIDE.md)
    "chaos_experiment": ChaosExperimentConfig,
    "chaos_blast_radius": ChaosBlastRadiusConfig,
    "corruption_shield": CorruptionShieldConfig,
    "notification_channel": NotificationChannelConfig,
    "cascade_retention": CascadeRetentionConfig,
    "distributed_lock": DistributedLockConfig,
    # Week 4 LOW Settings (92_CONFIG_IMPLEMENTATION_GUIDE.md)
    "dashboard": DashboardConfig,
    "batch": BatchConfig,
    "audit": AuditConfig,
    "celery_task": CeleryTaskConfig,
    "api_view": ApiViewConfig,
    "domain_sensitivity": DomainSensitivityConfig,
    "slack_channel": SlackChannelConfig,
    "audit_integrity": AuditIntegrityConfig,
    "regional_recovery_policy": RegionalRecoveryPolicyConfig,
}

# Default SLO configuration
DEFAULT_SLO_CONFIG = {
    "default_window_days": 30,
    "default_target": 0.999,
    "default_fast_burn_rate": 14.4,
    "default_slow_burn_rate": 3.0,
    "slos": [
        {
            "name": "availability",
            "sli_type": "availability",
            "target": 0.999,
            "window_days": 30,
            "description": "API availability - proportion of successful requests",
            "service_name": "",
            "domain": "",
            "warning_threshold": None,
            "critical_threshold": None,
            "fast_burn_rate": 14.4,
            "slow_burn_rate": 3.0,
        },
        {
            "name": "latency_p99",
            "sli_type": "latency_p99",
            "target": 0.500,
            "window_days": 7,
            "description": "99th percentile response time should be under 500ms",
            "service_name": "",
            "domain": "",
            "warning_threshold": None,
            "critical_threshold": None,
            "fast_burn_rate": 14.4,
            "slow_burn_rate": 3.0,
        },
        {
            "name": "error_rate",
            "sli_type": "error_rate",
            "target": 0.999,
            "window_days": 30,
            "description": "Error rate should be under 0.1%",
            "service_name": "",
            "domain": "",
            "warning_threshold": None,
            "critical_threshold": None,
            "fast_burn_rate": 14.4,
            "slow_burn_rate": 3.0,
        },
    ],
}
