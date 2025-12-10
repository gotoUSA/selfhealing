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

# Conditionally import adapters based on available dependencies
try:
    from selfhealing.adapters.queues.rq_adapter import RQTaskAdapter

    __all__.append("RQTaskAdapter")
except ImportError:
    pass
