"""
엣지 케이스 테스트.

임계값 경계, 음수/0/100% 예산 등 엣지 케이스 테스트.
"""

import pytest
from unittest.mock import patch


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
