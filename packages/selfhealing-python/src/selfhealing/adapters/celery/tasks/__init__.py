"""
Celery Tasks for Self-Healing System.

These tasks provide background processing for:
- Circuit breaker state management
- DLQ replay operations
- Metrics collection
- SLA monitoring
- Cleanup operations

Usage:
    Add these tasks to your Celery beat schedule:

    CELERY_BEAT_SCHEDULE = {
        'check-circuit-breaker-recovery': {
            'task': 'selfhealing.adapters.celery.tasks.check_circuit_breaker_recovery',
            'schedule': 60.0,  # Every minute
        },
        'expire-manual-overrides': {
            'task': 'selfhealing.adapters.celery.tasks.expire_manual_overrides',
            'schedule': 300.0,  # Every 5 minutes
        },
        'collect-self-healing-metrics': {
            'task': 'selfhealing.adapters.celery.tasks.collect_self_healing_metrics',
            'schedule': 60.0,  # Every minute
        },
        'check-sla-breaches': {
            'task': 'selfhealing.adapters.celery.tasks.check_and_report_sla_breaches',
            'schedule': 300.0,  # Every 5 minutes
        },
        'cleanup-dlq-entries': {
            'task': 'selfhealing.adapters.celery.tasks.cleanup_resolved_dlq_entries',
            'schedule': 86400.0,  # Daily
        },
        'emit-selfhealing-heartbeat': {
            'task': 'selfhealing.adapters.celery.tasks.emit_selfhealing_heartbeat',
            'schedule': 60.0,  # Every minute
        },
    }
"""

from __future__ import annotations

# ============================================================
# Circuit Breaker Tasks
# ============================================================
from .circuit_breaker import (
    check_circuit_breaker_recovery,
    collect_cb_open_snapshot,
    conditional_replay_on_circuit_close,
    expire_manual_overrides,
    force_close_circuit_breaker,
    force_open_circuit_breaker,
    send_cb_open_notification,
)

# ============================================================
# DLQ Replay Tasks
# ============================================================
from .dlq_replay import (
    cleanup_resolved_dlq_entries,
    replay_batch_by_domain,
    replay_single_dlq_entry,
)

# ============================================================
# Metrics & Monitoring Tasks
# ============================================================
from .monitoring import (
    check_and_report_sla_breaches,
    collect_self_healing_metrics,
    emit_selfhealing_heartbeat,
    notify_failsafe_recovery,
)

# ============================================================
# Async Persistence Tasks
# ============================================================
from .persistence import (
    async_persist_batch,
    async_persist_dlq_entry,
    link_audit_to_dlq,
)

# ============================================================
# SLA Notification Tasks
# ============================================================
from .sla_notification import send_sla_notification

# ============================================================
# Postmortem Tasks
# ============================================================
from .postmortem import (
    check_stale_incident_groups,
    close_incident_group,
    flush_aggregated_notifications,
    process_individual_postmortem,
)

# ============================================================
# Public API
# ============================================================
__all__ = [
    # Persistence
    "async_persist_dlq_entry",
    "async_persist_batch",
    "link_audit_to_dlq",
    # Circuit Breaker
    "conditional_replay_on_circuit_close",
    "check_circuit_breaker_recovery",
    "force_open_circuit_breaker",
    "force_close_circuit_breaker",
    "expire_manual_overrides",
    "send_cb_open_notification",
    "collect_cb_open_snapshot",
    # DLQ Replay
    "replay_single_dlq_entry",
    "replay_batch_by_domain",
    "cleanup_resolved_dlq_entries",
    # Monitoring
    "collect_self_healing_metrics",
    "check_and_report_sla_breaches",
    "emit_selfhealing_heartbeat",
    "notify_failsafe_recovery",
    # SLA Notification
    "send_sla_notification",
    # Postmortem
    "close_incident_group",
    "flush_aggregated_notifications",
    "check_stale_incident_groups",
    "process_individual_postmortem",
]
