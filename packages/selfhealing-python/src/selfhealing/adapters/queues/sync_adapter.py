"""
Synchronous Task Queue Adapter for the self-healing system.

Implements TaskQueueInterface with synchronous execution.
Intended for testing and development environments.
"""

from __future__ import annotations

import logging
import traceback
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Optional, TypeVar

from selfhealing.interfaces.task_queue import (
    TaskQueueInterface,
    TaskStatus,
    TaskResult,
    TaskOptions,
)

logger = logging.getLogger(__name__)
F = TypeVar('F', bound=Callable)


class SyncTaskAdapter(TaskQueueInterface):
    """
    Synchronous implementation of TaskQueueInterface.

    Executes tasks immediately in the calling thread.
    Useful for testing and debugging without task queue infrastructure.

    WARNING: This adapter is intended for testing only.
    Tasks are executed synchronously and do not provide true async behavior.
    """

    def __init__(self):
        """Initialize the synchronous task adapter."""
        self._tasks: dict[str, Callable] = {}
        self._results: dict[str, TaskResult] = {}
        self._periodic_schedules: dict[str, dict] = {}

    @property
    def provider_name(self) -> str:
        """Return the provider name."""
        return "sync"

    # =========================================================================
    # Task Registration
    # =========================================================================

    def task(
        self,
        name: Optional[str] = None,
        bind: bool = False,
        max_retries: int = 3,
        autoretry_for: tuple[type[Exception], ...] = (),
        retry_backoff: bool = True,
        rate_limit: Optional[str] = None,
    ) -> Callable[[F], F]:
        """Decorator to register a function as a task."""
        def decorator(func: F) -> F:
            task_name = name or func.__name__

            # Create wrapper that stores task metadata
            def wrapper(*args, **kwargs):
                return func(*args, **kwargs)

            wrapper.__name__ = func.__name__
            wrapper.__doc__ = func.__doc__
            wrapper._task_name = task_name
            wrapper._max_retries = max_retries
            wrapper._autoretry_for = autoretry_for

            # Add delay method for Celery compatibility
            def delay(*args, **kwargs):
                return self._execute_task(task_name, args, kwargs)

            wrapper.delay = delay

            # Add apply_async for Celery compatibility
            def apply_async(args=(), kwargs=None, **options):
                kwargs = kwargs or {}
                return self._execute_task(task_name, args, kwargs)

            wrapper.apply_async = apply_async

            # Store reference
            self._tasks[task_name] = wrapper

            return wrapper

        return decorator

    def _execute_task(
        self,
        task_name: str,
        args: tuple,
        kwargs: dict,
    ) -> "SyncAsyncResult":
        """Execute a task synchronously and store result."""
        task_id = str(uuid.uuid4())
        started_at = datetime.now()

        task_func = self._tasks.get(task_name)
        if task_func is None:
            result = TaskResult(
                task_id=task_id,
                status=TaskStatus.FAILURE,
                error=f"Task not found: {task_name}",
                started_at=started_at,
                completed_at=datetime.now(),
            )
            self._results[task_id] = result
            return SyncAsyncResult(task_id, result)

        try:
            result_value = task_func(*args, **kwargs)
            result = TaskResult(
                task_id=task_id,
                status=TaskStatus.SUCCESS,
                result=result_value,
                started_at=started_at,
                completed_at=datetime.now(),
            )
        except Exception as e:
            result = TaskResult(
                task_id=task_id,
                status=TaskStatus.FAILURE,
                error=str(e),
                traceback=traceback.format_exc(),
                started_at=started_at,
                completed_at=datetime.now(),
            )
            logger.error(f"[SyncAdapter] Task {task_name} failed: {e}")

        self._results[task_id] = result
        return SyncAsyncResult(task_id, result)

    # =========================================================================
    # Task Execution
    # =========================================================================

    def enqueue(
        self,
        task_name: str,
        args: tuple = (),
        kwargs: Optional[dict] = None,
        options: Optional[TaskOptions] = None,
    ) -> str:
        """Enqueue a task for sync execution."""
        kwargs = kwargs or {}
        result = self._execute_task(task_name, args, kwargs)
        return result.id

    def enqueue_many(
        self,
        tasks: list[tuple[str, tuple, dict]],
        options: Optional[TaskOptions] = None,
    ) -> list[str]:
        """Enqueue multiple tasks."""
        task_ids = []
        for task_name, args, kwargs in tasks:
            task_id = self.enqueue(task_name, args, kwargs, options)
            task_ids.append(task_id)
        return task_ids

    # =========================================================================
    # Task Management
    # =========================================================================

    def get_result(
        self,
        task_id: str,
        timeout: Optional[float] = None,
    ) -> TaskResult:
        """Get task result (always immediate in sync mode)."""
        return self._results.get(
            task_id,
            TaskResult(
                task_id=task_id,
                status=TaskStatus.PENDING,
            )
        )

    def revoke(
        self,
        task_id: str,
        terminate: bool = False,
        signal: str = "SIGTERM",
    ) -> bool:
        """Cancel a task (no-op in sync mode since tasks execute immediately)."""
        logger.warning(f"[SyncAdapter] Revoke called on sync adapter (no-op): {task_id}")
        return True

    def retry(
        self,
        task_id: str,
        countdown: Optional[int] = None,
        max_retries: Optional[int] = None,
    ) -> str:
        """Retry a failed task."""
        original = self._results.get(task_id)
        if original is None:
            raise ValueError(f"Task not found: {task_id}")

        # We don't have the original args/kwargs stored, so we can't retry
        logger.warning(f"[SyncAdapter] Retry not fully supported in sync mode")
        return task_id

    # =========================================================================
    # Scheduling
    # =========================================================================

    def schedule_periodic(
        self,
        task_name: str,
        schedule: timedelta,
        args: tuple = (),
        kwargs: Optional[dict] = None,
        name: Optional[str] = None,
    ) -> str:
        """Schedule a periodic task (stored but not executed automatically)."""
        schedule_name = name or f"periodic_{task_name}"
        kwargs = kwargs or {}

        self._periodic_schedules[schedule_name] = {
            'task': task_name,
            'schedule': schedule,
            'args': args,
            'kwargs': kwargs,
        }

        logger.info(
            f"[SyncAdapter] Periodic task registered: {schedule_name} "
            f"(will not run automatically in sync mode)"
        )
        return schedule_name

    def unschedule(self, schedule_id: str) -> bool:
        """Remove a periodic schedule."""
        if schedule_id in self._periodic_schedules:
            del self._periodic_schedules[schedule_id]
            return True
        return False

    def run_periodic_task(self, schedule_id: str) -> Optional[str]:
        """
        Manually trigger a periodic task (for testing).

        Args:
            schedule_id: Schedule ID from schedule_periodic

        Returns:
            Task ID if executed, None if schedule not found
        """
        schedule = self._periodic_schedules.get(schedule_id)
        if schedule is None:
            return None

        return self.enqueue(
            schedule['task'],
            schedule['args'],
            schedule['kwargs'],
        )

    # =========================================================================
    # Queue Management
    # =========================================================================

    def purge_queue(self, queue_name: str = "default") -> int:
        """Purge queue (clears stored results)."""
        count = len(self._results)
        self._results.clear()
        return count

    def queue_length(self, queue_name: str = "default") -> int:
        """Get queue length (always 0 in sync mode)."""
        return 0

    # =========================================================================
    # Health Check
    # =========================================================================

    def health_check(self) -> bool:
        """Check if adapter is operational (always True for sync)."""
        return True

    # =========================================================================
    # Test Helpers
    # =========================================================================

    def clear_results(self) -> None:
        """Clear all stored results (for testing)."""
        self._results.clear()

    def clear_tasks(self) -> None:
        """Clear all registered tasks (for testing)."""
        self._tasks.clear()

    def get_registered_tasks(self) -> list[str]:
        """Get list of registered task names (for testing)."""
        return list(self._tasks.keys())


class SyncAsyncResult:
    """
    Mock AsyncResult for sync adapter.

    Provides Celery-compatible interface for sync execution.
    """

    def __init__(self, task_id: str, result: TaskResult):
        """
        Initialize with task result.

        Args:
            task_id: Task ID
            result: TaskResult instance
        """
        self.id = task_id
        self._result = result

    @property
    def status(self) -> str:
        """Get task status."""
        return self._result.status.value.upper()

    @property
    def result(self) -> Any:
        """Get task result value."""
        if self._result.status == TaskStatus.SUCCESS:
            return self._result.result
        elif self._result.status == TaskStatus.FAILURE:
            raise Exception(self._result.error)
        return None

    def successful(self) -> bool:
        """Check if task completed successfully."""
        return self._result.status == TaskStatus.SUCCESS

    def failed(self) -> bool:
        """Check if task failed."""
        return self._result.status == TaskStatus.FAILURE

    def get(self, timeout: Optional[float] = None) -> Any:
        """Get result (blocking in real Celery, immediate here)."""
        return self.result
