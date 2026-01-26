"""
Audit Helpers - Backward Compatibility Wrapper

이 모듈은 하위 호환성을 위한 re-export wrapper입니다.
실제 구현은 selfhealing.services.audit 패키지에 있습니다.

Usage (기존 코드 그대로 동작):
    from selfhealing.services.audit_helpers import log_dlq_store_audit
    from selfhealing.services.audit_helpers import log_cb_state_change_audit

새 코드는 직접 패키지에서 import 가능:
    from selfhealing.services.audit import log_dlq_store_audit
"""

from __future__ import annotations

# =============================================================================
# Re-export everything from audit package for backward compatibility
# =============================================================================

from selfhealing.services.audit import (
    # Base utilities
    _write_to_wal,
    _try_add_to_buffer,
    _get_audit_adapter,
    _get_wal,
    get_wal_instance,
    disable_wal,
    enable_wal,
    get_wal_stats,
    # DLQ
    log_dlq_store_audit,
    log_dlq_replay_audit,
    # Circuit Breaker
    log_cb_state_change_audit,
    log_governance_blocked_audit,
    log_rate_limited_audit,
    log_pool_cb_rejection_audit,
    log_cb_state_change_with_trace_audit,
    log_governance_blocked_cb_audit,
    # Retry & Rollback
    log_retry_audit,
    log_system_control_audit,
    log_rollback_audit,
    # Chaos & Emergency
    log_chaos_experiment_audit,
    log_emergency_mode_audit,
    log_kill_switch_override_audit,
    log_panic_threshold_audit,
    log_freeze_mode_audit,
    log_error_budget_blocked_audit,
    # X-Test-Mode
    log_xtest_operation_audit,
    log_xtest_scenario_audit,
    log_xtest_session_start_audit,
    log_xtest_session_end_audit,
    log_xtest_injection_audit,
    log_xtest_cleanup_audit,
    # Compliance & FinOps
    log_compliance_audit,
    log_blast_radius_audit,
    log_finops_audit,
    log_data_access_audit,
    # Storage & Tasks
    log_storage_failure_audit,
    log_storage_recovery_audit,
    log_drift_reconciliation_audit,
    log_config_apply_audit,
    log_chaos_scheduler_audit,
    log_governance_task_audit,
    log_traffic_aware_replay_audit,
    log_drift_detection_audit,
)

__all__ = [
    # Base utilities
    "_write_to_wal",
    "_try_add_to_buffer",
    "_get_audit_adapter",
    "_get_wal",
    "get_wal_instance",
    "disable_wal",
    "enable_wal",
    "get_wal_stats",
    # DLQ
    "log_dlq_store_audit",
    "log_dlq_replay_audit",
    # Circuit Breaker
    "log_cb_state_change_audit",
    "log_governance_blocked_audit",
    "log_rate_limited_audit",
    "log_pool_cb_rejection_audit",
    "log_cb_state_change_with_trace_audit",
    "log_governance_blocked_cb_audit",
    # Retry & Rollback
    "log_retry_audit",
    "log_system_control_audit",
    "log_rollback_audit",
    # Chaos & Emergency
    "log_chaos_experiment_audit",
    "log_emergency_mode_audit",
    "log_kill_switch_override_audit",
    "log_panic_threshold_audit",
    "log_freeze_mode_audit",
    "log_error_budget_blocked_audit",
    # X-Test-Mode
    "log_xtest_operation_audit",
    "log_xtest_scenario_audit",
    "log_xtest_session_start_audit",
    "log_xtest_session_end_audit",
    "log_xtest_injection_audit",
    "log_xtest_cleanup_audit",
    # Compliance & FinOps
    "log_compliance_audit",
    "log_blast_radius_audit",
    "log_finops_audit",
    "log_data_access_audit",
    # Storage & Tasks
    "log_storage_failure_audit",
    "log_storage_recovery_audit",
    "log_drift_reconciliation_audit",
    "log_config_apply_audit",
    "log_chaos_scheduler_audit",
    "log_governance_task_audit",
    "log_traffic_aware_replay_audit",
    "log_drift_detection_audit",
]
