"""
ErrorBudgetGate 핵심 기능 테스트.

Gate의 열림/경고/차단 상태 및 비활성화 동작 테스트.
"""

from unittest.mock import patch


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
