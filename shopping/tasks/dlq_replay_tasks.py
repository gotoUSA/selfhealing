"""
DLQ Replay Tasks - Django Adapter

This module re-exports DLQ-related Celery tasks from the selfhealing package
for backward compatibility.

For new code, prefer importing directly from selfhealing.celery_tasks.
"""

from selfhealing.celery_tasks import (
    cleanup_resolved_dlq_entries,
    conditional_replay_on_circuit_close,
    replay_batch_by_domain,
    replay_batch_by_failure_type,
    replay_single_dlq_entry,
)

# Alias for backward compatibility
replay_on_circuit_breaker_close = conditional_replay_on_circuit_close

__all__ = [
    "cleanup_resolved_dlq_entries",
    "conditional_replay_on_circuit_close",
    "replay_batch_by_domain",
    "replay_batch_by_failure_type",
    "replay_single_dlq_entry",
    "replay_on_circuit_breaker_close",  # Alias
]
