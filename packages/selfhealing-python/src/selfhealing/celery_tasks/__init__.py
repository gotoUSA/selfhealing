"""
SelfHealing Celery Tasks

All Celery tasks for the selfhealing package are defined here.
This module can be autodiscovered by Celery and used in any Django project.

Usage in your Django project's celery.py:
    app.autodiscover_tasks(['selfhealing.celery_tasks'])

Or simply import all tasks in your host application's tasks.py:
    from selfhealing.celery_tasks import *  # noqa

Tasks are grouped by domain:
- circuit_breaker_tasks: Circuit breaker management
- dlq_tasks: DLQ replay operations  
- chaos_tasks: Chaos engineering safety
- metrics_tasks: Observability and SLA monitoring
- drift_detection_tasks: SLA drift detection
"""

from selfhealing.celery_tasks.circuit_breaker_tasks import (
    check_circuit_breaker_recovery,
    expire_manual_overrides,
    force_close_circuit_breaker,
    force_open_circuit_breaker,
)
from selfhealing.celery_tasks.dlq_tasks import (
    cleanup_resolved_dlq_entries,
    conditional_replay_on_circuit_close,
    replay_batch_by_domain,
    replay_batch_by_failure_type,
    replay_single_dlq_entry,
)
from selfhealing.celery_tasks.chaos_tasks import (
    check_recovery_monitoring_experiments,
    hunt_zombie_experiments,
)
from selfhealing.celery_tasks.metrics_tasks import (
    check_and_report_sla_breaches,
    collect_self_healing_metrics,
)
from selfhealing.celery_tasks.drift_detection_tasks import (
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
