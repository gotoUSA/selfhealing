"""
편의 함수 테스트.

check_automation_allowed, is_automation_allowed 등 편의 함수 테스트.
"""

from unittest.mock import patch


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
            get_error_budget_gate,
            is_automation_allowed,
        )

        gate = get_error_budget_gate()

        with patch.object(gate, '_get_error_budget_percent', return_value=75.0):
            gate.clear_cache()
            allowed = is_automation_allowed()

        assert allowed is True
