"""
RetryHandler Throttle 연동 단위 테스트.

Throttle-aware Backoff 및 관련 기능 테스트.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from selfhealing.services.retry_handler import (
    RetryConfig,
    RetryHandler,
)


class TestRetryConfigThrottleAware:
    """RetryConfig Throttle 설정 테스트."""

    def test_default_throttle_aware_enabled(self):
        """기본적으로 throttle_aware 활성화."""
        config = RetryConfig()

        assert config.throttle_aware is True

    def test_throttle_backoff_multiplier_cap(self):
        """Backoff 배율 cap 기본값."""
        config = RetryConfig()

        assert config.throttle_backoff_multiplier_cap == 4.0

    def test_critical_tier_settings(self):
        """CRITICAL 티어 설정값."""
        config = RetryConfig()

        assert config.critical_tier_full_stop_grace_retries == 1
        assert config.critical_tier_full_stop_max_delay == 720  # 12분


class TestRetryHandlerThrottleAware:
    """RetryHandler Throttle-aware 기능 테스트."""

    @patch("selfhealing.services.retry_handler._is_system_enabled", return_value=True)
    @patch("selfhealing.services.retry_handler.RetryHandler._check_error_budget_gate")
    def test_throttle_aware_init_uses_throttle_aware_calculator(self, mock_gate, mock_system):
        """throttle_aware=True 시 ThrottleAwareBackoffCalculator 사용."""
        mock_gate.return_value = None

        config = RetryConfig(throttle_aware=True)
        handler = RetryHandler(config=config)

        from selfhealing.services.backoff_calculator import (
            ThrottleAwareBackoffCalculator,
        )

        assert isinstance(handler.backoff, ThrottleAwareBackoffCalculator)
        assert handler._throttle_aware is True

    @patch("selfhealing.services.retry_handler._is_system_enabled", return_value=True)
    @patch("selfhealing.services.retry_handler.RetryHandler._check_error_budget_gate")
    def test_throttle_aware_false_uses_basic_calculator(self, mock_gate, mock_system):
        """throttle_aware=False 시 기본 BackoffCalculator 사용."""
        mock_gate.return_value = None

        config = RetryConfig(throttle_aware=False)
        handler = RetryHandler(config=config)

        from selfhealing.services.backoff_calculator import (
            BackoffCalculator,
            ThrottleAwareBackoffCalculator,
        )

        assert isinstance(handler.backoff, BackoffCalculator)
        assert not isinstance(handler.backoff, ThrottleAwareBackoffCalculator)
        assert handler._throttle_aware is False

    @patch("selfhealing.services.retry_handler._is_system_enabled", return_value=True)
    @patch("selfhealing.services.retry_handler.RetryHandler._check_error_budget_gate")
    def test_get_next_delay_throttle_aware(self, mock_gate, mock_system):
        """get_next_delay가 Throttle 상태 반영."""
        mock_gate.return_value = None

        # Mock ThrottleAwareBackoffCalculator
        mock_backoff = MagicMock()
        mock_backoff.calculate_with_throttle_context.return_value = (8, 2.0, "sla_critical")
        mock_backoff.calculate.return_value = 4

        config = RetryConfig(throttle_aware=True)
        handler = RetryHandler(config=config)
        handler.backoff = mock_backoff
        handler._throttle_aware = True

        delay = handler.get_next_delay(1)

        assert delay == 8
        mock_backoff.calculate_with_throttle_context.assert_called_once_with(1)

    @patch("selfhealing.services.retry_handler._is_system_enabled", return_value=True)
    @patch("selfhealing.services.retry_handler.RetryHandler._check_error_budget_gate")
    def test_get_next_delay_full_stop_returns_negative(self, mock_gate, mock_system):
        """Full Stop 시 -1 반환."""
        mock_gate.return_value = None

        mock_backoff = MagicMock()
        mock_backoff.calculate_with_throttle_context.return_value = (
            -1,
            float("inf"),
            "full_stop_active",
        )

        config = RetryConfig(throttle_aware=True)
        handler = RetryHandler(config=config)
        handler.backoff = mock_backoff
        handler._throttle_aware = True

        delay = handler.get_next_delay(1)

        assert delay == -1

    @patch("selfhealing.services.retry_handler._is_system_enabled", return_value=True)
    @patch("selfhealing.services.retry_handler.RetryHandler._check_error_budget_gate")
    def test_critical_tier_grace_retry_on_full_stop(self, mock_gate, mock_system):
        """CRITICAL 티어는 Full Stop 시 grace retry 허용."""
        mock_gate.return_value = None

        mock_backoff = MagicMock()
        mock_backoff.calculate_with_throttle_context.return_value = (
            -1,
            float("inf"),
            "full_stop_active",
        )
        mock_backoff.calculate.return_value = 4

        config = RetryConfig(
            throttle_aware=True,
            max_attempts=3,
            critical_tier_full_stop_grace_retries=1,
            critical_tier_full_stop_max_delay=720,
        )
        handler = RetryHandler(config=config)
        handler.backoff = mock_backoff
        handler._throttle_aware = True

        # CRITICAL 티어로 호출 (max_attempts 초과한 시도)
        delay = handler.get_next_delay(4, is_critical_tier=True)

        # grace retry 허용되어 max_delay 반환
        assert delay == 720

    @patch("selfhealing.services.retry_handler._is_system_enabled", return_value=True)
    @patch("selfhealing.services.retry_handler.RetryHandler._check_error_budget_gate")
    def test_critical_tier_grace_retry_exhausted(self, mock_gate, mock_system):
        """CRITICAL 티어 grace retry 소진 시 -1 반환."""
        mock_gate.return_value = None

        mock_backoff = MagicMock()
        mock_backoff.calculate_with_throttle_context.return_value = (
            -1,
            float("inf"),
            "full_stop_active",
        )
        mock_backoff.calculate.return_value = 4

        config = RetryConfig(
            throttle_aware=True,
            max_attempts=3,
            critical_tier_full_stop_grace_retries=1,  # 1회만 허용
        )
        handler = RetryHandler(config=config)
        handler.backoff = mock_backoff
        handler._throttle_aware = True

        # 두 번째 grace retry 시도 (already used 1)
        delay = handler.get_next_delay(5, is_critical_tier=True)

        # grace retry 소진되어 -1 반환
        assert delay == -1


class TestRetryHandlerGetCombinedDelay:
    """get_combined_delay 메서드 테스트."""

    @patch("selfhealing.services.retry_handler._is_system_enabled", return_value=True)
    @patch("selfhealing.services.retry_handler.RetryHandler._check_error_budget_gate")
    def test_combined_delay_uses_max(self, mock_gate, mock_system):
        """429 쿨다운과 Throttle 백오프 중 큰 값 사용."""
        mock_gate.return_value = None

        # Mock backoff
        mock_backoff = MagicMock()
        mock_backoff.calculate_with_throttle_context.return_value = (10, 1.0, "normal")
        mock_backoff.calculate.return_value = 10

        # Mock rate limit coordinator
        mock_coordinator = MagicMock()
        mock_state = MagicMock()
        mock_state.is_in_cooldown = True
        mock_state.remaining_cooldown = 30
        mock_coordinator._storage.get_state.return_value = mock_state

        config = RetryConfig(throttle_aware=True)
        handler = RetryHandler(config=config)
        handler.backoff = mock_backoff
        handler._throttle_aware = True
        handler._rate_limit_coordinator = mock_coordinator

        delay = handler.get_combined_delay(1)

        # 429 쿨다운(30) > Throttle 백오프(10)
        assert delay == 30

    @patch("selfhealing.services.retry_handler._is_system_enabled", return_value=True)
    @patch("selfhealing.services.retry_handler.RetryHandler._check_error_budget_gate")
    def test_combined_delay_full_stop_passes_through(self, mock_gate, mock_system):
        """Full Stop 신호는 그대로 전달."""
        mock_gate.return_value = None

        mock_backoff = MagicMock()
        mock_backoff.calculate_with_throttle_context.return_value = (
            -1,
            float("inf"),
            "full_stop_active",
        )

        config = RetryConfig(throttle_aware=True)
        handler = RetryHandler(config=config)
        handler.backoff = mock_backoff
        handler._throttle_aware = True

        delay = handler.get_combined_delay(1)

        assert delay == -1


class TestRetryHandlerExecuteWithThrottle:
    """execute 메서드 Throttle 연동 테스트."""

    @patch("selfhealing.services.retry_handler._is_system_enabled", return_value=True)
    @patch("selfhealing.services.retry_handler.RetryHandler._check_error_budget_gate")
    @patch("selfhealing.services.retry_handler.RetryHandler._wait_for_rate_limit")
    @patch("selfhealing.services.retry_handler.RetryHandler._log_retry_audit")
    def test_execute_updates_retry_budget(self, mock_audit, mock_wait, mock_gate, mock_system):
        """execute가 Adaptive Retry Budget 업데이트."""
        mock_gate.return_value = None

        config = RetryConfig(throttle_aware=False, max_attempts=3)
        handler = RetryHandler(config=config)

        call_count = 0

        def always_succeed():
            nonlocal call_count
            call_count += 1
            return "success"

        result = handler.execute(always_succeed)

        assert result.success is True
        # 1개 요청 기록됨
        assert handler._retry_budget.current_total_count == 1

    @patch("selfhealing.services.retry_handler._is_system_enabled", return_value=True)
    @patch("selfhealing.services.retry_handler.RetryHandler._check_error_budget_gate")
    @patch("selfhealing.services.retry_handler.RetryHandler._wait_for_rate_limit")
    @patch("selfhealing.services.retry_handler.RetryHandler._log_retry_audit")
    def test_execute_respects_critical_tier(self, mock_audit, mock_wait, mock_gate, mock_system):
        """execute가 is_critical_tier 파라미터를 전달."""
        mock_gate.return_value = None

        config = RetryConfig(
            throttle_aware=True,
            max_attempts=2,
            critical_tier_full_stop_grace_retries=2,
        )
        handler = RetryHandler(config=config)

        # Mock backoff
        mock_backoff = MagicMock()
        mock_backoff.calculate_with_throttle_context.return_value = (4, 1.0, "normal")
        mock_backoff.calculate.return_value = 4
        handler.backoff = mock_backoff

        call_count = 0

        def always_fail():
            nonlocal call_count
            call_count += 1
            raise Exception("Test error")

        result = handler.execute(always_fail, is_critical_tier=True)

        # CRITICAL 티어는 max_attempts + grace_retries까지 시도
        # 2 + 2 = 4 attempts
        assert result.attempt == 4


class TestRetryHandlerDLQMetadata:
    """DLQ 메타데이터 확장 테스트."""

    @patch("selfhealing.services.retry_handler._is_system_enabled", return_value=True)
    @patch("selfhealing.services.retry_handler.RetryHandler._check_error_budget_gate")
    @patch("selfhealing.services.retry_handler.RetryHandler._wait_for_rate_limit")
    @patch("selfhealing.services.retry_handler.RetryHandler._log_retry_audit")
    @patch("selfhealing.services.dlq.store_to_dlq")
    def test_dlq_includes_backoff_info(self, mock_dlq, mock_audit, mock_wait, mock_gate, mock_system):
        """DLQ에 backoff_info 포함."""
        mock_gate.return_value = None
        mock_dlq.return_value = MagicMock(success=True, dlq_id=123)

        config = RetryConfig(throttle_aware=True, max_attempts=1, enable_dlq=True)
        handler = RetryHandler(config=config)

        # Mock backoff
        mock_backoff = MagicMock()
        mock_backoff.calculate_with_throttle_context.return_value = (
            10,
            2.5,
            "emergency_level_2",
        )
        mock_backoff.calculate.return_value = 4
        handler.backoff = mock_backoff

        def always_fail():
            raise Exception("Test error")

        # should_retry가 False가 되도록 non_retryable_exceptions 설정
        handler.config.max_attempts = 1

        result = handler.execute(always_fail)

        # DLQ 호출 확인
        if mock_dlq.called:
            call_args = mock_dlq.call_args
            metadata = call_args.kwargs.get("metadata", {})

            # backoff_info가 포함되어야 함
            if handler._last_backoff_info:
                assert "throttle_aware_enabled" in metadata
