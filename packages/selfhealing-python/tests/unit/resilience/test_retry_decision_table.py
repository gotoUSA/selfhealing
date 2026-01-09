"""
Unit Tests for Retry Decision Table

Tests for retry policy decisions based on error types,
attempt counts, and configuration.

Reference: docs/testing/SELF_HEALING_TEST_SPECIFICATIONS.md
Risk Covered: R-015 (Retry storm overwhelming PG)
"""

import pytest

from selfhealing.services.retry_handler import (
    RetryAction,
    RetryConfig,
    RetryHandler,
    RetryResult,
)


@pytest.mark.tier1
class TestRetryConfigDefaults:
    """
    Tests for default retry configuration.

    Purpose:
        Verify that default retry settings match documented policy.
    """

    def test_default_max_attempts_is_3(self):
        """
        Purpose:
            Verify default max attempts matches architecture spec.

        Expected:
            - max_attempts = 3

        Risk Covered:
            R-015: Retry limit prevents storm
        """
        config = RetryConfig()
        assert config.max_attempts == 3, (
            "Policy Violation: Default max attempts must be 3. " "Check RETRY_MAX_ATTEMPTS configuration."
        )

    def test_default_backoff_base_is_4(self):
        """
        Purpose:
            Verify backoff base matches policy.

        Expected:
            - backoff_base = 4
        """
        config = RetryConfig()
        assert config.backoff_base == 4, (
            "Policy Violation: Default backoff base must be 4. " "Check RETRY_BACKOFF_BASE configuration."
        )

    def test_default_backoff_max_is_180(self):
        """
        Purpose:
            Verify max backoff matches policy.

        Expected:
            - backoff_max = 180 seconds
        """
        config = RetryConfig()
        assert config.backoff_max == 180, (
            "Policy Violation: Default backoff max must be 180 seconds. " "Check RETRY_BACKOFF_MAX configuration."
        )

    def test_default_dlq_enabled(self):
        """
        Purpose:
            Verify DLQ routing is enabled by default.

        Expected:
            - enable_dlq = True
        """
        config = RetryConfig()
        assert config.enable_dlq is True, (
            "Policy Violation: DLQ must be enabled by default. " "DLQ captures exhausted retries for manual review."
        )


@pytest.mark.tier1
class TestRetryDecisionWithinAttempts:
    """
    Tests for retry decisions within max attempts.

    Purpose:
        Verify retry allowed when attempts remain.
    """

    def test_should_retry_on_first_attempt(self):
        """
        Purpose:
            Verify first failure triggers retry.

        Scenario:
            1. Configure max_attempts=3
            2. Check should_retry for attempt 1
            3. Should return True

        Expected:
            - Retry allowed (2 more attempts remain)
        """
        config = RetryConfig(max_attempts=3)
        handler = RetryHandler(config=config)

        result = handler.should_retry(Exception("test"), attempt=1)

        assert result is True, "First attempt should allow retry. " "2 more attempts remain with max_attempts=3."

    def test_should_retry_on_second_attempt(self):
        """
        Purpose:
            Verify second failure triggers retry.
        """
        config = RetryConfig(max_attempts=3)
        handler = RetryHandler(config=config)

        result = handler.should_retry(Exception("test"), attempt=2)

        assert result is True, "Second attempt should allow retry. " "1 more attempt remains with max_attempts=3."

    def test_should_not_retry_at_max_attempts(self):
        """
        Purpose:
            Verify retry blocked at max attempts.

        Scenario:
            1. Configure max_attempts=3
            2. Check should_retry for attempt 3
            3. Should return False (exhausted)

        Expected:
            - Retry blocked (attempts exhausted)
        """
        config = RetryConfig(max_attempts=3)
        handler = RetryHandler(config=config)

        result = handler.should_retry(Exception("test"), attempt=3)

        assert result is False, "Retry should be blocked at max_attempts. " "No more retries allowed after attempt 3."

    def test_should_not_retry_beyond_max_attempts(self):
        """
        Purpose:
            Verify retry blocked beyond max attempts.
        """
        config = RetryConfig(max_attempts=3)
        handler = RetryHandler(config=config)

        result = handler.should_retry(Exception("test"), attempt=4)

        assert result is False, "Retry should be blocked beyond max_attempts."


@pytest.mark.tier1
class TestRetryDecisionExceptionTypes:
    """
    Tests for retry decisions based on exception type.

    Purpose:
        Validate exception classification logic.
    """

    def test_non_retryable_exception_blocks_retry(self):
        """
        Purpose:
            Verify non-retryable exceptions block retry immediately.

        Scenario:
            1. Define PermanentError as non-retryable
            2. Check should_retry for PermanentError on attempt 1
            3. Should return False despite attempts remaining

        Expected:
            - Retry blocked due to exception type
        """

        class PermanentError(Exception):
            pass

        config = RetryConfig(
            max_attempts=3,
            non_retryable_exceptions=(PermanentError,),
        )
        handler = RetryHandler(config=config)

        result = handler.should_retry(PermanentError("permanent"), attempt=1)

        assert result is False, (
            "Non-retryable exception should block retry immediately. " "PermanentError is configured as non-retryable."
        )

    def test_retryable_exception_allows_retry(self):
        """
        Purpose:
            Verify retryable exceptions allow retry.
        """

        class TransientError(Exception):
            pass

        config = RetryConfig(
            max_attempts=3,
            retryable_exceptions=(TransientError,),
        )
        handler = RetryHandler(config=config)

        result = handler.should_retry(TransientError("transient"), attempt=1)

        assert result is True, "Retryable exception should allow retry. " "TransientError is configured as retryable."

    def test_generic_exception_is_retryable_by_default(self):
        """
        Purpose:
            Verify generic Exception is retryable by default.

        Expected:
            - Exception (base class) is in default retryable_exceptions
        """
        config = RetryConfig()

        assert Exception in config.retryable_exceptions, "Base Exception should be in default retryable_exceptions."


@pytest.mark.tier1
class TestRetryResultProperties:
    """
    Tests for RetryResult dataclass properties.

    Purpose:
        Validate result object behavior.
    """

    def test_success_result_properties(self):
        """
        Purpose:
            Verify success result has correct properties.
        """
        result = RetryResult(
            success=True,
            action=RetryAction.SUCCESS,
            attempt=1,
            value="result_value",
        )

        assert result.success is True
        assert result.should_retry is False
        assert result.was_retried is False
        assert result.value == "result_value"

    def test_retry_result_properties(self):
        """
        Purpose:
            Verify retry result has correct properties.
        """
        result = RetryResult(
            success=False,
            action=RetryAction.RETRY,
            attempt=2,
            next_delay=16,
        )

        assert result.success is False
        assert result.should_retry is True
        assert result.was_retried is True
        assert result.next_delay == 16

    def test_dlq_result_properties(self):
        """
        Purpose:
            Verify DLQ result has correct properties.
        """
        result = RetryResult(
            success=False,
            action=RetryAction.DLQ,
            attempt=3,
            dlq_id=12345,
        )

        assert result.success is False
        assert result.should_retry is False
        assert result.dlq_id == 12345

    def test_abort_result_properties(self):
        """
        Purpose:
            Verify abort result has correct properties.
        """
        result = RetryResult(
            success=False,
            action=RetryAction.ABORT,
            attempt=1,
        )

        assert result.success is False
        assert result.should_retry is False
        assert result.action == RetryAction.ABORT


@pytest.mark.tier1
class TestRetryActionEnum:
    """
    Tests for RetryAction enumeration.

    Purpose:
        Validate all action types are defined.
    """

    def test_all_actions_defined(self):
        """
        Purpose:
            Verify all expected actions exist.
        """
        expected_actions = {"RETRY", "DLQ", "ABORT", "SUCCESS"}
        actual_actions = {a.name for a in RetryAction}

        assert expected_actions == actual_actions, (
            f"Missing actions: {expected_actions - actual_actions}. "
            f"Unexpected actions: {actual_actions - expected_actions}."
        )

    def test_action_values(self):
        """
        Purpose:
            Verify action values match expected strings.
        """
        assert RetryAction.RETRY.value == "retry"
        assert RetryAction.DLQ.value == "dlq"
        assert RetryAction.ABORT.value == "abort"
        assert RetryAction.SUCCESS.value == "success"


@pytest.mark.tier1
class TestRetryHandlerDelayCalculation:
    """
    Tests for delay calculation in retry handler.

    Purpose:
        Validate backoff integration.
    """

    def test_get_next_delay_uses_backoff(self):
        """
        Purpose:
            Verify delay calculation uses backoff algorithm.
        """
        config = RetryConfig(
            backoff_base=4,
            backoff_max=180,
            jitter_percent=0,
        )
        handler = RetryHandler(config=config)

        assert handler.get_next_delay(1) == 4  # 4^1
        assert handler.get_next_delay(2) == 16  # 4^2
        assert handler.get_next_delay(3) == 64  # 4^3

    def test_get_next_delay_respects_max(self):
        """
        Purpose:
            Verify max delay is enforced.
        """
        config = RetryConfig(
            backoff_base=4,
            backoff_max=50,
            jitter_percent=0,
        )
        handler = RetryHandler(config=config)

        # 4^3 = 64, should be capped at 50
        delay = handler.get_next_delay(3)

        assert delay == 50, f"Max delay not enforced: expected 50, got {delay}."


@pytest.mark.tier1
class TestRetryDecisionTable:
    """
    Tests for complete retry decision table.

    Purpose:
        Validate all decision combinations.

    Reference:
        docs/testing/SELF_HEALING_TEST_MATRICES.md §2.1
    """

    @pytest.mark.parametrize(
        "attempt,max_attempts,is_retryable,expected",
        [
            (1, 3, True, True),  # First attempt, retryable
            (2, 3, True, True),  # Second attempt, retryable
            (3, 3, True, False),  # At max, retryable but exhausted
            (1, 3, False, False),  # First attempt, non-retryable
            (1, 1, True, False),  # Single attempt, already at max
            (0, 3, True, True),  # Edge: attempt 0 treated as < max
        ],
    )
    def test_decision_table(self, attempt, max_attempts, is_retryable, expected):
        """
        Purpose:
            Verify all retry decision combinations.

        Decision Table:
            | Attempt | Max | Retryable | Decision |
            |---------|-----|-----------|----------|
            | 1       | 3   | True      | Retry    |
            | 2       | 3   | True      | Retry    |
            | 3       | 3   | True      | DLQ      |
            | 1       | 3   | False     | DLQ      |
        """

        class TestError(Exception):
            pass

        if is_retryable:
            config = RetryConfig(
                max_attempts=max_attempts,
                retryable_exceptions=(TestError,),
            )
        else:
            config = RetryConfig(
                max_attempts=max_attempts,
                non_retryable_exceptions=(TestError,),
            )

        handler = RetryHandler(config=config)
        result = handler.should_retry(TestError("test"), attempt=attempt)

        assert result is expected, (
            f"Decision table mismatch: "
            f"attempt={attempt}, max={max_attempts}, retryable={is_retryable} "
            f"expected {expected}, got {result}."
        )
