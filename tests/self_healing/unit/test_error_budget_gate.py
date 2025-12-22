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
