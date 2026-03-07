"""
Selfhealing Audit Helpers Package

모든 Audit 로깅 헬퍼 함수들을 통합하여 제공합니다.
기존 `services.audit_helpers` 모듈과의 하위 호환성을 유지합니다.

Usage:
    from selfhealing.services.audit import (
        log_dlq_store_audit,
        log_dlq_replay_audit,
        log_cb_state_change_audit,
        log_retry_audit,
        # ... 등등
    )

    # 또는 기존 방식:
    from selfhealing.services.audit import log_dlq_store_audit
"""

from __future__ import annotations

# ============================================================
# Base Utilities (WAL, buffer, adapter)
# ============================================================
from selfhealing.services.audit.base import (
    _get_audit_adapter,
    _get_wal,
    _try_add_to_buffer,
    _write_to_wal,
    disable_wal,
    enable_wal,
    get_wal_stats,
)

# Alias for compatibility
get_wal_instance = _get_wal

# ============================================================
# Circuit Breaker & Governance Audit Functions
# ============================================================
from selfhealing.services.audit.cb_audit import (
    log_cb_state_change_audit,
    log_cb_state_change_with_trace_audit,
    log_governance_blocked_audit,
    log_governance_blocked_cb_audit,
    log_pool_cb_rejection_audit,
    log_rate_limited_audit,
)

# ============================================================
# Chaos Experiment & Emergency Mode Audit Functions
# ============================================================
from selfhealing.services.audit.chaos_audit import (
    log_chaos_experiment_audit,
    log_emergency_mode_audit,
    log_error_budget_blocked_audit,
    log_freeze_mode_audit,
    log_kill_switch_override_audit,
    log_panic_threshold_audit,
)

# ============================================================
# Compliance, Security & FinOps Audit Functions
# ============================================================
from selfhealing.services.audit.compliance_audit import (
    log_blast_radius_audit,
    log_compliance_audit,
    log_data_access_audit,
    log_finops_audit,
    log_region_isolation_audit,
    log_security_violation_audit,
)

# ============================================================
# DLQ Audit Functions
# ============================================================
from selfhealing.services.audit.dlq_audit import (
    log_dlq_replay_audit,
    log_dlq_store_audit,
)

# ============================================================
# Retry, Rollback & System Control Audit Functions
# ============================================================
from selfhealing.services.audit.retry_audit import (
    log_retry_audit,
    log_rollback_audit,
    log_system_control_audit,
)

# ============================================================
# Storage & Task Audit Functions
# ============================================================
from selfhealing.services.audit.storage_audit import (
    log_chaos_scheduler_audit,
    log_config_apply_audit,
    log_drift_detection_audit,
    log_drift_reconciliation_audit,
    log_governance_task_audit,
    log_storage_failure_audit,
    log_storage_recovery_audit,
    log_traffic_aware_replay_audit,
)

# ============================================================
# X-Test-Mode Audit Functions
# ============================================================
from selfhealing.services.audit.xtest_audit import (
    log_xtest_cleanup_audit,
    log_xtest_injection_audit,
    log_xtest_operation_audit,
    log_xtest_scenario_audit,
    log_xtest_session_end_audit,
    log_xtest_session_start_audit,
)

# ============================================================
# Public API
# ============================================================
__all__ = [
    # Base utilities
    "_write_to_wal",
    "_try_add_to_buffer",
    "_get_audit_adapter",
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
    # Compliance, Security & FinOps
    "log_compliance_audit",
    "log_security_violation_audit",
    "log_region_isolation_audit",
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
