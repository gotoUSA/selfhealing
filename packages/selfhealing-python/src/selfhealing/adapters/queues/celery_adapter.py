"""
Celery Task Queue Adapter for the self-healing system.

Implements TaskQueueInterface using Celery as the backend.
"""

from __future__ import annotations

import logging
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


class CeleryTaskAdapter(TaskQueueInterface):
    """
    Celery implementation of TaskQueueInterface.

    Wraps Celery's task registration and execution APIs.
    """

    def __init__(self, app=None):
        """
        Initialize the Celery task adapter.

        Args:
            app: Celery app instance. If None, uses the default app.
        """
        self._app = app
        self._tasks: dict[str, Any] = {}

    @property
    def app(self):
        """Get Celery app, importing default if needed."""
        if self._app is None:
            try:
                from celery import current_app
                self._app = current_app
            except ImportError:
                raise ImportError("Celery is required for CeleryTaskAdapter")
        return self._app

    @property
    def provider_name(self) -> str:
        """Return the provider name."""
        return "celery"

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

            # Register with Celery
            celery_task = self.app.task(
                bind=bind,
                name=task_name,
                max_retries=max_retries,
                autoretry_for=autoretry_for,
                retry_backoff=retry_backoff,
                rate_limit=rate_limit,
            )(func)

            # Store reference
            self._tasks[task_name] = celery_task

            return celery_task

        return decorator

    def register_celery_task(self, celery_task, name: Optional[str] = None) -> None:
        """
        Register an existing Celery task.

        Args:
            celery_task: Celery task instance
            name: Optional override for task name
        """
        task_name = name or celery_task.name
        self._tasks[task_name] = celery_task

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
        """Enqueue a task for async execution."""
        kwargs = kwargs or {}
        options = options or TaskOptions()

        # Get task
        task = self._tasks.get(task_name)
        if task is None:
            # Try to get from Celery app
            try:
                task = self.app.tasks.get(task_name)
            except KeyError:
                pass

        if task is None:
            raise ValueError(f"Task not found: {task_name}")

        # Build apply_async options
        celery_options = {}
        if options.countdown is not None:
            celery_options['countdown'] = options.countdown
        if options.eta is not None:
            celery_options['eta'] = options.eta
        if options.expires is not None:
            celery_options['expires'] = options.expires
        if options.queue is not None:
            celery_options['queue'] = options.queue
        if options.priority != 0:
            celery_options['priority'] = options.priority

        # Execute
        result = task.apply_async(args=args, kwargs=kwargs, **celery_options)
        return result.id

    def enqueue_many(
        self,
        tasks: list[tuple[str, tuple, dict]],
        options: Optional[TaskOptions] = None,
    ) -> list[str]:
        """Enqueue multiple tasks atomically."""
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
        """Get task result (may block if timeout provided)."""
        from celery.result import AsyncResult

        async_result = AsyncResult(task_id, app=self.app)

        if timeout is not None:
            try:
                result = async_result.get(timeout=timeout)
                return TaskResult(
                    task_id=task_id,
                    status=TaskStatus.SUCCESS,
                    result=result,
                )
            except Exception as e:
                return TaskResult(
                    task_id=task_id,
                    status=self._map_celery_status(async_result.status),
                    error=str(e),
                )

        # Non-blocking status check
        status = self._map_celery_status(async_result.status)

        if async_result.successful():
            return TaskResult(
                task_id=task_id,
                status=TaskStatus.SUCCESS,
                result=async_result.result,
            )
        elif async_result.failed():
            return TaskResult(
                task_id=task_id,
                status=TaskStatus.FAILURE,
                error=str(async_result.result),
                traceback=async_result.traceback,
            )
        else:
            return TaskResult(
                task_id=task_id,
                status=status,
            )

    def _map_celery_status(self, celery_status: str) -> TaskStatus:
        """Map Celery status to TaskStatus."""
        mapping = {
            'PENDING': TaskStatus.PENDING,
            'STARTED': TaskStatus.STARTED,
            'SUCCESS': TaskStatus.SUCCESS,
            'FAILURE': TaskStatus.FAILURE,
            'RETRY': TaskStatus.RETRY,
            'REVOKED': TaskStatus.REVOKED,
        }
        return mapping.get(celery_status, TaskStatus.PENDING)

    def revoke(
        self,
        task_id: str,
        terminate: bool = False,
        signal: str = "SIGTERM",
    ) -> bool:
        """Cancel a pending or running task."""
        try:
            self.app.control.revoke(
                task_id,
                terminate=terminate,
                signal=signal,
            )
            return True
        except Exception as e:
            logger.error(f"[CeleryAdapter] Failed to revoke task {task_id}: {e}")
            return False

    def retry(
        self,
        task_id: str,
        countdown: Optional[int] = None,
        max_retries: Optional[int] = None,
    ) -> str:
        """Retry a failed task."""
        from celery.result import AsyncResult

        async_result = AsyncResult(task_id, app=self.app)

        # Get original task info
        task_name = async_result.name
        args = async_result.args or ()
        kwargs = async_result.kwargs or {}

        options = TaskOptions(countdown=countdown)
        return self.enqueue(task_name, args, kwargs, options)

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
        """Schedule a periodic task."""
        schedule_name = name or f"periodic_{task_name}"
        kwargs = kwargs or {}

        try:
            from celery.schedules import timedelta as celery_timedelta

            # Add to beat schedule
            self.app.conf.beat_schedule[schedule_name] = {
                'task': task_name,
                'schedule': schedule,
                'args': args,
                'kwargs': kwargs,
            }
            return schedule_name
        except Exception as e:
            logger.error(f"[CeleryAdapter] Failed to schedule periodic task: {e}")
            raise

    def unschedule(self, schedule_id: str) -> bool:
        """Remove a periodic schedule."""
        try:
            if schedule_id in self.app.conf.beat_schedule:
                del self.app.conf.beat_schedule[schedule_id]
                return True
            return False
        except Exception as e:
            logger.error(f"[CeleryAdapter] Failed to unschedule task: {e}")
            return False

    # =========================================================================
    # Queue Management
    # =========================================================================

    def purge_queue(self, queue_name: str = "default") -> int:
        """Remove all pending tasks from a queue."""
        try:
            return self.app.control.purge()
        except Exception as e:
            logger.error(f"[CeleryAdapter] Failed to purge queue: {e}")
            return 0

    def queue_length(self, queue_name: str = "default") -> int:
        """Get number of pending tasks in queue."""
        try:
            with self.app.connection_or_acquire() as conn:
                return conn.default_channel.queue_declare(
                    queue=queue_name, passive=True
                ).message_count
        except Exception as e:
            logger.warning(f"[CeleryAdapter] Failed to get queue length: {e}")
            return 0

    # =========================================================================
    # Health Check
    # =========================================================================

    def health_check(self) -> bool:
        """Check if Celery workers are reachable."""
        try:
            inspect = self.app.control.inspect()
            return bool(inspect.ping())
        except Exception as e:
            logger.error(f"[CeleryAdapter] Health check failed: {e}")
            return False
