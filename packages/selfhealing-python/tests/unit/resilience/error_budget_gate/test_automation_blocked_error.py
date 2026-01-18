"""
AutomationBlockedError 테스트.

차단 시 발생하는 예외 및 require() 메서드 테스트.
"""

import pytest
from unittest.mock import patch


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
