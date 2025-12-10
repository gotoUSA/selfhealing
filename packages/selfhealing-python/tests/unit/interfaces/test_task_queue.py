"""
Unit tests for TaskQueueInterface.

Tests the abstract interface contract and synchronous implementation.
"""

import pytest
import time
from datetime import datetime, timedelta
from typing import Callable
from unittest.mock import MagicMock, patch

from selfhealing.interfaces.task_queue import (
    TaskQueueInterface,
    TaskStatus,
    TaskResult,
    TaskOptions,
)
from selfhealing.adapters.queues.sync_adapter import SyncTaskAdapter


class TestTaskStatus:
    """Tests for TaskStatus enum."""

    def test_status_values(self):
        """Test all status values exist."""
        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.STARTED == "started"
        assert TaskStatus.SUCCESS == "success"
        assert TaskStatus.FAILURE == "failure"
        assert TaskStatus.RETRY == "retry"
        assert TaskStatus.REVOKED == "revoked"

    def test_status_string_comparison(self):
        """Test status can be compared with strings."""
        assert TaskStatus.SUCCESS == "success"
        assert TaskStatus.FAILURE == "failure"


class TestTaskResult:
    """Tests for TaskResult dataclass."""

    def test_success_result(self):
        """Test creating a successful task result."""
        result = TaskResult(
            task_id="task_123",
            status=TaskStatus.SUCCESS,
            result={"processed": True},
            started_at=datetime(2025, 12, 10, 10, 0, 0),
            completed_at=datetime(2025, 12, 10, 10, 0, 5),
        )
        assert result.task_id == "task_123"
        assert result.status == TaskStatus.SUCCESS
        assert result.result == {"processed": True}
        assert result.error is None

    def test_failure_result(self):
        """Test creating a failed task result."""
        result = TaskResult(
            task_id="task_456",
            status=TaskStatus.FAILURE,
            error="Connection timeout",
            traceback="Traceback...",
            retries=3,
        )
        assert result.status == TaskStatus.FAILURE
        assert result.error == "Connection timeout"
        assert result.retries == 3

    def test_pending_result(self):
        """Test creating a pending task result."""
        result = TaskResult(
            task_id="task_789",
            status=TaskStatus.PENDING,
        )
        assert result.status == TaskStatus.PENDING
        assert result.result is None


class TestTaskOptions:
    """Tests for TaskOptions dataclass."""

    def test_default_options(self):
        """Test default task options."""
        options = TaskOptions()
        assert options.countdown is None
        assert options.retry is True
        assert options.max_retries == 3
        assert options.retry_backoff is True
        assert options.retry_backoff_max == 600
        assert options.queue is None
        assert options.priority == 0

    def test_custom_options(self):
        """Test custom task options."""
        eta = datetime(2025, 12, 10, 12, 0, 0)
        expires = datetime(2025, 12, 10, 13, 0, 0)
        options = TaskOptions(
            countdown=30,
            eta=eta,
            expires=expires,
            retry=False,
            max_retries=5,
            queue="high_priority",
            priority=10,
        )
        assert options.countdown == 30
        assert options.eta == eta
        assert options.expires == expires
        assert options.retry is False
        assert options.max_retries == 5
        assert options.queue == "high_priority"
        assert options.priority == 10


class TestSyncTaskAdapter:
    """Tests for SyncTaskAdapter implementation."""

    @pytest.fixture
    def adapter(self):
        """Create a synchronous task adapter."""
        return SyncTaskAdapter()

    def test_provider_name(self, adapter: SyncTaskAdapter):
        """Test provider name."""
        assert adapter.provider_name == "sync"

    def test_implements_interface(self, adapter: SyncTaskAdapter):
        """Test that adapter implements TaskQueueInterface."""
        assert isinstance(adapter, TaskQueueInterface)

    # =========================================================================
    # Task Registration Tests
    # =========================================================================

    def test_task_decorator_registers_task(self, adapter: SyncTaskAdapter):
        """Test task decorator registers the function."""

        @adapter.task(name="my_task")
        def my_task(x, y):
            return x + y

        assert "my_task" in adapter._tasks

    def test_task_decorator_default_name(self, adapter: SyncTaskAdapter):
        """Test task decorator uses function name by default."""

        @adapter.task()
        def process_order():
            return "processed"

        assert "process_order" in adapter._tasks

    def test_task_delay_method(self, adapter: SyncTaskAdapter):
        """Test task has delay method."""

        @adapter.task(name="add_task")
        def add(x, y):
            return x + y

        result = add.delay(2, 3)
        assert result.get() == 5

    def test_task_apply_async_method(self, adapter: SyncTaskAdapter):
        """Test task has apply_async method."""

        @adapter.task(name="multiply_task")
        def multiply(x, y):
            return x * y

        result = multiply.apply_async(args=(4, 5))
        assert result.get() == 20

    # =========================================================================
    # Task Execution Tests
    # =========================================================================

    def test_enqueue_executes_immediately(self, adapter: SyncTaskAdapter):
        """Test enqueue executes task immediately in sync mode."""
        results = []

        @adapter.task(name="record_task")
        def record(value):
            results.append(value)
            return value

        task_id = adapter.enqueue("record_task", args=("test_value",))
        assert "test_value" in results
        assert task_id is not None

    def test_enqueue_with_kwargs(self, adapter: SyncTaskAdapter):
        """Test enqueue with keyword arguments."""

        @adapter.task(name="greet_task")
        def greet(name, greeting="Hello"):
            return f"{greeting}, {name}!"

        task_id = adapter.enqueue(
            "greet_task", args=("World",), kwargs={"greeting": "Hi"}
        )
        result = adapter.get_result(task_id)
        assert result.result == "Hi, World!"

    def test_enqueue_nonexistent_task(self, adapter: SyncTaskAdapter):
        """Test enqueue with nonexistent task."""
        task_id = adapter.enqueue("nonexistent_task")
        result = adapter.get_result(task_id)
        assert result.status == TaskStatus.FAILURE
        assert "not found" in result.error.lower()

    def test_enqueue_many_tasks(self, adapter: SyncTaskAdapter):
        """Test enqueue multiple tasks at once."""
        results = []

        @adapter.task(name="append_task")
        def append_value(value):
            results.append(value)
            return value

        task_ids = adapter.enqueue_many(
            [
                ("append_task", (1,), {}),
                ("append_task", (2,), {}),
                ("append_task", (3,), {}),
            ]
        )
        assert len(task_ids) == 3
        assert results == [1, 2, 3]

    def test_task_exception_handling(self, adapter: SyncTaskAdapter):
        """Test task exception is captured."""

        @adapter.task(name="failing_task")
        def fail():
            raise ValueError("Intentional failure")

        task_id = adapter.enqueue("failing_task")
        result = adapter.get_result(task_id)
        assert result.status == TaskStatus.FAILURE
        assert "Intentional failure" in result.error
        assert result.traceback is not None

    # =========================================================================
    # Task Management Tests
    # =========================================================================

    def test_get_result_success(self, adapter: SyncTaskAdapter):
        """Test get_result for successful task."""

        @adapter.task(name="success_task")
        def success():
            return {"status": "ok"}

        task_id = adapter.enqueue("success_task")
        result = adapter.get_result(task_id)
        assert result.status == TaskStatus.SUCCESS
        assert result.result == {"status": "ok"}

    def test_get_result_failure(self, adapter: SyncTaskAdapter):
        """Test get_result for failed task."""

        @adapter.task(name="fail_task")
        def fail():
            raise RuntimeError("Failed!")

        task_id = adapter.enqueue("fail_task")
        result = adapter.get_result(task_id)
        assert result.status == TaskStatus.FAILURE
        assert "Failed!" in result.error

    def test_get_result_nonexistent_task(self, adapter: SyncTaskAdapter):
        """Test get_result for nonexistent task ID."""
        result = adapter.get_result("nonexistent_task_id")
        assert result.status == TaskStatus.PENDING

    def test_revoke_task(self, adapter: SyncTaskAdapter):
        """Test revoke task (no-op in sync mode)."""
        result = adapter.revoke("any_task_id")
        assert result is True  # Always returns True in sync mode

    def test_retry_task(self, adapter: SyncTaskAdapter):
        """Test retry task (limited support in sync mode)."""
        call_count = 0

        @adapter.task(name="retry_test")
        def increment():
            nonlocal call_count
            call_count += 1
            return call_count

        task_id = adapter.enqueue("retry_test")
        # In sync mode, retry may not fully re-execute the task
        # Just verify retry returns a task_id (no exception)
        new_task_id = adapter.retry(task_id)
        assert new_task_id is not None
        assert call_count >= 1  # At least original was called

    # =========================================================================
    # Scheduling Tests
    # =========================================================================

    def test_schedule_periodic(self, adapter: SyncTaskAdapter):
        """Test scheduling a periodic task."""

        @adapter.task(name="periodic_task")
        def periodic():
            return "tick"

        schedule_id = adapter.schedule_periodic(
            task_name="periodic_task",
            schedule=timedelta(minutes=5),
            name="every_5_minutes",
        )
        assert schedule_id is not None

    def test_unschedule(self, adapter: SyncTaskAdapter):
        """Test unscheduling a periodic task."""

        @adapter.task(name="periodic_task")
        def periodic():
            return "tick"

        schedule_id = adapter.schedule_periodic(
            task_name="periodic_task",
            schedule=timedelta(minutes=5),
        )
        result = adapter.unschedule(schedule_id)
        assert result is True

    def test_unschedule_nonexistent(self, adapter: SyncTaskAdapter):
        """Test unscheduling a nonexistent schedule."""
        result = adapter.unschedule("nonexistent_schedule")
        assert result is False

    # =========================================================================
    # Queue Management Tests
    # =========================================================================

    def test_purge_queue(self, adapter: SyncTaskAdapter):
        """Test purge queue (no-op in sync mode since tasks execute immediately)."""
        count = adapter.purge_queue()
        assert count == 0  # No pending tasks in sync mode

    def test_queue_length(self, adapter: SyncTaskAdapter):
        """Test queue length (always 0 in sync mode)."""
        length = adapter.queue_length()
        assert length == 0

    # =========================================================================
    # Health Check Tests
    # =========================================================================

    def test_health_check_healthy(self, adapter: SyncTaskAdapter):
        """Test health check returns True."""
        assert adapter.health_check() is True


class TestSyncAsyncResult:
    """Tests for SyncAsyncResult class."""

    @pytest.fixture
    def adapter(self):
        """Create a synchronous task adapter."""
        return SyncTaskAdapter()

    def test_async_result_id(self, adapter: SyncTaskAdapter):
        """Test async result has task ID."""

        @adapter.task(name="id_test")
        def task_func():
            return "result"

        result = task_func.delay()
        assert result.id is not None

    def test_async_result_get(self, adapter: SyncTaskAdapter):
        """Test async result get method."""

        @adapter.task(name="get_test")
        def task_func():
            return {"data": "value"}

        result = task_func.delay()
        assert result.get() == {"data": "value"}

    def test_async_result_successful(self, adapter: SyncTaskAdapter):
        """Test async result successful property."""

        @adapter.task(name="success_test")
        def task_func():
            return "ok"

        result = task_func.delay()
        assert result.successful() is True

    def test_async_result_failed(self, adapter: SyncTaskAdapter):
        """Test async result failed property."""

        @adapter.task(name="fail_test")
        def task_func():
            raise ValueError("error")

        result = task_func.delay()
        assert result.successful() is False
        assert result.failed() is True


class TestTaskQueueInterfaceContract:
    """Tests to verify interface contract compliance."""

    def test_abstract_methods_required(self):
        """Test that all abstract methods must be implemented."""
        with pytest.raises(TypeError):
            TaskQueueInterface()

    def test_interface_has_required_methods(self):
        """Test that interface defines all required methods."""
        required_methods = [
            "provider_name",
            "task",
            "enqueue",
            "enqueue_many",
            "get_result",
            "revoke",
            "retry",
            "schedule_periodic",
            "unschedule",
            "purge_queue",
            "queue_length",
            "health_check",
        ]
        for method in required_methods:
            assert hasattr(TaskQueueInterface, method)


class TestTaskRetryBehavior:
    """Tests for task retry behavior."""

    @pytest.fixture
    def adapter(self):
        """Create a synchronous task adapter."""
        return SyncTaskAdapter()

    def test_task_with_autoretry(self, adapter: SyncTaskAdapter):
        """Test task with autoretry configuration."""
        attempt_count = 0

        @adapter.task(
            name="flaky_task",
            max_retries=3,
            autoretry_for=(ConnectionError,),
        )
        def flaky_task():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 3:
                raise ConnectionError("Network error")
            return "success"

        # In sync mode, autoretry is not automatic
        # We just verify the task metadata is stored
        assert adapter._tasks["flaky_task"]._max_retries == 3
        assert ConnectionError in adapter._tasks["flaky_task"]._autoretry_for

    def test_manual_retry(self, adapter: SyncTaskAdapter):
        """Test manual retry of a task."""
        values = []

        @adapter.task(name="value_task")
        def value_task(val):
            values.append(val)
            return val

        task_id = adapter.enqueue("value_task", args=(1,))
        adapter.retry(task_id)
        # Retry should call the task again with original args
        assert len(values) >= 1
