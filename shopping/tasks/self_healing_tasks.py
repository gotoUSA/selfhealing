"""
Self-Healing Tasks - Django Adapter (DEPRECATED)

.. deprecated:: 2.0.0
    이 모듈은 하위 호환성을 위해서만 유지됩니다.
    직접 selfhealing.celery_tasks에서 import하세요.
    이 모듈은 v3.0.0에서 제거될 예정입니다.

This module re-exports all selfhealing Celery tasks for backward compatibility.
All task definitions live in the selfhealing package (selfhealing.celery_tasks).

Migration Guide:
    Before (deprecated):
        from shopping.tasks.self_healing_tasks import hunt_zombie_experiments
    
    After (recommended):
        from selfhealing.celery_tasks import hunt_zombie_experiments
"""

import warnings

warnings.warn(
    "Importing from 'shopping.tasks.self_healing_tasks' is deprecated. "
    "Import directly from 'selfhealing.celery_tasks' instead. "
    "This module will be removed in v3.0.0.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export all tasks from selfhealing package (backward compatibility)
from selfhealing.celery_tasks import (
    # Circuit Breaker
    check_circuit_breaker_recovery,
    expire_manual_overrides,
    force_close_circuit_breaker,
    force_open_circuit_breaker,
    # DLQ
    cleanup_resolved_dlq_entries,
    conditional_replay_on_circuit_close,
    replay_batch_by_domain,
    replay_batch_by_failure_type,
    replay_single_dlq_entry,
    # Chaos
    check_recovery_monitoring_experiments,
    hunt_zombie_experiments,
    # Metrics
    check_and_report_sla_breaches,
    collect_self_healing_metrics,
    # Drift Detection
    check_sla_drift,
    cleanup_expired_chaos_experiments,
)

__all__ = [
    # Circuit Breaker
    "check_circuit_breaker_recovery",
    "expire_manual_overrides",
    "force_close_circuit_breaker",
    "force_open_circuit_breaker",
    # DLQ
    "cleanup_resolved_dlq_entries",
    "conditional_replay_on_circuit_close",
    "replay_batch_by_domain",
    "replay_batch_by_failure_type",
    "replay_single_dlq_entry",
    # Chaos
    "check_recovery_monitoring_experiments",
    "hunt_zombie_experiments",
    # Metrics
    "check_and_report_sla_breaches",
    "collect_self_healing_metrics",
    # Drift Detection
    "check_sla_drift",
    "cleanup_expired_chaos_experiments",
]
