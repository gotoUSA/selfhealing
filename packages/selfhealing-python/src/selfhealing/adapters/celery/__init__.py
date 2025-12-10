"""
Celery adapter for the self-healing system.

This module provides Celery-specific implementations including:
- Celery tasks for DLQ processing
- Celery tasks for circuit breaker management
- Celery tasks for metric collection
- Celery beat schedule helpers
"""

from selfhealing.adapters.celery.tasks import (
    # Circuit Breaker Tasks
    conditional_replay_on_circuit_close,
    check_circuit_breaker_recovery,
    force_open_circuit_breaker,
    force_close_circuit_breaker,
    expire_manual_overrides,
    # DLQ Tasks
    replay_single_dlq_entry,
    replay_batch_by_domain,
    cleanup_resolved_dlq_entries,
    # Metrics Tasks
    collect_self_healing_metrics,
    check_and_report_sla_breaches,
)

__all__ = [
    # Circuit Breaker Tasks
    "conditional_replay_on_circuit_close",
    "check_circuit_breaker_recovery",
    "force_open_circuit_breaker",
    "force_close_circuit_breaker",
    "expire_manual_overrides",
    # DLQ Tasks
    "replay_single_dlq_entry",
    "replay_batch_by_domain",
    "cleanup_resolved_dlq_entries",
    # Metrics Tasks
    "collect_self_healing_metrics",
    "check_and_report_sla_breaches",
]


# Default Celery Beat Schedule for self-healing tasks
CELERY_BEAT_SCHEDULE = {
    "selfhealing-check-circuit-recovery": {
        "task": "selfhealing.adapters.celery.tasks.check_circuit_breaker_recovery",
        "schedule": 60.0,  # Every minute
    },
    "selfhealing-expire-manual-overrides": {
        "task": "selfhealing.adapters.celery.tasks.expire_manual_overrides",
        "schedule": 300.0,  # Every 5 minutes
    },
    "selfhealing-collect-metrics": {
        "task": "selfhealing.adapters.celery.tasks.collect_self_healing_metrics",
        "schedule": 60.0,  # Every minute
    },
    "selfhealing-check-sla-breaches": {
        "task": "selfhealing.adapters.celery.tasks.check_and_report_sla_breaches",
        "schedule": 300.0,  # Every 5 minutes
    },
    "selfhealing-cleanup-dlq": {
        "task": "selfhealing.adapters.celery.tasks.cleanup_resolved_dlq_entries",
        "schedule": 86400.0,  # Daily
    },
}
