"""
Task queue adapters for the self-healing system.

This module contains concrete implementations of TaskQueueInterface
for different task queue backends.

Available Adapters:
    - CeleryTaskAdapter: Celery-based distributed task queue
    - SyncTaskAdapter: Synchronous execution for testing
"""

from shopping.services.self_healing.adapters.queues.celery_adapter import (
    CeleryTaskAdapter,
)
from shopping.services.self_healing.adapters.queues.sync_adapter import (
    SyncTaskAdapter,
)

__all__ = [
    "CeleryTaskAdapter",
    "SyncTaskAdapter",
]
