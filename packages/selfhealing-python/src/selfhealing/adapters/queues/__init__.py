"""
Task queue adapters for the self-healing system.

This package contains implementations of TaskQueueInterface.
"""

from selfhealing.adapters.queues.celery_adapter import CeleryTaskAdapter
from selfhealing.adapters.queues.sync_adapter import SyncTaskAdapter

__all__ = [
    "CeleryTaskAdapter",
    "SyncTaskAdapter",
]
