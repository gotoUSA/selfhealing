"""
Error Budget Gate Tests

Tests for the error budget based automation control gate.

Tests cover:
1. Basic gate functionality (open, warning, blocked states)
2. Fail-open behavior when error budget retrieval fails
3. Configuration updates
4. Integration with automation functions
5. Gate disabled behavior
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock

# =============================================================================
# Test: ErrorBudgetGateConfig
# =============================================================================


class TestErrorBudgetGateConfig:
    """ErrorBudgetGateConfig 테스트."""
    
    def test_default_config(self):
        """기본 설정 테스트."""
        from selfhealing.services.error_budget_gate import ErrorBudgetGateConfig
        
        config = ErrorBudgetGateConfig()
        
        assert config.enabled is True
        assert config.critical_threshold_percent == 10.0
        assert config.warning_threshold_percent == 20.0
        assert config.fail_open is True
        assert config.cache_ttl_seconds == 30
    
    def test_custom_config(self):
        """커스텀 설정 테스트."""
        from selfhealing.services.error_budget_gate import ErrorBudgetGateConfig
        
        config = ErrorBudgetGateConfig(
            enabled=False,
            critical_threshold_percent=15.0,
            warning_threshold_percent=30.0,
            fail_open=False,
            cache_ttl_seconds=60,
        )
        
        assert config.enabled is False
        assert config.critical_threshold_percent == 15.0
        assert config.warning_threshold_percent == 30.0
        assert config.fail_open is False
        assert config.cache_ttl_seconds == 60
    
    def test_config_to_dict(self):
        """설정 딕셔너리 변환 테스트."""
        from selfhealing.services.error_budget_gate import ErrorBudgetGateConfig
        
        config = ErrorBudgetGateConfig()
        config_dict = config.to_dict()
        
        assert "enabled" in config_dict
        assert "critical_threshold_percent" in config_dict
        assert "warning_threshold_percent" in config_dict
        assert "fail_open" in config_dict
        assert "cache_ttl_seconds" in config_dict
    
    def test_config_from_dict(self):
        """딕셔너리에서 설정 생성 테스트."""
        from selfhealing.services.error_budget_gate import ErrorBudgetGateConfig
        
        data = {
            "enabled": False,
            "critical_threshold_percent": 5.0,
            "warning_threshold_percent": 15.0,
            "fail_open": False,
            "cache_ttl_seconds": 120,
        }
        
        config = ErrorBudgetGateConfig.from_dict(data)
        
        assert config.enabled is False
        assert config.critical_threshold_percent == 5.0
        assert config.warning_threshold_percent == 15.0
        assert config.fail_open is False
        assert config.cache_ttl_seconds == 120


# =============================================================================
# Test: GateCheckResult
# =============================================================================


class TestGateCheckResult:
    """GateCheckResult 테스트."""
    
    def test_result_to_dict(self):
        """결과 딕셔너리 변환 테스트."""
        from selfhealing.services.error_budget_gate import (
            GateCheckResult,
            GateStatus,
        )
        
        result = GateCheckResult(
            allowed=True,
            status=GateStatus.OPEN,
            error_budget_percent=75.0,
            threshold_percent=10.0,
            reason="Error budget healthy",
            recommendation="자동화 정상 동작 중",
        )
        
        result_dict = result.to_dict()
        
        assert result_dict["allowed"] is True
        assert result_dict["status"] == "open"
        assert result_dict["error_budget_percent"] == 75.0
        assert "checked_at" in result_dict


# =============================================================================
# Test: ErrorBudgetGate Core Functionality
# =============================================================================


class TestErrorBudgetGateCore:
    """ErrorBudgetGate 핵심 기능 테스트."""
    
    def test_gate_allows_when_budget_healthy(self):
        """에러 예산이 충분할 때 자동화 허용."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
            GateStatus,
        )
        
        config = ErrorBudgetGateConfig(
            enabled=True,
            critical_threshold_percent=10.0,
            warning_threshold_percent=20.0,
        )
        gate = ErrorBudgetGate(config=config)
        
        # Mock error budget service
        with patch.object(gate, '_get_error_budget_percent', return_value=75.0):
            result = gate.check(force_refresh=True)
        
        assert result.allowed is True
        assert result.status == GateStatus.OPEN
        assert result.error_budget_percent == 75.0
    
    def test_gate_warns_when_budget_low(self):
        """에러 예산이 낮을 때 경고."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
            GateStatus,
        )
        
        config = ErrorBudgetGateConfig(
            enabled=True,
            critical_threshold_percent=10.0,
            warning_threshold_percent=20.0,
        )
        gate = ErrorBudgetGate(config=config)
        
        # Mock error budget at 15% (below warning, above critical)
        with patch.object(gate, '_get_error_budget_percent', return_value=15.0):
            result = gate.check(force_refresh=True)
        
        assert result.allowed is True  # Still allowed, but with warning
        assert result.status == GateStatus.WARNING
        assert result.error_budget_percent == 15.0
    
    def test_gate_blocks_when_budget_critical(self):
        """에러 예산이 위험할 때 자동화 차단."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
            GateStatus,
        )
        
        config = ErrorBudgetGateConfig(
            enabled=True,
            critical_threshold_percent=10.0,
            warning_threshold_percent=20.0,
        )
        gate = ErrorBudgetGate(config=config)
        
        # Mock error budget at 5% (below critical)
        with patch.object(gate, '_get_error_budget_percent', return_value=5.0):
            result = gate.check(force_refresh=True)
        
        assert result.allowed is False
        assert result.status == GateStatus.BLOCKED
        assert result.error_budget_percent == 5.0
        assert "critically low" in result.reason.lower()
    
    def test_gate_disabled(self):
        """게이트 비활성화 시 항상 허용."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
            GateStatus,
        )
        
        config = ErrorBudgetGateConfig(enabled=False)
        gate = ErrorBudgetGate(config=config)
        
        result = gate.check()
        
        assert result.allowed is True
        assert result.status == GateStatus.DISABLED


# =============================================================================
# Test: Fail-Open Behavior
# =============================================================================


class TestFailOpenBehavior:
    """Fail-open 동작 테스트."""
    
    def test_fail_open_when_budget_retrieval_fails(self):
        """에러 예산 조회 실패 시 Fail-open."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
            GateStatus,
        )
        
        config = ErrorBudgetGateConfig(
            enabled=True,
            fail_open=True,
        )
        gate = ErrorBudgetGate(config=config)
        
        # Mock error budget retrieval failure
        with patch.object(gate, '_get_error_budget_percent', return_value=None):
            result = gate.check(force_refresh=True)
        
        assert result.allowed is True
        assert result.status == GateStatus.FAIL_OPEN
        assert result.fail_open_triggered is True
        assert result.error_budget_percent is None
    
    def test_fail_close_when_configured(self):
        """Fail-close 설정 시 조회 실패하면 차단."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
            GateStatus,
        )
        
        config = ErrorBudgetGateConfig(
            enabled=True,
            fail_open=False,  # Fail-close
        )
        gate = ErrorBudgetGate(config=config)
        
        # Mock error budget retrieval failure
        with patch.object(gate, '_get_error_budget_percent', return_value=None):
            result = gate.check(force_refresh=True)
        
        assert result.allowed is False
        assert result.status == GateStatus.BLOCKED
        assert result.fail_open_triggered is True


# =============================================================================
# Test: AutomationBlockedError
# =============================================================================


class TestAutomationBlockedError:
    """AutomationBlockedError 테스트."""
    
    def test_require_raises_when_blocked(self):
        """차단 시 예외 발생."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
            AutomationBlockedError,
        )
        
        config = ErrorBudgetGateConfig(
            enabled=True,
            critical_threshold_percent=10.0,
        )
        gate = ErrorBudgetGate(config=config)
        
        # Mock critical error budget
        with patch.object(gate, '_get_error_budget_percent', return_value=5.0):
            with pytest.raises(AutomationBlockedError) as exc_info:
                gate.require(action="test_action")
        
        error = exc_info.value
        assert error.error_budget_percent == 5.0
        assert error.threshold_percent == 10.0
        assert error.action == "test_action"
    
    def test_require_returns_result_when_allowed(self):
        """허용 시 결과 반환."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
            GateStatus,
        )
        
        config = ErrorBudgetGateConfig(
            enabled=True,
            critical_threshold_percent=10.0,
        )
        gate = ErrorBudgetGate(config=config)
        
        # Mock healthy error budget
        with patch.object(gate, '_get_error_budget_percent', return_value=75.0):
            result = gate.require(action="test_action")
        
        assert result.allowed is True
        assert result.status == GateStatus.OPEN
    
    def test_error_to_dict(self):
        """에러 딕셔너리 변환."""
        from selfhealing.services.error_budget_gate import AutomationBlockedError
        
        error = AutomationBlockedError(
            message="Error budget critically low",
            error_budget_percent=5.0,
            threshold_percent=10.0,
            action="chaos_experiment",
        )
        
        error_dict = error.to_dict()
        
        assert error_dict["error"] == "AutomationBlockedError"
        assert error_dict["error_budget_percent"] == 5.0
        assert error_dict["threshold_percent"] == 10.0
        assert error_dict["action"] == "chaos_experiment"
        assert error_dict["manual_mode_enforced"] is True


# =============================================================================
# Test: Convenience Functions
# =============================================================================


class TestConvenienceFunctions:
    """편의 함수 테스트."""
    
    def test_check_automation_allowed(self):
        """check_automation_allowed 함수 테스트."""
        from selfhealing.services.error_budget_gate import (
            check_automation_allowed,
            get_error_budget_gate,
        )
        
        gate = get_error_budget_gate()
        
        with patch.object(gate, '_get_error_budget_percent', return_value=75.0):
            result = check_automation_allowed(force_refresh=True)
        
        assert result.allowed is True
    
    def test_is_automation_allowed(self):
        """is_automation_allowed 함수 테스트."""
        from selfhealing.services.error_budget_gate import (
            is_automation_allowed,
            get_error_budget_gate,
        )
        
        gate = get_error_budget_gate()
        
        with patch.object(gate, '_get_error_budget_percent', return_value=75.0):
            gate.clear_cache()
            allowed = is_automation_allowed()
        
        assert allowed is True


# =============================================================================
# Test: Decorator
# =============================================================================


class TestAutomationGateDecorator:
    """automation_gate 데코레이터 테스트."""
    
    def test_decorator_allows_when_budget_healthy(self):
        """예산 충분 시 함수 실행."""
        from selfhealing.services.error_budget_gate import (
            automation_gate,
            get_error_budget_gate,
        )
        
        @automation_gate(action="test_func")
        def test_func():
            return "executed"
        
        gate = get_error_budget_gate()
        
        with patch.object(gate, '_get_error_budget_percent', return_value=75.0):
            gate.clear_cache()
            result = test_func()
        
        assert result == "executed"
    
    def test_decorator_blocks_when_budget_critical(self):
        """예산 위험 시 함수 차단."""
        from selfhealing.services.error_budget_gate import (
            automation_gate,
            get_error_budget_gate,
            AutomationBlockedError,
        )
        
        @automation_gate(action="test_func")
        def test_func():
            return "executed"
        
        gate = get_error_budget_gate()
        
        with patch.object(gate, '_get_error_budget_percent', return_value=5.0):
            gate.clear_cache()
            with pytest.raises(AutomationBlockedError):
                test_func()


# =============================================================================
# Test: Caching
# =============================================================================


class TestCaching:
    """캐싱 테스트."""
    
    def test_result_is_cached(self):
        """결과가 캐싱되는지 확인."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
        )
        
        config = ErrorBudgetGateConfig(
            enabled=True,
            cache_ttl_seconds=60,
        )
        gate = ErrorBudgetGate(config=config)
        
        call_count = 0
        
        def mock_get_budget():
            nonlocal call_count
            call_count += 1
            return 75.0
        
        with patch.object(gate, '_get_error_budget_percent', side_effect=mock_get_budget):
            # First call
            result1 = gate.check(force_refresh=True)
            # Second call (should use cache)
            result2 = gate.check()
            # Third call (should use cache)
            result3 = gate.check()
        
        # Only called once due to caching
        assert call_count == 1
        assert result1.allowed == result2.allowed == result3.allowed
    
    def test_force_refresh_bypasses_cache(self):
        """force_refresh가 캐시를 무시하는지 확인."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
        )
        
        config = ErrorBudgetGateConfig(
            enabled=True,
            cache_ttl_seconds=60,
        )
        gate = ErrorBudgetGate(config=config)
        
        call_count = 0
        
        def mock_get_budget():
            nonlocal call_count
            call_count += 1
            return 75.0
        
        with patch.object(gate, '_get_error_budget_percent', side_effect=mock_get_budget):
            # First call
            gate.check(force_refresh=True)
            # Second call with force_refresh
            gate.check(force_refresh=True)
        
        # Called twice due to force_refresh
        assert call_count == 2
    
    def test_clear_cache(self):
        """캐시 초기화 테스트."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
        )
        
        config = ErrorBudgetGateConfig(
            enabled=True,
            cache_ttl_seconds=60,
        )
        gate = ErrorBudgetGate(config=config)
        
        call_count = 0
        
        def mock_get_budget():
            nonlocal call_count
            call_count += 1
            return 75.0
        
        with patch.object(gate, '_get_error_budget_percent', side_effect=mock_get_budget):
            gate.check(force_refresh=True)
            gate.clear_cache()
            gate.check()
        
        # Called twice (before and after cache clear)
        assert call_count == 2


# =============================================================================
# Test: Config Update
# =============================================================================


class TestConfigUpdate:
    """설정 업데이트 테스트."""
    
    def test_update_config(self):
        """설정 업데이트 테스트."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
        )
        
        config = ErrorBudgetGateConfig(
            enabled=True,
            critical_threshold_percent=10.0,
        )
        gate = ErrorBudgetGate(config=config)
        
        # Update config
        with patch.object(gate, '_persist_config'):
            updated = gate.update_config(critical_threshold_percent=15.0)
        
        assert updated.critical_threshold_percent == 15.0
        assert gate.get_config().critical_threshold_percent == 15.0
    
    def test_update_invalidates_cache(self):
        """설정 업데이트 시 캐시 무효화."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
        )
        
        config = ErrorBudgetGateConfig(
            enabled=True,
            critical_threshold_percent=10.0,
        )
        gate = ErrorBudgetGate(config=config)
        
        # Populate cache
        with patch.object(gate, '_get_error_budget_percent', return_value=75.0):
            gate.check(force_refresh=True)
        
        # Update config
        with patch.object(gate, '_persist_config'):
            gate.update_config(critical_threshold_percent=15.0)
        
        # Cache should be invalidated
        assert gate._cache is None


# =============================================================================
# Test: Edge Cases
# =============================================================================


class TestEdgeCases:
    """엣지 케이스 테스트."""
    
    def test_exactly_at_threshold(self):
        """정확히 임계값일 때."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
            GateStatus,
        )
        
        config = ErrorBudgetGateConfig(
            enabled=True,
            critical_threshold_percent=10.0,
            warning_threshold_percent=20.0,
        )
        gate = ErrorBudgetGate(config=config)
        
        # Exactly at critical threshold (should block)
        with patch.object(gate, '_get_error_budget_percent', return_value=10.0):
            result = gate.check(force_refresh=True)
        
        # At threshold - should still allow (only below is blocked)
        assert result.allowed is True
        assert result.status == GateStatus.WARNING
    
    def test_negative_budget(self):
        """음수 에러 예산 (over budget)."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
            GateStatus,
        )
        
        config = ErrorBudgetGateConfig(
            enabled=True,
            critical_threshold_percent=10.0,
        )
        gate = ErrorBudgetGate(config=config)
        
        # Negative budget (over budget)
        with patch.object(gate, '_get_error_budget_percent', return_value=-5.0):
            result = gate.check(force_refresh=True)
        
        assert result.allowed is False
        assert result.status == GateStatus.BLOCKED
    
    def test_zero_budget(self):
        """에러 예산 0%."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
            GateStatus,
        )
        
        config = ErrorBudgetGateConfig(
            enabled=True,
            critical_threshold_percent=10.0,
        )
        gate = ErrorBudgetGate(config=config)
        
        with patch.object(gate, '_get_error_budget_percent', return_value=0.0):
            result = gate.check(force_refresh=True)
        
        assert result.allowed is False
        assert result.status == GateStatus.BLOCKED
    
    def test_hundred_percent_budget(self):
        """에러 예산 100%."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
            GateStatus,
        )
        
        config = ErrorBudgetGateConfig(
            enabled=True,
            critical_threshold_percent=10.0,
            warning_threshold_percent=20.0,
        )
        gate = ErrorBudgetGate(config=config)
        
        with patch.object(gate, '_get_error_budget_percent', return_value=100.0):
            result = gate.check(force_refresh=True)
        
        assert result.allowed is True
        assert result.status == GateStatus.OPEN


# =============================================================================
# Test: In-Memory Rate Limiter
# =============================================================================


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


# =============================================================================
# Test: Fail-Open Rate Limiting
# =============================================================================


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
            GateStatus,
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
            GateStatus,
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


# =============================================================================
# Test: Gate Fault Detector (formerly Circuit Breaker)
# =============================================================================


class TestInMemoryCircuitBreaker:
    """GateFaultDetector 테스트 (하위 호환성을 위해 클래스명 유지)."""
    
    def test_circuit_breaker_initial_state(self):
        """초기 상태 HEALTHY."""
        from selfhealing.services.error_budget_gate import GateFaultDetector, GateFaultState
        
        cb = GateFaultDetector(failure_threshold=3, recovery_timeout=30)
        
        status = cb.get_status()
        assert status["state"] == GateFaultState.HEALTHY.value
        assert status["failure_count"] == 0
    
    def test_circuit_breaker_opens_on_threshold(self):
        """실패 임계값 초과 시 DEGRADED 상태 전환."""
        from selfhealing.services.error_budget_gate import GateFaultDetector, GateFaultState
        
        cb = GateFaultDetector(failure_threshold=3, recovery_timeout=30)
        
        # 3회 실패
        cb.record_failure()
        cb.record_failure()
        assert cb.can_execute() is True  # 아직 CLOSED
        
        cb.record_failure()  # 3번째 실패
        assert cb.can_execute() is False  # DEGRADED
        
        status = cb.get_status()
        assert status["state"] == GateFaultState.DEGRADED.value
    
    def test_circuit_breaker_success_resets(self):
        """성공 시 실패 카운트 리셋."""
        from selfhealing.services.error_budget_gate import GateFaultDetector
        
        cb = GateFaultDetector(failure_threshold=3, recovery_timeout=30)
        
        cb.record_failure()
        cb.record_failure()
        
        cb.record_success()  # 성공 시 리셋
        
        status = cb.get_status()
        assert status["failure_count"] == 0
    
    def test_circuit_breaker_reset(self):
        """Gate Fault Detector 리셋."""
        from selfhealing.services.error_budget_gate import GateFaultDetector, GateFaultState
        
        cb = GateFaultDetector(failure_threshold=2, recovery_timeout=30)
        
        cb.record_failure()
        cb.record_failure()
        assert cb.can_execute() is False
        
        cb.reset()
        
        assert cb.can_execute() is True
        assert cb.get_status()["state"] == GateFaultState.HEALTHY.value
    
    def test_circuit_breaker_config_update(self):
        """설정 동적 업데이트."""
        from selfhealing.services.error_budget_gate import GateFaultDetector
        
        cb = GateFaultDetector(failure_threshold=3, recovery_timeout=30)
        
        cb.update_config(failure_threshold=5, recovery_timeout=60)
        
        status = cb.get_status()
        assert status["failure_threshold"] == 5
        assert status["recovery_timeout"] == 60


# =============================================================================
# Test: Alert Manager
# =============================================================================


class TestGateAlertManager:
    """GateAlertManager 테스트."""
    
    def test_alert_manager_cooldown(self):
        """알림 쿨다운 동작."""
        from selfhealing.services.error_budget_gate import GateAlertManager
        
        manager = GateAlertManager(cooldown_seconds=300)
        
        # 첫 번째 알림 - 성공
        result1 = manager._can_send_alert("test_alert")
        assert result1 is True
        
        manager._record_alert_sent("test_alert")
        
        # 두 번째 알림 - 쿨다운 중
        result2 = manager._can_send_alert("test_alert")
        assert result2 is False
    
    def test_alert_manager_different_types(self):
        """다른 알림 타입은 별도 쿨다운."""
        from selfhealing.services.error_budget_gate import GateAlertManager
        
        manager = GateAlertManager(cooldown_seconds=300)
        
        manager._record_alert_sent("type_a")
        
        # type_b는 별도
        result = manager._can_send_alert("type_b")
        assert result is True
    
    def test_alert_manager_reset(self):
        """알림 쿨다운 리셋."""
        from selfhealing.services.error_budget_gate import GateAlertManager
        
        manager = GateAlertManager(cooldown_seconds=300)
        
        manager._record_alert_sent("test_alert")
        assert manager._can_send_alert("test_alert") is False
        
        manager.reset()
        
        assert manager._can_send_alert("test_alert") is True
    
    def test_alert_manager_status(self):
        """Alert Manager 상태 조회."""
        from selfhealing.services.error_budget_gate import GateAlertManager
        
        manager = GateAlertManager(cooldown_seconds=600)
        
        status = manager.get_status()
        
        assert status["cooldown_seconds"] == 600
        assert "last_alerts" in status


# =============================================================================
# Test: Gate with Circuit Breaker Integration
# =============================================================================


class TestGateCircuitBreakerIntegration:
    """Gate와 Circuit Breaker 통합 테스트."""
    
    def test_gate_circuit_breaker_status(self):
        """Gate에서 Circuit Breaker 상태 조회."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
        )
        
        config = ErrorBudgetGateConfig(
            enabled=True,
            circuit_breaker_enabled=True,
            circuit_breaker_failure_threshold=5,
            circuit_breaker_recovery_timeout=30,
        )
        gate = ErrorBudgetGate(config=config)
        
        cb_status = gate.get_circuit_breaker_status()
        
        assert cb_status["enabled"] is True
        assert cb_status["failure_threshold"] == 5
        assert cb_status["state"] == "healthy"  # GateFaultState.HEALTHY
    
    def test_gate_reset_circuit_breaker(self):
        """Gate에서 Circuit Breaker 리셋."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
        )
        
        config = ErrorBudgetGateConfig(
            enabled=True,
            circuit_breaker_enabled=True,
            circuit_breaker_failure_threshold=2,
        )
        gate = ErrorBudgetGate(config=config)
        
        # Fault Detector 트리거 (강제 실패)
        gate._fault_detector.record_failure()
        gate._fault_detector.record_failure()
        
        assert gate.get_circuit_breaker_status()["state"] == "degraded"  # GateFaultState.DEGRADED
        
        # 리셋
        gate.reset_circuit_breaker()
        
        assert gate.get_circuit_breaker_status()["state"] == "healthy"  # GateFaultState.HEALTHY


# =============================================================================
# Test: Gate Health Status
# =============================================================================


class TestGateHealthStatus:
    """Gate 헬스 상태 테스트."""
    
    def test_get_health_status_healthy(self):
        """정상 상태 헬스 체크."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
        )
        
        config = ErrorBudgetGateConfig(enabled=True)
        gate = ErrorBudgetGate(config=config)
        
        # Mock 정상 예산
        with patch.object(gate, '_get_error_budget_percent', return_value=80.0):
            health = gate.get_health_status()
        
        assert health["healthy"] is True
        assert health["status"] == "healthy"
        assert "gate" in health
        assert "circuit_breaker" in health
        assert "rate_limiter" in health
        assert "alerts" in health
    
    def test_get_health_status_degraded(self):
        """Fail-Open 상태에서 degraded 헬스."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
        )
        
        config = ErrorBudgetGateConfig(
            enabled=True,
            fail_open=True,
            alert_on_fail_open=False,  # 테스트에서 알림 비활성화
        )
        gate = ErrorBudgetGate(config=config)
        
        # Mock 예산 조회 실패
        with patch.object(gate, '_get_error_budget_percent', return_value=None):
            health = gate.get_health_status()
        
        assert health["healthy"] is False
        assert health["status"] == "degraded"
        assert health["gate"]["fail_open_triggered"] is True


# =============================================================================
# Test: Config with new fields
# =============================================================================


class TestConfigNewFields:
    """새로 추가된 설정 필드 테스트."""
    
    def test_config_circuit_breaker_fields(self):
        """Circuit Breaker 설정 필드."""
        from selfhealing.services.error_budget_gate import ErrorBudgetGateConfig
        
        config = ErrorBudgetGateConfig(
            circuit_breaker_enabled=True,
            circuit_breaker_failure_threshold=10,
            circuit_breaker_recovery_timeout=60,
        )
        
        assert config.circuit_breaker_enabled is True
        assert config.circuit_breaker_failure_threshold == 10
        assert config.circuit_breaker_recovery_timeout == 60
    
    def test_config_alert_fields(self):
        """알림 설정 필드."""
        from selfhealing.services.error_budget_gate import ErrorBudgetGateConfig
        
        config = ErrorBudgetGateConfig(
            alert_on_fail_open=True,
            alert_cooldown_seconds=600,
        )
        
        assert config.alert_on_fail_open is True
        assert config.alert_cooldown_seconds == 600
    
    def test_config_to_dict_all_fields(self):
        """to_dict에 모든 필드 포함."""
        from selfhealing.services.error_budget_gate import ErrorBudgetGateConfig
        
        config = ErrorBudgetGateConfig()
        config_dict = config.to_dict()
        
        assert "circuit_breaker_enabled" in config_dict
        assert "circuit_breaker_failure_threshold" in config_dict
        assert "circuit_breaker_recovery_timeout" in config_dict
        assert "alert_on_fail_open" in config_dict
        assert "alert_cooldown_seconds" in config_dict
    
    def test_config_from_dict_all_fields(self):
        """from_dict에서 모든 필드 로드."""
        from selfhealing.services.error_budget_gate import ErrorBudgetGateConfig
        
        data = {
            "enabled": True,
            "circuit_breaker_enabled": False,
            "circuit_breaker_failure_threshold": 7,
            "circuit_breaker_recovery_timeout": 45,
            "alert_on_fail_open": False,
            "alert_cooldown_seconds": 120,
        }
        
        config = ErrorBudgetGateConfig.from_dict(data)
        
        assert config.circuit_breaker_enabled is False
        assert config.circuit_breaker_failure_threshold == 7
        assert config.circuit_breaker_recovery_timeout == 45
        assert config.alert_on_fail_open is False
        assert config.alert_cooldown_seconds == 120