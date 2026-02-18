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

from selfhealing.settings.admission_control import (
    AdmissionControlSettings,
    get_admission_control_settings,
    reset_admission_control_settings,
)
from selfhealing.settings.anti_flapping import (
    AntiFlappingSettings,
    get_anti_flapping_settings,
    reset_anti_flapping_settings,
)

# API Rate Limit (106_HARDCODED_CONFIG_API_REFACTORING.md Step 1)
from selfhealing.settings.api_rate_limit import (
    ApiRateLimitSettings,
    get_api_rate_limit_settings,
    reset_api_rate_limit_settings,
)
from selfhealing.settings.api_view import (
    ApiViewSettings,
    get_api_view_settings,
    reset_api_view_settings,
)
from selfhealing.settings.apply_strategy import (
    ApplyStrategySettings,
    get_apply_strategy_settings,
    reset_apply_strategy_settings,
)
from selfhealing.settings.audit_integrity import (
    AuditIntegritySettings,
    get_audit_integrity_settings,
    reset_audit_integrity_settings,
)
from selfhealing.settings.audit_settings import (
    AuditSettings,
    get_audit_settings,
    reset_audit_settings,
)
from selfhealing.settings.audit_sync import (
    AuditSyncSettings,
    get_audit_sync_settings,
    reset_audit_sync_settings,
)
from selfhealing.settings.audit_watchdog import (
    AuditWatchdogSettings,
    get_audit_watchdog_settings,
    reset_audit_watchdog_settings,
)
from selfhealing.settings.auto_rollback import (
    AutoRollbackSettings,
    get_auto_rollback_settings,
    reset_auto_rollback_settings,
)
from selfhealing.settings.backpressure import (
    LEVEL_RATE_MULTIPLIERS,
    BackpressureLevel,
    BackpressureSettings,
    BackpressureStrategy,
    get_backpressure_settings,
    reset_backpressure_settings,
)
from selfhealing.settings.batch import (
    BatchSettings,
    get_batch_settings,
    reset_batch_settings,
)
from selfhealing.settings.cascade_retention import (
    CascadeRetentionSettings,
    get_cascade_retention_settings,
    reset_cascade_retention_settings,
)
from selfhealing.settings.celery_task import (
    CeleryTaskSettings,
    get_celery_task_settings,
    reset_celery_task_settings,
)
from selfhealing.settings.chaos import (
    ChaosSettings,
    get_chaos_settings,
    reset_chaos_settings,
)
from selfhealing.settings.chaos_blast_radius import (
    ChaosBlastRadiusSettings,
    get_chaos_blast_radius_settings,
    reset_chaos_blast_radius_settings,
)

# Week 3 MEDIUM Settings (92_CONFIG_IMPLEMENTATION_GUIDE.md)
from selfhealing.settings.chaos_experiment import (
    ChaosExperimentSettings,
    get_chaos_experiment_settings,
    reset_chaos_experiment_settings,
)

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

# Cleanup Task Settings (108_HARDCODED_CONFIG_REFACTORING_PART1_CELERY_TASKS.md)
from selfhealing.settings.cleanup import (
    CleanupSettings,
    get_cleanup_settings,
    reset_cleanup_settings,
)
from selfhealing.settings.corruption_shield import (
    CorruptionShieldSettings,
    get_corruption_shield_settings,
    reset_corruption_shield_settings,
)
from selfhealing.settings.critical_worker import (
    CriticalWorkerSettings,
    DeploymentEnvironment,
    get_critical_worker_settings,
    reset_critical_worker_settings,
)

# Daily Report Task Settings (108_HARDCODED_CONFIG_REFACTORING_PART1_CELERY_TASKS.md)
from selfhealing.settings.daily_report import (
    DailyReportSettings,
    get_daily_report_settings,
    reset_daily_report_settings,
)

# Week 4 LOW Settings (92_CONFIG_IMPLEMENTATION_GUIDE.md)
from selfhealing.settings.dashboard import (
    DashboardSettings,
    get_dashboard_settings,
    reset_dashboard_settings,
)
from selfhealing.settings.decision_engine import (
    DecisionEngineSettings,
    get_decision_engine_settings,
    reset_decision_engine_settings,
)
from selfhealing.settings.distributed_lock import (
    DistributedLockSettings,
    get_distributed_lock_settings,
    reset_distributed_lock_settings,
)
from selfhealing.settings.dlq import (
    DLQSettings,
    get_dlq_settings,
    reset_dlq_settings,
)
from selfhealing.settings.domain_sensitivity import (
    DomainSensitivitySettings,
    get_domain_sensitivity_settings,
    reset_domain_sensitivity_settings,
)
from selfhealing.settings.drift_threshold import (
    DriftThresholdSettings,
    get_drift_threshold_settings,
    reset_drift_threshold_settings,
)
from selfhealing.settings.error_budget import (
    ErrorBudgetSettings,
    get_error_budget_settings,
    reset_error_budget_settings,
)
from selfhealing.settings.error_budget_gate import (
    ErrorBudgetGateSettings,
    get_error_budget_gate_settings,
    reset_error_budget_gate_settings,
)

# Event Buffer Settings (169_SETTINGS_SCALE_LIMITS.md)
from selfhealing.settings.event_buffer import (
    EventBufferSettings,
    get_event_buffer_settings,
    reset_event_buffer_settings,
)

# Week 2 HIGH Settings (92_CONFIG_IMPLEMENTATION_GUIDE.md)
from selfhealing.settings.error_budget_propagation import (
    ErrorBudgetPropagationSettings,
    get_error_budget_propagation_settings,
    reset_error_budget_propagation_settings,
)
from selfhealing.settings.forensic import (
    ForensicSettings,
    get_forensic_settings,
    reset_forensic_settings,
)
from selfhealing.settings.governance import (
    GovernanceSettings,
    get_governance_settings,
    reset_governance_settings,
)

# Audit Module Settings (105_HARDCODED_CONFIG_AUDIT_REFACTORING.md Step 2)
from selfhealing.settings.hash_chain import (
    HashChainSettings,
    get_hash_chain_settings,
    reset_hash_chain_settings,
)
from selfhealing.settings.idempotency import (
    IdempotencySettings,
    get_idempotency_settings,
    reset_idempotency_settings,
)
from selfhealing.settings.l2_storage import (
    L2StorageSettings,
    get_l2_storage_settings,
    reset_l2_storage_settings,
)
from selfhealing.settings.leader_election import (
    LeaderElectionSettings,
    get_leader_election_settings,
    reset_leader_election_settings,
)

# 고급 기능
from selfhealing.settings.layered_provider import (
    RequestOverrideContext,
    clear_request_overrides,
    detect_config_source,
    get_all_request_overrides,
    get_circuit_breaker_layered,
    get_config_with_sources,
    get_dlq_layered,
    get_layered_settings,
    get_rate_limit_layered,
    get_request_override,
    get_retry_layered,
    set_request_override,
)
from selfhealing.settings.logging_config import (
    LoggingSettings,
    get_logging_settings,
    reset_logging_settings,
)
from selfhealing.settings.meta_watchdog import (
    MetaWatchdogSettings,
    get_meta_watchdog_settings,
    reset_meta_watchdog_settings,
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
from selfhealing.settings.notification_channel import (
    NotificationChannelSettings,
    get_notification_channel_settings,
    reset_notification_channel_settings,
)
from selfhealing.settings.rate_limit import (
    RateLimitSettings,
    get_rate_limit_settings,
    reset_rate_limit_settings,
)

# Week 1 CRITICAL Settings (92_CONFIG_IMPLEMENTATION_GUIDE.md)
from selfhealing.settings.recovery_circuit_breaker import (
    RecoveryCircuitBreakerSettings,
    get_recovery_circuit_breaker_settings,
    reset_recovery_circuit_breaker_settings,
)
from selfhealing.settings.recovery_coordinator import (
    RecoveryCoordinatorSettings,
    get_recovery_coordinator_settings,
    reset_recovery_coordinator_settings,
)
from selfhealing.settings.recovery_shutdown import (
    RecoveryShutdownSettings,
    get_recovery_shutdown_settings,
    reset_recovery_shutdown_settings,
)

# Coordination Settings (104_HARDCODED_CONFIG_COORDINATION_REFACTORING.md Step 1)
from selfhealing.settings.recovery_tasks import (
    RecoveryTasksSettings,
    get_recovery_tasks_settings,
    reset_recovery_tasks_settings,
)
from selfhealing.settings.redis_key_guard import (
    RedisKeyGuardSettings,
    get_redis_key_guard_settings,
    reset_redis_key_guard_settings,
)
from selfhealing.settings.regional_recovery_policy import (
    RegionalRecoveryPolicySettings,
    get_regional_recovery_policy_settings,
    reset_regional_recovery_policy_settings,
)
from selfhealing.settings.replay_automation import (
    ReplayAutomationSettings,
    get_replay_automation_settings,
    reset_replay_automation_settings,
)
from selfhealing.settings.resilient_recorder import (
    ResilientRecorderSettings,
    get_resilient_recorder_settings,
    reset_resilient_recorder_settings,
)

# X-Test Resource Guard Settings (143_XTEST_RESOURCE_AWARE_INTERLOCK.md)
from selfhealing.settings.resource_guard import (
    ResourceGuardSettings,
    get_resource_guard_settings,
    reset_resource_guard_settings,
)
from selfhealing.settings.retry import (
    RetrySettings,
    get_retry_settings,
    reset_retry_settings,
)

# Root Settings (SelfHealingSettings)
from selfhealing.settings.root import (  # Convenience getters; Legacy function aliases
    SelfHealingSettings,
    configure,
    get_circuit_breaker_config,
    get_config,
    get_dlq_config,
    get_dlq_settings,
    get_forensic_config,
    get_forensic_settings,
    get_notification_config,
    get_notification_settings,
    get_rate_limit_config,
    get_rate_limit_settings,
    get_retry_config,
    get_retry_settings,
    get_security_thresholds,
    get_sla_thresholds,
    reload_config,
    reset_config,
    set_config,
)

# Core Module Settings (103_HARDCODED_CONFIG_CORE_REFACTORING.md Step 1, 2)
from selfhealing.settings.runtime_feedback import (
    RuntimeFeedbackSettings,
    get_runtime_feedback_settings,
    reset_runtime_feedback_settings,
)
from selfhealing.settings.safety_bounds import (
    ParameterBoundConfig,
    SafetyBoundsSettings,
    get_safety_bounds_settings,
    reset_safety_bounds_settings,
)
from selfhealing.settings.secrets import (
    SecretsSettings,
    get_secrets,
    reset_secrets,
)
from selfhealing.settings.security import (
    SecuritySettings,
    get_security_settings,
    reset_security_settings,
)

# Enterprise Scale Settings (169_SETTINGS_SCALE_LIMITS.md)
from selfhealing.settings.scale import (
    PROFILE_DEFAULTS,
    ScaleProfile,
    ScaleSettings,
    get_scale_settings,
    reset_scale_settings,
)

# 확장 설정 (12)
from selfhealing.settings.sla import (
    SLASettings,
    get_sla_settings,
    reset_sla_settings,
)
from selfhealing.settings.slack_channel import (
    SlackChannelSettings,
    get_slack_channel_settings,
    reset_slack_channel_settings,
)
from selfhealing.settings.slo import (
    SLOSettings,
    get_slo_settings,
    reset_slo_settings,
)
from selfhealing.settings.state_cache import (
    StateCacheSettings,
    get_state_cache_settings,
    reset_state_cache_settings,
)
from selfhealing.settings.throttle import (
    ThrottleSettings,
    get_throttle_settings,
    reset_throttle_settings,
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
    # Week 3 MEDIUM Settings (92_CONFIG_IMPLEMENTATION_GUIDE.md)
    # Chaos Experiment
    "ChaosExperimentSettings",
    "get_chaos_experiment_settings",
    "reset_chaos_experiment_settings",
    # Chaos Blast Radius
    "ChaosBlastRadiusSettings",
    "get_chaos_blast_radius_settings",
    "reset_chaos_blast_radius_settings",
    # Corruption Shield
    "CorruptionShieldSettings",
    "get_corruption_shield_settings",
    "reset_corruption_shield_settings",
    # Notification Channel
    "NotificationChannelSettings",
    "get_notification_channel_settings",
    "reset_notification_channel_settings",
    # Cascade Retention
    "CascadeRetentionSettings",
    "get_cascade_retention_settings",
    "reset_cascade_retention_settings",
    # Distributed Lock
    "DistributedLockSettings",
    "get_distributed_lock_settings",
    "reset_distributed_lock_settings",
    # Week 4 LOW Settings (92_CONFIG_IMPLEMENTATION_GUIDE.md)
    # Dashboard
    "DashboardSettings",
    "get_dashboard_settings",
    "reset_dashboard_settings",
    # Batch
    "BatchSettings",
    "get_batch_settings",
    "reset_batch_settings",
    # Audit
    "AuditSettings",
    "get_audit_settings",
    "reset_audit_settings",
    # Celery Task
    "CeleryTaskSettings",
    "get_celery_task_settings",
    "reset_celery_task_settings",
    # API View
    "ApiViewSettings",
    "get_api_view_settings",
    "reset_api_view_settings",
    # Domain Sensitivity
    "DomainSensitivitySettings",
    "get_domain_sensitivity_settings",
    "reset_domain_sensitivity_settings",
    # Slack Channel
    "SlackChannelSettings",
    "get_slack_channel_settings",
    "reset_slack_channel_settings",
    # Audit Integrity
    "AuditIntegritySettings",
    "get_audit_integrity_settings",
    "reset_audit_integrity_settings",
    # Audit Sync
    "AuditSyncSettings",
    "get_audit_sync_settings",
    "reset_audit_sync_settings",
    # Audit Watchdog
    "AuditWatchdogSettings",
    "get_audit_watchdog_settings",
    "reset_audit_watchdog_settings",
    # Regional Recovery Policy
    "RegionalRecoveryPolicySettings",
    "get_regional_recovery_policy_settings",
    "reset_regional_recovery_policy_settings",
    # Coordination Settings (104_HARDCODED_CONFIG_COORDINATION_REFACTORING.md)
    # Recovery Tasks
    "RecoveryTasksSettings",
    "get_recovery_tasks_settings",
    "reset_recovery_tasks_settings",
    # Recovery Coordinator
    "RecoveryCoordinatorSettings",
    "get_recovery_coordinator_settings",
    "reset_recovery_coordinator_settings",
    # Deployment Environment (Critical Worker)
    "DeploymentEnvironment",
    # Core Module Settings (Runtime Feedback, Auto Rollback, Safety Bounds, etc.)
    # Runtime Feedback
    "RuntimeFeedbackSettings",
    "get_runtime_feedback_settings",
    "reset_runtime_feedback_settings",
    # Auto Rollback
    "AutoRollbackSettings",
    "get_auto_rollback_settings",
    "reset_auto_rollback_settings",
    # Safety Bounds
    "SafetyBoundsSettings",
    "ParameterBoundConfig",
    "get_safety_bounds_settings",
    "reset_safety_bounds_settings",
    # State Cache
    "StateCacheSettings",
    "get_state_cache_settings",
    "reset_state_cache_settings",
    # Apply Strategy
    "ApplyStrategySettings",
    "get_apply_strategy_settings",
    "reset_apply_strategy_settings",
    # Decision Engine
    "DecisionEngineSettings",
    "get_decision_engine_settings",
    "reset_decision_engine_settings",
    # Hash Chain (105_HARDCODED_CONFIG_AUDIT_REFACTORING.md Step 2)
    "HashChainSettings",
    "get_hash_chain_settings",
    "reset_hash_chain_settings",
    # API Rate Limit (106_HARDCODED_CONFIG_API_REFACTORING.md Step 1)
    "ApiRateLimitSettings",
    "get_api_rate_limit_settings",
    "reset_api_rate_limit_settings",
    # Daily Report Task Settings (108_HARDCODED_CONFIG_REFACTORING_PART1_CELERY_TASKS.md)
    "DailyReportSettings",
    "get_daily_report_settings",
    "reset_daily_report_settings",
    # Cleanup Task Settings (108_HARDCODED_CONFIG_REFACTORING_PART1_CELERY_TASKS.md)
    "CleanupSettings",
    "get_cleanup_settings",
    "reset_cleanup_settings",
    # X-Test Resource Guard Settings (143_XTEST_RESOURCE_AWARE_INTERLOCK.md)
    "ResourceGuardSettings",
    "get_resource_guard_settings",
    "reset_resource_guard_settings",
    # Event Buffer Settings (169_SETTINGS_SCALE_LIMITS.md)
    "EventBufferSettings",
    "get_event_buffer_settings",
    "reset_event_buffer_settings",
    # Enterprise Scale Settings (169_SETTINGS_SCALE_LIMITS.md)
    "ScaleProfile",
    "ScaleSettings",
    "PROFILE_DEFAULTS",
    "get_scale_settings",
    "reset_scale_settings",
    # 207 위치통일: Backpressure (scaling/config.py → settings/backpressure.py)
    "BackpressureLevel",
    "BackpressureStrategy",
    "LEVEL_RATE_MULTIPLIERS",
    "BackpressureSettings",
    "get_backpressure_settings",
    "reset_backpressure_settings",
    # Admission Control (HTTP 유입 제어)
    "AdmissionControlSettings",
    "get_admission_control_settings",
    "reset_admission_control_settings",
    # 207 위치통일: Leader Election (coordination/config.py → settings/leader_election.py)
    "LeaderElectionSettings",
    "get_leader_election_settings",
    "reset_leader_election_settings",
    # 207 위치통일: Meta Watchdog (meta/config.py → settings/meta_watchdog.py)
    "MetaWatchdogSettings",
    "get_meta_watchdog_settings",
    "reset_meta_watchdog_settings",
    # 207 위치통일: Error Budget Gate (services/error_budget_gate/config.py → settings/error_budget_gate.py)
    "ErrorBudgetGateSettings",
    "get_error_budget_gate_settings",
    "reset_error_budget_gate_settings",
]
