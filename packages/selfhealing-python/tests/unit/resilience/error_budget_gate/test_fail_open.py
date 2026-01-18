"""
Fail-Open 동작 테스트.

에러 예산 조회 실패 시 Fail-Open/Fail-Close 동작 테스트.
"""

import pytest
from unittest.mock import patch


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
