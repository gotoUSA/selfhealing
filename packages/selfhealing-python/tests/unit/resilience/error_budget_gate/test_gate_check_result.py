"""
GateCheckResult 테스트.

Gate 체크 결과 객체의 변환 및 속성 테스트.
"""

import pytest


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
