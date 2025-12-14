"""
Unit Tests for Self-Healing Components

Tests for backoff calculator, retry handler policy logic,
and idempotency key generation without database dependencies.

Reference: docs/L3_SELF_HEALING_ARCHITECTURE.md §7, §8
"""

from decimal import Decimal

import pytest

from selfhealing.core import (
    BackoffCalculator,
    BackoffConfig,
    calculate_backoff,
)
from selfhealing.services import (
    IdempotencyDomain,
    IdempotencyKey,
)
from selfhealing.services import (
    RetryAction,
    RetryConfig,
    RetryHandler,
    RetryResult,
)


# =============================================================================
# Backoff Calculator Unit Tests
# =============================================================================


class TestBackoffConfig:
    """Tests for BackoffConfig dataclass."""

    def test_default_values(self):
        """
        Purpose:
            Verify default configuration values.
        """
        config = BackoffConfig()

        assert config.base == 4
        assert config.max_delay == 180
        assert config.jitter_percent == 25
        assert config.min_delay == 1

    def test_custom_values(self):
        """
        Purpose:
            Verify custom configuration is applied.
        """
        config = BackoffConfig(
            base=2,
            max_delay=60,
            jitter_percent=10,
            min_delay=5,
        )

        assert config.base == 2
        assert config.max_delay == 60
        assert config.jitter_percent == 10
        assert config.min_delay == 5


class TestBackoffCalculatorUnit:
    """Unit tests for BackoffCalculator."""

    def test_calculate_attempt_zero_returns_min(self):
        """
        Purpose:
            Verify attempt 0 or negative returns minimum delay.
        """
        config = BackoffConfig(base=4, min_delay=1)
        calc = BackoffCalculator(config)

        assert calc.calculate(0, with_jitter=False) == 1
        assert calc.calculate(-1, with_jitter=False) == 1

    def test_calculate_without_jitter_is_deterministic(self):
        """
        Purpose:
            Verify calculation without jitter is deterministic.
        """
        config = BackoffConfig(base=4, max_delay=180, jitter_percent=25)
        calc = BackoffCalculator(config)

        # Same attempt should give same result without jitter
        delays = [calc.calculate(2, with_jitter=False) for _ in range(10)]

        assert all(d == 16 for d in delays)

    def test_delays_sequence_is_correct(self):
        """
        Purpose:
            Verify full delay sequence matches expectation.
        """
        config = BackoffConfig(base=2, max_delay=100, jitter_percent=0)
        calc = BackoffCalculator(config)

        sequence = calc.get_delays_sequence(5, with_jitter=False)

        # 2^1=2, 2^2=4, 2^3=8, 2^4=16, 2^5=32
        assert sequence == [2, 4, 8, 16, 32]

    def test_max_delay_capping_in_sequence(self):
        """
        Purpose:
            Verify max_delay is applied correctly in sequence.
        """
        config = BackoffConfig(base=4, max_delay=50, jitter_percent=0)
        calc = BackoffCalculator(config)

        # 4^1=4, 4^2=16, 4^3=64->50 (capped), 4^4=256->50 (capped)
        sequence = calc.get_delays_sequence(4, with_jitter=False)

        assert sequence == [4, 16, 50, 50]


class TestCalculateBackoffFunction:
    """Tests for convenience function."""

    def test_basic_calculation(self):
        """
        Purpose:
            Verify convenience function basic calculation.
        """
        result = calculate_backoff(
            attempt=1,
            base=4,
            max_delay=180,
            jitter_percent=0,
        )

        assert result == 4

    def test_with_different_base(self):
        """
        Purpose:
            Verify different base values work correctly.
        """
        result = calculate_backoff(
            attempt=3,
            base=2,
            max_delay=180,
            jitter_percent=0,
        )

        assert result == 8  # 2^3


# =============================================================================
# Retry Handler Unit Tests
# =============================================================================


class TestRetryConfig:
    """Tests for RetryConfig dataclass."""

    def test_default_values(self):
        """
        Purpose:
            Verify default configuration.
        """
        config = RetryConfig()

        assert config.max_attempts == 3
        assert config.backoff_base == 4
        assert config.backoff_max == 180
        assert config.enable_dlq is True

    def test_retryable_exceptions_default(self):
        """
        Purpose:
            Verify default retryable exceptions.
        """
        config = RetryConfig()

        # By default, all exceptions are retryable
        assert Exception in config.retryable_exceptions


class TestRetryResult:
    """Tests for RetryResult dataclass."""

    def test_success_result(self):
        """
        Purpose:
            Verify success result properties.
        """
        result = RetryResult(
            success=True,
            action=RetryAction.SUCCESS,
            attempt=1,
            value="result",
        )

        assert result.success is True
        assert result.should_retry is False
        assert result.was_retried is False

    def test_retry_result(self):
        """
        Purpose:
            Verify retry result properties.
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

    def test_dlq_result(self):
        """
        Purpose:
            Verify DLQ result properties.
        """
        result = RetryResult(
            success=False,
            action=RetryAction.DLQ,
            attempt=3,
            dlq_id=123,
        )

        assert result.success is False
        assert result.should_retry is False
        assert result.dlq_id == 123


class TestRetryHandlerShouldRetry:
    """Tests for should_retry logic."""

    def test_should_retry_within_max_attempts(self):
        """
        Purpose:
            Verify retry allowed within max attempts.
        """
        config = RetryConfig(max_attempts=3)
        handler = RetryHandler(config=config)

        assert handler.should_retry(Exception("test"), attempt=1) is True
        assert handler.should_retry(Exception("test"), attempt=2) is True

    def test_should_not_retry_at_max_attempts(self):
        """
        Purpose:
            Verify retry blocked at max attempts.
        """
        config = RetryConfig(max_attempts=3)
        handler = RetryHandler(config=config)

        assert handler.should_retry(Exception("test"), attempt=3) is False

    def test_should_not_retry_non_retryable_exception(self):
        """
        Purpose:
            Verify non-retryable exceptions block retry.
        """

        class PermanentError(Exception):
            pass

        config = RetryConfig(
            max_attempts=3,
            non_retryable_exceptions=(PermanentError,),
        )
        handler = RetryHandler(config=config)

        assert handler.should_retry(PermanentError("test"), attempt=1) is False

    def test_should_retry_retryable_exception(self):
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

        assert handler.should_retry(TransientError("test"), attempt=1) is True


class TestRetryHandlerGetNextDelay:
    """Tests for delay calculation."""

    def test_get_next_delay(self):
        """
        Purpose:
            Verify delay calculation uses backoff calculator.
        """
        config = RetryConfig(
            backoff_base=4,
            backoff_max=180,
            jitter_percent=0,
        )
        handler = RetryHandler(config=config)

        assert handler.get_next_delay(1) == 4
        assert handler.get_next_delay(2) == 16
        assert handler.get_next_delay(3) == 64


# =============================================================================
# Idempotency Key Unit Tests
# =============================================================================


class TestIdempotencyKey:
    """Tests for IdempotencyKey generation."""

    def test_payment_key_format(self):
        """
        Purpose:
            Verify payment idempotency key format.
        """
        key = IdempotencyKey.for_payment(order_id=12345, amount=50000)

        assert key.domain == IdempotencyDomain.PAYMENT
        assert "12345" in key.key
        assert "50000" in key.key
        assert "idempotency:payment:" in key.cache_key

    def test_payment_confirm_key_format(self):
        """
        Purpose:
            Verify payment confirmation key format.
        """
        key = IdempotencyKey.for_payment_confirm(
            payment_key="pay_abc123",
            order_id=12345,
            amount=50000,
        )

        assert key.domain == IdempotencyDomain.PAYMENT
        assert "pay_abc123" in key.key
        assert "confirm" in key.key

    def test_webhook_key_format(self):
        """
        Purpose:
            Verify webhook idempotency key format.
        """
        key = IdempotencyKey.for_webhook(event_id="evt_webhook_123")

        assert key.domain == IdempotencyDomain.WEBHOOK
        assert key.key == "evt_webhook_123"
        assert "idempotency:webhook:" in key.cache_key

    def test_point_operation_key_format(self):
        """
        Purpose:
            Verify point operation key format.
        """
        key = IdempotencyKey.for_point_operation(
            order_id=12345,
            point_type="earn",
            amount=500,
        )

        assert key.domain == IdempotencyDomain.POINT
        assert "12345" in key.key
        assert "earn" in key.key
        assert "500" in key.key

    def test_inventory_key_format(self):
        """
        Purpose:
            Verify inventory operation key format.
        """
        key = IdempotencyKey.for_inventory(
            order_item_id=999,
            action="deduct",
        )

        assert key.domain == IdempotencyDomain.INVENTORY
        assert "999" in key.key
        assert "deduct" in key.key

    def test_key_hash_is_consistent(self):
        """
        Purpose:
            Verify same inputs produce same hash.
        """
        key1 = IdempotencyKey.for_payment(order_id=123, amount=10000)
        key2 = IdempotencyKey.for_payment(order_id=123, amount=10000)

        assert key1.hash == key2.hash

    def test_different_inputs_produce_different_hash(self):
        """
        Purpose:
            Verify different inputs produce different hash.
        """
        key1 = IdempotencyKey.for_payment(order_id=123, amount=10000)
        key2 = IdempotencyKey.for_payment(order_id=124, amount=10000)

        assert key1.hash != key2.hash


# =============================================================================
# Policy Classification Tests
# =============================================================================


class TestFailureClassification:
    """Tests for failure type to policy mapping."""

    def test_pg_timeout_is_retryable(self):
        """
        Purpose:
            Verify PG timeout errors are retryable.
        """
        from shopping.constants import TOSS_RETRYABLE_ERRORS

        assert "TIMEOUT" in TOSS_RETRYABLE_ERRORS
        assert "NETWORK_ERROR" in TOSS_RETRYABLE_ERRORS

    def test_invalid_card_is_not_retryable(self):
        """
        Purpose:
            Verify invalid card errors are not retryable.
        """
        from shopping.constants import TOSS_NON_RETRYABLE_ERRORS

        assert "INVALID_CARD_NUMBER" in TOSS_NON_RETRYABLE_ERRORS
        assert "INVALID_CARD_EXPIRATION" in TOSS_NON_RETRYABLE_ERRORS

    def test_already_processed_is_not_retryable(self):
        """
        Purpose:
            Verify already processed errors are not retryable.
        """
        from shopping.constants import TOSS_NON_RETRYABLE_ERRORS

        assert "ALREADY_PROCESSED_PAYMENT" in TOSS_NON_RETRYABLE_ERRORS
        assert "ALREADY_CANCELED_PAYMENT" in TOSS_NON_RETRYABLE_ERRORS

    def test_server_errors_are_retryable(self):
        """
        Purpose:
            Verify server errors (5xx) are retryable.
        """
        from shopping.constants import TOSS_RETRYABLE_ERRORS

        assert "FAILED_INTERNAL_SYSTEM_PROCESSING" in TOSS_RETRYABLE_ERRORS
        assert "COMMON_ERROR" in TOSS_RETRYABLE_ERRORS
