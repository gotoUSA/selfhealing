"""
InMemoryRateLimiter 및 Fail-Open Rate Limiting 테스트.

Rate Limiter 동작 및 Fail-Open 시 Rate Limiting 테스트.
"""

from unittest.mock import patch


class TestInMemoryRateLimiter:
    """InMemoryRateLimiter 테스트."""

    def test_rate_limiter_allows_within_limit(self):
        """Rate limit 내 요청 허용."""
        from selfhealing.services.error_budget_gate import InMemoryRateLimiter

        limiter = InMemoryRateLimiter(max_requests=5, window_seconds=60)

        # 5회까지 허용
        for i in range(5):
            allowed, remaining, _ = limiter.try_acquire()
            assert allowed is True
            assert remaining == 5 - i - 1

    def test_rate_limiter_blocks_over_limit(self):
        """Rate limit 초과 시 차단."""
        from selfhealing.services.error_budget_gate import InMemoryRateLimiter

        limiter = InMemoryRateLimiter(max_requests=3, window_seconds=60)

        # 3회 소진
        for _ in range(3):
            allowed, _, _ = limiter.try_acquire()
            assert allowed is True

        # 4번째 요청 차단
        allowed, remaining, reset_at = limiter.try_acquire()
        assert allowed is False
        assert remaining == 0
        assert reset_at is not None

    def test_rate_limiter_reset(self):
        """Rate limiter 리셋."""
        from selfhealing.services.error_budget_gate import InMemoryRateLimiter

        limiter = InMemoryRateLimiter(max_requests=2, window_seconds=60)

        # 소진
        limiter.try_acquire()
        limiter.try_acquire()
        allowed, _, _ = limiter.try_acquire()
        assert allowed is False

        # 리셋
        limiter.reset()

        # 다시 허용
        allowed, _, _ = limiter.try_acquire()
        assert allowed is True

    def test_rate_limiter_update_limits(self):
        """Rate limit 설정 동적 업데이트."""
        from selfhealing.services.error_budget_gate import InMemoryRateLimiter

        limiter = InMemoryRateLimiter(max_requests=2, window_seconds=60)

        # 2회 소진
        limiter.try_acquire()
        limiter.try_acquire()

        # 차단 확인
        allowed, _, _ = limiter.try_acquire()
        assert allowed is False

        # limit 증가
        limiter.update_limits(max_requests=5, window_seconds=60)

        # 다시 허용 (5 - 2 = 3회 남음)
        allowed, remaining, _ = limiter.try_acquire()
        assert allowed is True
        assert remaining == 2  # 5 - 3 = 2

    def test_rate_limiter_get_status(self):
        """Rate limiter 상태 조회."""
        from selfhealing.services.error_budget_gate import InMemoryRateLimiter

        limiter = InMemoryRateLimiter(max_requests=10, window_seconds=60)

        limiter.try_acquire()
        limiter.try_acquire()

        status = limiter.get_status()

        assert status["current_count"] == 2
        assert status["max_requests"] == 10
        assert status["window_seconds"] == 60
        assert status["remaining"] == 8


class TestFailOpenRateLimiting:
    """Fail-Open Rate Limiting 테스트."""

    def test_fail_open_rate_limit_allows_within_limit(self):
        """Rate limit 내 Fail-Open 허용."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
            GateStatus,
        )

        config = ErrorBudgetGateConfig(
            enabled=True,
            fail_open=True,
            fail_open_rate_limit_enabled=True,
            fail_open_rate_limit_per_minute=5,
            fail_open_rate_limit_window_seconds=60,
        )
        gate = ErrorBudgetGate(config=config)

        # Mock error budget retrieval failure
        with patch.object(gate, '_get_error_budget_percent', return_value=None):
            # 첫 번째 요청 - 허용
            result = gate.check(force_refresh=True)

            assert result.allowed is True
            assert result.status == GateStatus.FAIL_OPEN
            assert result.fail_open_triggered is True
            assert result.rate_limit_remaining == 4

    def test_fail_open_rate_limit_blocks_over_limit(self):
        """Rate limit 초과 시 Fail-Open에서도 차단."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
            GateStatus,
        )

        config = ErrorBudgetGateConfig(
            enabled=True,
            fail_open=True,
            fail_open_rate_limit_enabled=True,
            fail_open_rate_limit_per_minute=3,
            fail_open_rate_limit_window_seconds=60,
        )
        gate = ErrorBudgetGate(config=config)

        with patch.object(gate, '_get_error_budget_percent', return_value=None):
            # 3회 허용
            for _ in range(3):
                result = gate.check(force_refresh=True)
                assert result.allowed is True

            # 4번째 요청 - Rate Limit 초과로 차단
            result = gate.check(force_refresh=True)

            assert result.allowed is False
            assert result.status == GateStatus.FAIL_OPEN_RATE_LIMITED
            assert result.fail_open_triggered is True
            assert result.rate_limit_remaining == 0
            assert result.rate_limit_reset_at is not None

    def test_fail_open_rate_limit_disabled(self):
        """Rate limit 비활성화 시 무제한 허용."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
            GateStatus,
        )

        config = ErrorBudgetGateConfig(
            enabled=True,
            fail_open=True,
            fail_open_rate_limit_enabled=False,  # 비활성화
            fail_open_rate_limit_per_minute=3,
        )
        gate = ErrorBudgetGate(config=config)

        with patch.object(gate, '_get_error_budget_percent', return_value=None):
            # 10회 모두 허용
            for _ in range(10):
                result = gate.check(force_refresh=True)
                assert result.allowed is True
                assert result.status == GateStatus.FAIL_OPEN

    def test_rate_limit_config_update_via_api(self):
        """API를 통한 Rate Limit 설정 동적 변경."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
        )

        config = ErrorBudgetGateConfig(
            enabled=True,
            fail_open=True,
            fail_open_rate_limit_enabled=True,
            fail_open_rate_limit_per_minute=2,
            fail_open_rate_limit_window_seconds=60,
        )
        gate = ErrorBudgetGate(config=config)

        with patch.object(gate, '_get_error_budget_percent', return_value=None):
            # 2회 소진
            gate.check(force_refresh=True)
            gate.check(force_refresh=True)

            # 차단 확인
            result = gate.check(force_refresh=True)
            assert result.allowed is False

            # API로 limit 증가
            gate.update_config(fail_open_rate_limit_per_minute=10)

            # 다시 허용
            result = gate.check(force_refresh=True)
            assert result.allowed is True

    def test_get_rate_limiter_status(self):
        """Rate limiter 상태 조회."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
        )

        config = ErrorBudgetGateConfig(
            enabled=True,
            fail_open=True,
            fail_open_rate_limit_enabled=True,
            fail_open_rate_limit_per_minute=10,
        )
        gate = ErrorBudgetGate(config=config)

        status = gate.get_rate_limiter_status()

        assert status["enabled"] is True
        assert status["max_requests"] == 10
        assert status["remaining"] == 10

    def test_reset_rate_limiter(self):
        """Rate limiter 리셋."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
        )

        config = ErrorBudgetGateConfig(
            enabled=True,
            fail_open=True,
            fail_open_rate_limit_enabled=True,
            fail_open_rate_limit_per_minute=2,
        )
        gate = ErrorBudgetGate(config=config)

        with patch.object(gate, '_get_error_budget_percent', return_value=None):
            # 소진
            gate.check(force_refresh=True)
            gate.check(force_refresh=True)

            # 차단 확인
            result = gate.check(force_refresh=True)
            assert result.allowed is False

            # 리셋
            gate.reset_rate_limiter()

            # 다시 허용
            result = gate.check(force_refresh=True)
            assert result.allowed is True

    def test_config_to_dict_includes_rate_limit_fields(self):
        """Config dict에 rate limit 필드 포함."""
        from selfhealing.services.error_budget_gate import ErrorBudgetGateConfig

        config = ErrorBudgetGateConfig(
            fail_open_rate_limit_enabled=True,
            fail_open_rate_limit_per_minute=15,
            fail_open_rate_limit_window_seconds=120,
        )

        config_dict = config.to_dict()

        assert "fail_open_rate_limit_enabled" in config_dict
        assert config_dict["fail_open_rate_limit_per_minute"] == 15
        assert config_dict["fail_open_rate_limit_window_seconds"] == 120

    def test_config_from_dict_includes_rate_limit_fields(self):
        """Config from_dict로 rate limit 필드 로드."""
        from selfhealing.services.error_budget_gate import ErrorBudgetGateConfig

        data = {
            "enabled": True,
            "fail_open": True,
            "fail_open_rate_limit_enabled": True,
            "fail_open_rate_limit_per_minute": 20,
            "fail_open_rate_limit_window_seconds": 30,
        }

        config = ErrorBudgetGateConfig.from_dict(data)

        assert config.fail_open_rate_limit_enabled is True
        assert config.fail_open_rate_limit_per_minute == 20
        assert config.fail_open_rate_limit_window_seconds == 30
