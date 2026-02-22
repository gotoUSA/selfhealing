"""
Tests for RetryHandler.
retry_handler.py의 RetryHandler, RetryConfig, RetryResult, with_retry 데코레이터 등을 검증합니다.
Kill Switch, ErrorBudgetGate, 429 감지, should_retry 로직 등을 테스트합니다.
"""

from unittest.mock import MagicMock, patch

import pytest

from selfhealing.services.retry_handler import (
    MaxRetriesExceededError,
    RetryAction,
    RetryConfig,
    RetryHandler,
    RetryResult,
    with_retry,
)

# =============================================================================
# RetryConfig Tests
# =============================================================================


class TestRetryConfig:
    """RetryConfig 데이터클래스 테스트."""

    def test_default_values(self):
        """Default values
        RetryConfig 기본값이 올바르게 초기화되는지 확인.
        """
        config = RetryConfig()
        assert config.max_attempts == 3
        assert config.backoff_base == 4
        assert config.backoff_max == 180
        assert config.enable_dlq is True
        assert config.domain == "default"

    def test_custom_values(self):
        """Custom values
        커스텀 값이 올바르게 설정되는지 확인.
        """
        config = RetryConfig(
            max_attempts=5,
            backoff_base=2,
            backoff_max=60,
            domain="payment",
            retryable_exceptions=(ConnectionError, TimeoutError),
            non_retryable_exceptions=(ValueError,),
        )
        assert config.max_attempts == 5
        assert config.domain == "payment"
        assert config.retryable_exceptions == (ConnectionError, TimeoutError)

    def test_rate_limit_aware_defaults(self):
        """Rate limit aware defaults
        Rate Limit 관련 기본값이 올바른지 확인.
        """
        config = RetryConfig()
        assert config.rate_limit_aware is True
        assert config.rate_limit_key is None

    def test_throttle_aware_defaults(self):
        """Throttle aware defaults
        Throttle 관련 기본값이 올바른지 확인.
        """
        config = RetryConfig()
        assert config.throttle_aware is True
        assert config.throttle_backoff_multiplier_cap == 4.0

    def test_critical_tier_defaults(self):
        """Critical tier defaults
        CRITICAL 티어 관련 기본값이 올바른지 확인.
        """
        config = RetryConfig()
        assert config.critical_tier_full_stop_grace_retries == 1
        assert config.critical_tier_full_stop_max_delay == 720


# =============================================================================
# RetryResult Tests
# =============================================================================


class TestRetryResult:
    """RetryResult 데이터클래스 테스트."""

    def test_should_retry_true(self):
        """Should retry true
        action이 RETRY일 때 should_retry=True인지 확인.
        """
        result = RetryResult(success=False, action=RetryAction.RETRY, attempt=1)
        assert result.should_retry is True

    def test_should_retry_false_on_success(self):
        """Should retry false on success
        action이 SUCCESS일 때 should_retry=False인지 확인.
        """
        result = RetryResult(success=True, action=RetryAction.SUCCESS, attempt=1)
        assert result.should_retry is False

    def test_was_retried(self):
        """Was retried
        attempt > 1이면 was_retried=True인지 확인.
        """
        result = RetryResult(success=True, action=RetryAction.SUCCESS, attempt=3)
        assert result.was_retried is True

    def test_was_not_retried_first_attempt(self):
        """Was not retried first attempt
        첫 시도(attempt=1)에서 was_retried=False인지 확인.
        """
        result = RetryResult(success=True, action=RetryAction.SUCCESS, attempt=1)
        assert result.was_retried is False

    def test_dlq_action(self):
        """DLQ action
        DLQ 이동 결과에 dlq_id가 포함되는지 확인.
        """
        result = RetryResult(success=False, action=RetryAction.DLQ, attempt=3, dlq_id=42)
        assert result.action == RetryAction.DLQ
        assert result.dlq_id == 42


# =============================================================================
# MaxRetriesExceededError Tests
# =============================================================================


class TestMaxRetriesExceededError:
    """MaxRetriesExceededError 예외 테스트."""

    def test_attributes(self):
        """Attributes
        예외의 속성값이 올바르게 설정되는지 확인.
        """
        err = MaxRetriesExceededError(
            "Max retries",
            retry_count=3,
            max_retries=3,
            last_error=ValueError("timeout"),
        )
        assert err.retry_count == 3
        assert err.max_retries == 3
        assert isinstance(err.last_error, ValueError)


# =============================================================================
# RetryHandler.should_retry Tests
# =============================================================================


class TestRetryHandlerShouldRetry:
    """RetryHandler.should_retry 판단 로직 테스트."""

    def _make_handler(self, **config_kwargs):
        """throttle_aware=False로 핸들러를 생성 (외부 의존성 제거)."""
        config = RetryConfig(**config_kwargs)
        return RetryHandler(config=config, throttle_aware=False)

    def test_retry_on_retryable_exception(self):
        """Retry on retryable exception
        retryable_exception에 해당하면 재시도되는지 확인.
        """
        handler = self._make_handler(
            max_attempts=3,
            retryable_exceptions=(ConnectionError,),
        )
        assert handler.should_retry(ConnectionError(), attempt=1) is True

    def test_no_retry_on_non_retryable_exception(self):
        """No retry on non-retryable exception
        non_retryable_exception에 해당하면 재시도하지 않는지 확인.
        """
        handler = self._make_handler(
            max_attempts=3,
            non_retryable_exceptions=(ValueError,),
        )
        assert handler.should_retry(ValueError(), attempt=1) is False

    def test_no_retry_on_max_attempts_reached(self):
        """No retry on max attempts reached
        최대 시도 횟수에 도달하면 재시도하지 않는지 확인.
        """
        handler = self._make_handler(max_attempts=3)
        assert handler.should_retry(Exception(), attempt=3) is False

    def test_retry_below_max_attempts(self):
        """Retry below max attempts
        최대 시도 횟수 이하에서는 재시도되는지 확인.
        """
        handler = self._make_handler(max_attempts=3)
        assert handler.should_retry(Exception(), attempt=2) is True

    def test_non_retryable_takes_precedence(self):
        """Non-retryable takes precedence
        retryable이면서 동시에 non-retryable이면 재시도하지 않는지 확인.
        """
        handler = self._make_handler(
            max_attempts=3,
            retryable_exceptions=(Exception,),
            non_retryable_exceptions=(ValueError,),
        )
        assert handler.should_retry(ValueError("bad"), attempt=1) is False


# =============================================================================
# RetryHandler.is_rate_limit_error Tests
# =============================================================================


class TestRetryHandlerRateLimitDetection:
    """RetryHandler.is_rate_limit_error 429 감지 테스트."""

    def _make_handler(self):
        config = RetryConfig(max_attempts=3)
        return RetryHandler(config=config, throttle_aware=False)

    def test_detect_429_in_message(self):
        """Detect 429 in message
        에러 메시지에 429가 포함되면 감지하는지 확인.
        """
        handler = self._make_handler()
        is_rate, _ = handler.is_rate_limit_error(Exception("HTTP 429 Too Many Requests"))
        assert is_rate is True

    def test_detect_rate_limit_keyword(self):
        """Detect rate limit keyword
        'rate limit' 키워드를 감지하는지 확인.
        """
        handler = self._make_handler()
        is_rate, _ = handler.is_rate_limit_error(Exception("Rate limit exceeded"))
        assert is_rate is True

    def test_normal_error_not_detected(self):
        """Normal error not detected
        일반 에러는 429로 감지하지 않는지 확인.
        """
        handler = self._make_handler()
        is_rate, _ = handler.is_rate_limit_error(ValueError("Invalid input"))
        assert is_rate is False

    def test_extract_retry_after_attribute(self):
        """Extract retry-after attribute
        예외에 retry_after 속성이 있으면 추출하는지 확인.
        """
        handler = self._make_handler()
        err = Exception("429")
        err.retry_after = 30
        is_rate, retry_after = handler.is_rate_limit_error(err)
        assert is_rate is True
        assert retry_after == 30

    def test_detect_throttle_keyword(self):
        """Detect throttle keyword
        'throttle' 키워드를 감지하는지 확인.
        """
        handler = self._make_handler()
        is_rate, _ = handler.is_rate_limit_error(Exception("Request throttled"))
        assert is_rate is True


# =============================================================================
# RetryHandler.execute Tests
# =============================================================================


class TestRetryHandlerExecute:
    """RetryHandler.execute 메서드 테스트."""

    def _make_handler(self, **config_kwargs):
        config = RetryConfig(**config_kwargs)
        return RetryHandler(config=config, throttle_aware=False)

    @patch("selfhealing.services.retry_handler._is_system_enabled", return_value=True)
    def test_success_first_attempt(self, mock_enabled):
        """Success first attempt
        첫 시도에서 성공하면 즉시 SUCCESS를 반환하는지 확인.
        """
        handler = self._make_handler(max_attempts=3)
        result = handler.execute(lambda: "ok")
        assert result.success is True
        assert result.attempt == 1
        assert result.value == "ok"

    @patch("selfhealing.services.retry_handler._is_system_enabled", return_value=False)
    def test_kill_switch_blocks(self, mock_enabled):
        """Kill switch blocks
        Kill Switch가 활성화되면 execute가 즉시 ABORT를 반환하는지 확인.
        """
        handler = self._make_handler(max_attempts=3)
        result = handler.execute(lambda: "ok")
        assert result.success is False
        assert result.action == RetryAction.ABORT
        assert "Kill Switch" in str(result.error)

    @patch("selfhealing.services.retry_handler._is_system_enabled", return_value=True)
    def test_max_retries_then_dlq(self, mock_enabled):
        """Max retries then DLQ
        최대 재시도 초과 후 DLQ로 이동하는지 확인.
        AdaptiveRetryBudget을 우회하기 위해 예산을 충분히 설정.
        """
        handler = self._make_handler(max_attempts=2, enable_dlq=True, domain="test")
        # AdaptiveRetryBudget 우회: 예산 무제한
        handler._retry_budget.should_allow_retry = lambda: True
        call_count = 0

        def failing_fn():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("timeout")

        with patch.object(handler, "_move_to_dlq", return_value=99) as mock_dlq:
            result = handler.execute(failing_fn)

        assert result.success is False
        assert result.action == RetryAction.DLQ
        assert result.dlq_id == 99
        assert call_count == 2

    @patch("selfhealing.services.retry_handler._is_system_enabled", return_value=True)
    def test_non_retryable_exception_aborts(self, mock_enabled):
        """Non-retryable exception aborts immediately
        non_retryable 예외 발생 시 즉시 중단되는지 확인.
        """
        handler = self._make_handler(
            max_attempts=5,
            non_retryable_exceptions=(ValueError,),
            enable_dlq=False,
        )

        def failing_fn():
            raise ValueError("bad data")

        result = handler.execute(failing_fn)
        assert result.success is False
        assert result.attempt == 1  # 즉시 중단
        assert result.action == RetryAction.ABORT

    @patch("selfhealing.services.retry_handler._is_system_enabled", return_value=True)
    def test_error_budget_gate_blocks(self, mock_enabled):
        """Error budget gate blocks
        ErrorBudgetGate가 차단하면 execute가 ABORT를 반환하는지 확인.
        """
        handler = self._make_handler(max_attempts=3)
        gate_result = MagicMock()
        gate_result.allowed = False
        gate_result.error_budget_percent = 5.0
        gate_result.threshold_percent = 10.0

        with patch.object(handler, "_check_error_budget_gate", return_value=gate_result):
            result = handler.execute(lambda: "ok")

        assert result.success is False
        assert result.action == RetryAction.ABORT
        assert "budget" in str(result.error).lower()

    @patch("selfhealing.services.retry_handler._is_system_enabled", return_value=True)
    def test_success_after_retry(self, mock_enabled):
        """Success after retry
        첫 시도 실패 후 두 번째 시도에서 성공하는지 확인.
        AdaptiveRetryBudget을 우회하기 위해 예산을 충분히 설정.
        """
        handler = self._make_handler(max_attempts=3)
        # AdaptiveRetryBudget 우회: 예산 무제한
        handler._retry_budget.should_allow_retry = lambda: True
        attempts = [0]

        def flaky_fn():
            attempts[0] += 1
            if attempts[0] == 1:
                raise ConnectionError("temporary failure")
            return "eventually ok"

        result = handler.execute(flaky_fn)
        assert result.success is True
        assert result.attempt == 2
        assert result.value == "eventually ok"


# =============================================================================
# RetryHandler.get_next_delay Tests
# =============================================================================


class TestRetryHandlerDelay:
    """RetryHandler delay 계산 테스트."""

    def test_non_throttle_delay(self):
        """Non-throttle delay
        throttle_aware=False일 때 기본 backoff가 사용되는지 확인.
        """
        config = RetryConfig(backoff_base=2, backoff_max=60, jitter_percent=0)
        handler = RetryHandler(config=config, throttle_aware=False)
        delay = handler.get_next_delay(attempt=1)
        assert isinstance(delay, (int, float))
        assert delay >= 0


# =============================================================================
# with_retry Decorator Tests
# =============================================================================


class TestWithRetryDecorator:
    """with_retry 데코레이터 테스트."""

    @patch("selfhealing.services.retry_handler._is_system_enabled", return_value=True)
    def test_decorator_success(self, mock_enabled):
        """Decorator success
        데코레이터가 성공 시 원래 값을 반환하는지 확인.
        """

        @with_retry(domain="test", max_attempts=1)
        def my_func():
            return "result"

        result = my_func()
        assert result == "result"

    @patch("selfhealing.services.retry_handler._is_system_enabled", return_value=True)
    def test_decorator_raises_on_failure(self, mock_enabled):
        """Decorator raises on failure
        데코레이터가 최대 재시도 초과 시 MaxRetriesExceededError를 발생시키는지 확인.
        """

        @with_retry(domain="test", max_attempts=1)
        def my_func():
            raise ConnectionError("fail")

        with pytest.raises(MaxRetriesExceededError):
            my_func()
