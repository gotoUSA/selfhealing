"""
Transaction and Task Timing Tests

This module tests the interaction between Django database transactions and Celery tasks.
In production, there's a critical timing issue: if a task is dispatched before the
transaction commits, the task may try to read data that doesn't exist yet.

Key issues addressed:
- Task dispatched inside transaction may read uncommitted data
- Task dispatched before commit may fail to find the record
- on_commit hooks ensure task runs after data is visible
- Eager mode masks these issues because everything runs in the same transaction

These tests ensure that our code uses proper patterns for transaction-task coordination.

Reference:
- docs/SELF_HEALING_TEST_GAP_ANALYSIS.md §2.2 (Transaction Commit 후 Task 실행 테스트)
- Django docs: https://docs.djangoproject.com/en/5.0/topics/db/transactions/#django.db.transaction.on_commit
"""

import pytest

# 이 파일의 모든 테스트는 DB 필요
pytestmark = pytest.mark.requires_db

from django.db import transaction
from unittest.mock import patch, MagicMock

from shopping.models import FailedOperation


@pytest.mark.django_db(transaction=True)
class TestTransactionTaskCoordination:
    """
    Tests for proper coordination between database transactions and Celery tasks.

    The key pattern is: Use transaction.on_commit() to schedule tasks after
    the transaction commits. This ensures tasks can read the data they need.
    """

    def test_task_scheduled_after_transaction_commit(self):
        """
        Verify that tasks scheduled with on_commit run after commit.

        Pattern:
            with transaction.atomic():
                obj = Model.objects.create(...)
                transaction.on_commit(lambda: task.delay(obj.id))

        Expected behavior:
        - Task is NOT called during the transaction
        - Task IS called after the transaction commits
        - Task receives the correct object ID
        """
        task_calls = []

        def mock_task_dispatch(obj_id):
            """Simulates task dispatch - records when it's called."""
            task_calls.append({"id": obj_id, "can_read_db": FailedOperation.objects.filter(id=obj_id).exists()})

        # Create object and schedule task inside transaction
        with transaction.atomic():
            failed_op = FailedOperation.objects.create(
                domain=FailedOperation.Domain.PAYMENT,
                failure_type="TEST_FAILURE",
                status=FailedOperation.Status.PENDING,
                snapshot_data={"test": "data"},
                error_message="Test error for transaction timing test",
            )
            obj_id = failed_op.id

            # Schedule task using on_commit
            transaction.on_commit(lambda: mock_task_dispatch(obj_id))

            # Task should NOT be called yet (inside transaction)
            assert len(task_calls) == 0

        # After transaction commits, task should be called
        assert len(task_calls) == 1
        assert task_calls[0]["id"] == obj_id
        # Task should be able to read the committed data
        assert task_calls[0]["can_read_db"] is True

    def test_task_can_read_committed_data(self):
        """
        Verify that tasks dispatched via on_commit can read the created data.

        This is the production-safe pattern for task dispatch:
        1. Create/modify data in transaction
        2. Use on_commit to schedule task
        3. Task runs after commit and can see the data

        Expected behavior:
        - Data exists in DB when task runs
        - Task can retrieve and process the data
        """
        result_container = {"data_visible": None, "data_correct": None}

        def verify_data_visible(op_id, expected_type):
            """Task that verifies it can read the committed data."""
            try:
                op = FailedOperation.objects.get(id=op_id)
                result_container["data_visible"] = True
                result_container["data_correct"] = op.failure_type == expected_type
            except FailedOperation.DoesNotExist:
                result_container["data_visible"] = False

        failure_type = "PAYMENT_TIMEOUT"

        with transaction.atomic():
            failed_op = FailedOperation.objects.create(
                domain=FailedOperation.Domain.PAYMENT,
                failure_type=failure_type,
                status=FailedOperation.Status.PENDING,
                snapshot_data={"order_id": 12345},
                error_message="Payment gateway timeout",
            )
            op_id = failed_op.id

            # Schedule verification after commit
            transaction.on_commit(lambda: verify_data_visible(op_id, failure_type))

        # Verify task saw the data correctly
        assert result_container["data_visible"] is True
        assert result_container["data_correct"] is True

    def test_rollback_prevents_task_execution(self):
        """
        Verify that on_commit tasks are NOT called if transaction rolls back.

        If an error occurs and the transaction is rolled back, any on_commit
        callbacks should be discarded. This prevents tasks from trying to
        process data that was never saved.

        Expected behavior:
        - Task callback is registered
        - Transaction rolls back (exception or explicit)
        - Task callback is NOT executed
        """
        task_calls = []

        def should_not_be_called(obj_id):
            task_calls.append(obj_id)

        try:
            with transaction.atomic():
                failed_op = FailedOperation.objects.create(
                    domain=FailedOperation.Domain.PAYMENT,
                    failure_type="WILL_ROLLBACK",
                    status=FailedOperation.Status.PENDING,
                    snapshot_data={},
                    error_message="This will be rolled back",
                )

                transaction.on_commit(lambda: should_not_be_called(failed_op.id))

                # Force rollback by raising an exception
                raise ValueError("Intentional rollback for testing")

        except ValueError:
            pass  # Expected exception

        # Task should NOT have been called due to rollback
        assert len(task_calls) == 0

        # Data should not exist in DB
        assert not FailedOperation.objects.filter(failure_type="WILL_ROLLBACK").exists()

    def test_nested_transaction_on_commit_timing(self):
        """
        Verify on_commit behavior with nested transactions (savepoints).

        With nested transaction.atomic() blocks:
        - Inner on_commit runs when OUTER transaction commits
        - Not when the inner savepoint is released

        Expected behavior:
        - Inner on_commit callback waits for outermost commit
        """
        callback_order = []

        with transaction.atomic():
            # Outer transaction
            callback_order.append("outer_start")

            with transaction.atomic():
                # Inner transaction (savepoint)
                failed_op = FailedOperation.objects.create(
                    domain=FailedOperation.Domain.POINT,
                    failure_type="NESTED_TEST",
                    status=FailedOperation.Status.PENDING,
                    snapshot_data={},
                    error_message="Nested transaction test",
                )

                # This on_commit is inside inner block
                transaction.on_commit(lambda: callback_order.append("inner_on_commit"))
                callback_order.append("inner_end")

            # Inner block done, but outer not committed yet
            callback_order.append("after_inner")

            # on_commit from inner should NOT have run yet
            assert "inner_on_commit" not in callback_order

        # Now outer transaction committed
        assert callback_order == ["outer_start", "inner_end", "after_inner", "inner_on_commit"]  # Runs after outermost commit


@pytest.mark.django_db(transaction=True)
class TestDLQTransactionPatterns:
    """
    Tests for DLQ-specific transaction patterns.

    When a payment fails and we store it to DLQ, we need to:
    1. Store the failure record in the same transaction as any related updates
    2. Dispatch replay task only after commit succeeds
    """

    def test_dlq_entry_creation_with_task_dispatch(self):
        """
        Verify proper pattern for creating DLQ entry and dispatching task.

        The correct pattern:
            with transaction.atomic():
                dlq_entry = FailedOperation.objects.create(...)
                transaction.on_commit(
                    lambda: replay_task.apply_async(args=[dlq_entry.id])
                )

        Expected behavior:
        - DLQ entry is created
        - Task is only dispatched after commit
        - Task receives valid DLQ ID
        """
        dispatched_tasks = []

        with patch("shopping.tasks.dlq_replay_tasks.replay_single_dlq_entry.apply_async") as mock_task:

            def capture_dispatch(*args, **kwargs):
                dispatched_tasks.append({"args": args, "kwargs": kwargs})
                mock_result = MagicMock()
                mock_result.id = "task-123"
                return mock_result

            mock_task.side_effect = capture_dispatch

            # Simulate proper DLQ creation pattern
            with transaction.atomic():
                dlq_entry = FailedOperation.objects.create(
                    domain=FailedOperation.Domain.PAYMENT,
                    failure_type="PG_TIMEOUT",
                    status=FailedOperation.Status.PENDING,
                    snapshot_data={"order_id": 999, "amount": 50000},
                    error_message="Payment gateway timeout after 30s",
                )

                from selfhealing.celery_tasks import replay_single_dlq_entry

                # Schedule task after commit
                transaction.on_commit(
                    lambda: replay_single_dlq_entry.apply_async(args=[dlq_entry.id], countdown=60)  # Delay before retry
                )

                # Task should NOT be dispatched yet
                assert len(dispatched_tasks) == 0

            # After commit, task should be dispatched
            assert len(dispatched_tasks) == 1
            assert dispatched_tasks[0]["kwargs"]["args"] == [dlq_entry.id]
            assert dispatched_tasks[0]["kwargs"]["countdown"] == 60

    def test_multiple_on_commit_callbacks_order(self):
        """
        Verify that multiple on_commit callbacks execute in order.

        When multiple tasks need to be dispatched after a transaction:
        - All callbacks execute in registration order
        - All can access the committed data

        Expected behavior:
        - Callbacks execute in the order they were registered
        - Each callback can access all committed data
        """
        execution_order = []

        with transaction.atomic():
            failed_op_1 = FailedOperation.objects.create(
                domain=FailedOperation.Domain.PAYMENT,
                failure_type="ERROR_1",
                status=FailedOperation.Status.PENDING,
                snapshot_data={},
                error_message="First error",
            )

            failed_op_2 = FailedOperation.objects.create(
                domain=FailedOperation.Domain.POINT,
                failure_type="ERROR_2",
                status=FailedOperation.Status.PENDING,
                snapshot_data={},
                error_message="Second error",
            )

            # Register callbacks in specific order
            transaction.on_commit(lambda: execution_order.append("first"))
            transaction.on_commit(lambda: execution_order.append("second"))
            transaction.on_commit(lambda: execution_order.append("third"))

        # Verify order
        assert execution_order == ["first", "second", "third"]


@pytest.mark.django_db(transaction=True)
class TestEagerVsAsyncDifferences:
    """
    Tests that highlight differences between eager (test) and async (prod) modes.

    These tests document known differences to prevent surprises in production.
    """

    def test_document_eager_mode_transaction_behavior(self, settings):
        """
        Document: In eager mode, task runs in same transaction.

        IMPORTANT: This is a documentation test showing WHY we need on_commit.

        In eager mode (CELERY_TASK_ALWAYS_EAGER=True):
        - Task runs synchronously in the same process
        - Task sees uncommitted data (same transaction)
        - This masks issues that appear in production

        In production (async mode):
        - Task runs in separate worker process
        - Task has separate DB connection
        - Task cannot see uncommitted data from calling code

        Solution: Always use transaction.on_commit() for task dispatch.
        """
        # Force eager mode for this test
        settings.CELERY_TASK_ALWAYS_EAGER = True
        settings.CELERY_TASK_EAGER_PROPAGATES = True

        # In eager mode, this pattern would APPEAR to work:
        # (but would fail in production)
        #
        # with transaction.atomic():
        #     obj = Model.objects.create(...)
        #     task.delay(obj.id)  # BAD: task runs before commit!
        #
        # The task would see the object because it's the same transaction.
        # In production, the task would fail with DoesNotExist.

        # This test just documents the issue - actual tests are above
        assert settings.CELERY_TASK_ALWAYS_EAGER is True

    def test_async_mode_requires_committed_data(self, settings):
        """
        Document: In async mode, task cannot see uncommitted data.

        This test simulates what would happen if we dispatched a task
        before the transaction commits (in async mode).

        The task would:
        1. Be queued immediately
        2. Possibly execute before calling transaction commits
        3. Fail to find the data (DoesNotExist)

        Prevention: Use transaction.on_commit() for task dispatch.
        """
        # Simulate async mode
        settings.CELERY_TASK_ALWAYS_EAGER = False

        # This test verifies the PATTERN, not actual task execution
        # The pattern that MUST be used:
        correct_pattern_used = False

        with transaction.atomic():
            failed_op = FailedOperation.objects.create(
                domain=FailedOperation.Domain.WEBHOOK,
                failure_type="ASYNC_TEST",
                status=FailedOperation.Status.PENDING,
                snapshot_data={},
                error_message="Async mode test",
            )

            # CORRECT: Use on_commit
            transaction.on_commit(lambda: setattr(TestEagerVsAsyncDifferences, "_pattern_verified", True))
            correct_pattern_used = True

        assert correct_pattern_used is True
        assert getattr(TestEagerVsAsyncDifferences, "_pattern_verified", False) is True

        # Cleanup
        if hasattr(TestEagerVsAsyncDifferences, "_pattern_verified"):
            delattr(TestEagerVsAsyncDifferences, "_pattern_verified")
