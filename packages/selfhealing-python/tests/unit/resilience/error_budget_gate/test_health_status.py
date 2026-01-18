"""
Gate 헬스 상태 테스트.

Gate의 전체 헬스 체크 기능 테스트.
"""

import pytest
from unittest.mock import patch


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
