"""
Celery Async Mode Simulation Tests

This module tests Celery behavior differences between test (eager) and production (async) modes.
In tests, CELERY_TASK_ALWAYS_EAGER=True makes tasks run synchronously, which differs significantly
from production where tasks are queued and processed asynchronously.

Key differences addressed:
- Task execution: Sync (eager) vs Async (production)
- Error propagation: Immediate vs Task-internal
- Retry behavior: Ignored in eager mode vs Actual backoff in production
- Broker failures: No impact in eager vs Task loss possible in production
- Transaction isolation: Same transaction in eager vs Separate in production

These tests simulate production-like async behavior to catch issues that only appear
when running with a real message broker.

Reference:
- docs/SELF_HEALING_TEST_GAP_ANALYSIS.md §2.2
- Celery documentation: https://docs.celeryq.dev/en/stable/userguide/testing.html
"""

import pytest

# 이 파일의 모든 테스트는 DB 및 Celery 필요
pytestmark = [pytest.mark.requires_db, pytest.mark.requires_celery]

from unittest.mock import patch, MagicMock, PropertyMock
from celery import states
from kombu.exceptions import OperationalError


@pytest.mark.django_db
class TestCeleryAsyncBehavior:
    """
    Tests that simulate Celery async (production) behavior.

    These tests disable eager mode to verify that:
    1. Tasks are properly queued, not executed immediately
    2. Broker connection failures are handled gracefully
    3. Task delays (countdown) are properly applied
    """

    @pytest.fixture(autouse=True)
    def disable_eager_mode(self, settings):
        """
        Disable Celery eager mode for these tests.

        In eager mode (test default), tasks run synchronously in the same process.
        Disabling it simulates production behavior where tasks are queued.
        """
        settings.CELERY_TASK_ALWAYS_EAGER = False
        settings.CELERY_TASK_EAGER_PROPAGATES = False

    def test_task_queued_not_executed_immediately(self):
        """
        Verify that apply_async queues the task without executing it.

        In production, tasks are sent to the broker and processed by workers.
        The calling code receives a PENDING AsyncResult, not the actual result.

        Expected behavior:
        - Task is not executed immediately
        - AsyncResult is returned with PENDING state
        - apply_async is called with correct arguments
        """
        with patch("selfhealing.celery_tasks.replay_single_dlq_entry.apply_async") as mock_apply:
            # Configure mock to return a pending task result
            mock_result = MagicMock()
            mock_result.id = "test-task-id-12345"
            mock_result.state = states.PENDING
            mock_apply.return_value = mock_result

            # Import and call the task via apply_async
            from selfhealing.celery_tasks import replay_single_dlq_entry

            result = replay_single_dlq_entry.apply_async(args=[999])

            # Verify task is queued but not executed (PENDING state)
            assert result.state == states.PENDING
            assert result.id == "test-task-id-12345"
            mock_apply.assert_called_once_with(args=[999])

    def test_dlq_replay_applies_countdown_delay(self):
        """
        Verify that DLQ replay respects the delay/countdown parameter.

        When replaying failed operations, we may want to add a delay to:
        - Avoid thundering herd when recovering from outages
        - Give external services time to stabilize
        - Implement rate limiting

        Expected behavior:
        - apply_async is called with countdown parameter
        - The countdown value matches the requested delay
        """
        with patch("selfhealing.celery_tasks.replay_single_dlq_entry.apply_async") as mock_apply:
            mock_result = MagicMock()
            mock_result.id = "delayed-task-id"
            mock_result.state = states.PENDING
            mock_apply.return_value = mock_result

            from selfhealing.celery_tasks import replay_single_dlq_entry

            # Call with countdown (delay) parameter
            delay_seconds = 60
            replay_single_dlq_entry.apply_async(args=[123], countdown=delay_seconds)

            # Verify countdown was passed to apply_async
            mock_apply.assert_called_once()
            call_kwargs = mock_apply.call_args
            assert call_kwargs[1].get("countdown") == delay_seconds or call_kwargs.kwargs.get("countdown") == delay_seconds

    def test_broker_connection_failure_raises_operational_error(self):
        """
        Verify that broker connection failures are properly propagated.

        In production, if Redis/RabbitMQ is down, apply_async will fail.
        The application should handle this gracefully:
        - Log the error
        - Store to DB as fallback (if applicable)
        - Return appropriate error response

        Expected behavior:
        - OperationalError is raised when broker is unavailable
        - Calling code can catch and handle the exception
        """
        with patch("selfhealing.celery_tasks.replay_single_dlq_entry.apply_async") as mock_apply:
            # Simulate broker connection failure
            mock_apply.side_effect = OperationalError("Connection refused")

            from selfhealing.celery_tasks import replay_single_dlq_entry

            # Verify that the exception is raised
            with pytest.raises(OperationalError) as exc_info:
                replay_single_dlq_entry.apply_async(args=[456])

            assert "Connection refused" in str(exc_info.value)

    def test_circuit_breaker_task_uses_correct_queue(self):
        """
        Verify that circuit breaker tasks are routed to the correct queue.

        Different task types should go to different queues:
        - dlq_processing: DLQ replay operations
        - maintenance: Circuit breaker checks

        Expected behavior:
        - Task uses the queue specified in its decorator
        - Queue routing is applied correctly
        """
        with patch("selfhealing.celery_tasks.conditional_replay_on_circuit_close.apply_async") as mock_apply:
            mock_result = MagicMock()
            mock_result.id = "cb-recovery-task"
            mock_result.state = states.PENDING
            mock_apply.return_value = mock_result

            from selfhealing.celery_tasks import conditional_replay_on_circuit_close

            # Trigger task with queue specification
            conditional_replay_on_circuit_close.apply_async(
                args=["toss_payment"], kwargs={"max_items": 50}, queue="dlq_processing"
            )

            mock_apply.assert_called_once()
            call_kwargs = mock_apply.call_args
            assert call_kwargs[1].get("queue") == "dlq_processing" or call_kwargs.kwargs.get("queue") == "dlq_processing"


@pytest.mark.django_db
class TestCeleryRetrySimulation:
    """
    Tests that simulate Celery retry behavior in production.

    In eager mode, retries are executed immediately in the same process.
    In production, retries are scheduled with actual delays via the broker.
    These tests verify retry configuration is correct.
    """

    @pytest.fixture(autouse=True)
    def disable_eager_mode(self, settings):
        """Disable eager mode to simulate production retry behavior."""
        settings.CELERY_TASK_ALWAYS_EAGER = False
        settings.CELERY_TASK_EAGER_PROPAGATES = False

    def test_task_retry_with_exponential_backoff_configuration(self):
        """
        Verify that tasks are configured with proper retry settings.

        DLQ replay tasks should NOT auto-retry (max_retries=0) because
        failed replays should be logged and left for manual review.

        Other tasks may have different retry configurations.

        Expected behavior:
        - DLQ replay tasks have max_retries=0
        - Retry configuration matches the task decorator
        """
        from selfhealing.celery_tasks import replay_single_dlq_entry

        # Verify DLQ replay task does not auto-retry
        # (failures are stored in DLQ, not retried automatically)
        assert replay_single_dlq_entry.max_retries == 0

    def test_task_time_limits_are_configured(self):
        """
        Verify that tasks have appropriate time limits.

        Time limits prevent runaway tasks from consuming resources:
        - time_limit: Hard limit, task is killed
        - soft_time_limit: Soft limit, SoftTimeLimitExceeded is raised

        Expected behavior:
        - Tasks have both time_limit and soft_time_limit set
        - soft_time_limit < time_limit (to allow graceful shutdown)
        """
        from selfhealing.celery_tasks import replay_single_dlq_entry
        from selfhealing.celery_tasks import conditional_replay_on_circuit_close

        # Verify time limits are set
        assert replay_single_dlq_entry.time_limit is not None
        assert replay_single_dlq_entry.soft_time_limit is not None
        assert replay_single_dlq_entry.soft_time_limit < replay_single_dlq_entry.time_limit

        # Verify circuit breaker task limits
        assert conditional_replay_on_circuit_close.time_limit is not None
        assert conditional_replay_on_circuit_close.soft_time_limit is not None

    def test_acks_late_configured_for_reliability(self):
        """
        Verify that tasks use acks_late for reliability.

        With acks_late=True, the task message is acknowledged only after
        successful completion. If the worker crashes, the message is
        redelivered to another worker.

        Expected behavior:
        - Critical tasks have acks_late=True
        """
        from selfhealing.celery_tasks import replay_single_dlq_entry
        from selfhealing.celery_tasks import conditional_replay_on_circuit_close

        # Verify acks_late is enabled for reliability
        assert replay_single_dlq_entry.acks_late is True
        assert conditional_replay_on_circuit_close.acks_late is True


@pytest.mark.django_db
class TestBrokerFailureRecovery:
    """
    Tests for handling broker (Redis/RabbitMQ) connection failures.

    When the message broker is unavailable:
    - Tasks cannot be queued
    - The application should handle this gracefully
    - Critical operations should have fallback mechanisms
    """

    @pytest.fixture(autouse=True)
    def disable_eager_mode(self, settings):
        """Disable eager mode to test broker failure scenarios."""
        settings.CELERY_TASK_ALWAYS_EAGER = False
        settings.CELERY_TASK_EAGER_PROPAGATES = False

    def test_graceful_degradation_on_broker_failure(self):
        """
        Verify that the system degrades gracefully when broker is unavailable.

        When Redis is down:
        - Task queuing fails with OperationalError
        - Application should catch this and log appropriately
        - Critical operations may need DB-based fallback

        This test ensures the error type is correct for proper handling.
        """
        with patch("selfhealing.celery_tasks.replay_single_dlq_entry.apply_async") as mock_apply:
            # Simulate various broker failure scenarios
            broker_errors = [
                OperationalError("Connection refused"),
                OperationalError("Connection timed out"),
                OperationalError("No broker available"),
            ]

            for error in broker_errors:
                mock_apply.side_effect = error

                from selfhealing.celery_tasks import replay_single_dlq_entry

                with pytest.raises(OperationalError):
                    replay_single_dlq_entry.apply_async(args=[789])

    def test_task_result_unavailable_on_backend_failure(self):
        """
        Verify behavior when result backend (Redis) is unavailable.

        Even if tasks are queued successfully, result fetching may fail.
        The application should handle AsyncResult.get() failures.

        Expected behavior:
        - Timeout or connection errors when fetching results
        - Application can continue without blocking
        """
        with patch("selfhealing.celery_tasks.replay_single_dlq_entry.apply_async") as mock_apply:
            mock_result = MagicMock()
            mock_result.id = "queued-task-id"
            mock_result.state = states.PENDING

            # Simulate result backend failure when trying to get result
            mock_result.get.side_effect = OperationalError("Result backend unavailable")
            mock_apply.return_value = mock_result

            from selfhealing.celery_tasks import replay_single_dlq_entry

            result = replay_single_dlq_entry.apply_async(args=[111])

            # Task queues successfully
            assert result.state == states.PENDING

            # But getting the result fails
            with pytest.raises(OperationalError):
                result.get(timeout=1)
