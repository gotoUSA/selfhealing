"""
Task Queue Interface for the self-healing system.

This module defines the abstract interface for async task execution,
allowing different implementations (Celery, RQ, Dramatiq, Sync for testing, etc.)
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, TypeVar
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum

F = TypeVar('F', bound=Callable)


class TaskStatus(str, Enum):
    """Task execution status"""
    PENDING = "pending"
    STARTED = "started"
    SUCCESS = "success"
    FAILURE = "failure"
    RETRY = "retry"
    REVOKED = "revoked"


@dataclass
class TaskResult:
    """Result of task execution or status check"""
    task_id: str
    status: TaskStatus
    result: Optional[Any] = None
    error: Optional[str] = None
    traceback: Optional[str] = None
    retries: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


@dataclass
class TaskOptions:
    """Options for task enqueueing"""
    countdown: Optional[int] = None          # Delay in seconds
    eta: Optional[datetime] = None           # Exact execution time
    expires: Optional[datetime] = None       # Task expiration time
    retry: bool = True                        # Enable auto-retry
    max_retries: int = 3                      # Max retry attempts
    retry_backoff: bool = True                # Exponential backoff
    retry_backoff_max: int = 600              # Max backoff seconds
    queue: Optional[str] = None               # Target queue name
    priority: int = 0                         # Task priority (higher = sooner)


class TaskQueueInterface(ABC):
    """
    Abstract interface for async task queues.

    Implementations:
        - CeleryTaskAdapter (current)
        - RQTaskAdapter (Redis Queue - planned)
        - DramatiqTaskAdapter (planned)
        - SyncTaskAdapter (for testing - synchronous execution)
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name (e.g., 'celery', 'rq')"""
        pass

    # =========================================================================
    # Task Registration
    # =========================================================================

    @abstractmethod
    def task(
        self,
        name: Optional[str] = None,
        bind: bool = False,
        max_retries: int = 3,
        autoretry_for: tuple[type[Exception], ...] = (),
        retry_backoff: bool = True,
        rate_limit: Optional[str] = None,
    ) -> Callable[[F], F]:
        """
        Decorator to register a function as a task.

        Args:
            name: Task name (default: function name)
            bind: If True, pass task instance as first arg
            max_retries: Maximum retry attempts
            autoretry_for: Exception types to auto-retry
            retry_backoff: Use exponential backoff
            rate_limit: Rate limit (e.g., "10/m" for 10 per minute)

        Returns:
            Decorator function

        Example:
            @task_queue.task(max_retries=5, autoretry_for=(ConnectionError,))
            def process_payment(payment_id: int):
                ...
        """
        pass

    # =========================================================================
    # Task Execution
    # =========================================================================

    @abstractmethod
    def enqueue(
        self,
        task_name: str,
        args: tuple = (),
        kwargs: Optional[dict] = None,
        options: Optional[TaskOptions] = None,
    ) -> str:
        """
        Enqueue a task for async execution.

        Args:
            task_name: Registered task name
            args: Positional arguments
            kwargs: Keyword arguments
            options: Execution options

        Returns:
            Task ID for tracking

        Raises:
            TaskNotFoundError: If task_name not registered
        """
        pass

    @abstractmethod
    def enqueue_many(
        self,
        tasks: list[tuple[str, tuple, dict]],
        options: Optional[TaskOptions] = None,
    ) -> list[str]:
        """
        Enqueue multiple tasks atomically.

        Args:
            tasks: List of (task_name, args, kwargs) tuples
            options: Shared execution options

        Returns:
            List of task IDs
        """
        pass

    # =========================================================================
    # Task Management
    # =========================================================================

    @abstractmethod
    def get_result(
        self,
        task_id: str,
        timeout: Optional[float] = None,
    ) -> TaskResult:
        """
        Get task result (may block if timeout provided).

        Args:
            task_id: Task ID from enqueue
            timeout: Max seconds to wait for completion

        Returns:
            TaskResult with status and result/error
        """
        pass

    @abstractmethod
    def revoke(
        self,
        task_id: str,
        terminate: bool = False,
        signal: str = "SIGTERM",
    ) -> bool:
        """
        Cancel a pending or running task.

        Args:
            task_id: Task ID to cancel
            terminate: If True, terminate running task
            signal: Signal to send if terminating

        Returns:
            True if task was revoked
        """
        pass

    @abstractmethod
    def retry(
        self,
        task_id: str,
        countdown: Optional[int] = None,
        max_retries: Optional[int] = None,
    ) -> str:
        """
        Retry a failed task.

        Args:
            task_id: Original task ID
            countdown: Delay before retry
            max_retries: Override max retries

        Returns:
            New task ID
        """
        pass

    # =========================================================================
    # Scheduling
    # =========================================================================

    @abstractmethod
    def schedule_periodic(
        self,
        task_name: str,
        schedule: timedelta,
        args: tuple = (),
        kwargs: Optional[dict] = None,
        name: Optional[str] = None,
    ) -> str:
        """
        Schedule a periodic task.

        Args:
            task_name: Registered task name
            schedule: Execution interval
            args: Positional arguments
            kwargs: Keyword arguments
            name: Unique schedule name

        Returns:
            Schedule ID
        """
        pass

    @abstractmethod
    def unschedule(self, schedule_id: str) -> bool:
        """Remove a periodic schedule."""
        pass

    # =========================================================================
    # Queue Management
    # =========================================================================

    @abstractmethod
    def purge_queue(self, queue_name: str = "default") -> int:
        """
        Remove all pending tasks from a queue.

        Returns:
            Number of tasks purged
        """
        pass

    @abstractmethod
    def queue_length(self, queue_name: str = "default") -> int:
        """Get number of pending tasks in queue."""
        pass

    # =========================================================================
    # Health Check
    # =========================================================================

    @abstractmethod
    def health_check(self) -> bool:
        """Check if task queue backend is reachable."""
        pass
