"""
Drift Detection Tasks - Django Adapter

This module re-exports drift detection Celery tasks from the selfhealing package
for backward compatibility.

For new code, prefer importing directly from selfhealing.celery_tasks.
"""

from selfhealing.celery_tasks import (
    check_sla_drift,
    cleanup_expired_chaos_experiments,
)

__all__ = [
    "check_sla_drift",
    "cleanup_expired_chaos_experiments",
]
