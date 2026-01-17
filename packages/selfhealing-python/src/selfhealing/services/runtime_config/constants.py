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
    # Phase 3
    "l2_storage": L2StorageConfig,
    "chaos": ChaosConfig,
    "approval_requests": None,  # ApprovalRequest는 리스트로 저장
    # Replay Automation
    "replay_automation": ReplayAutomationConfig,
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
