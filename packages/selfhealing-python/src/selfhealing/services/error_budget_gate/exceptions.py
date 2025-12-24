"""
Error Budget Gate - Exceptions.

에러 예산 게이트 관련 예외 클래스.

Reference:
- docs/self_healing/12_ERROR_BUDGET.md
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class AutomationBlockedError(Exception):
    """
    자동화가 에러 예산 게이트에 의해 차단됨.
    
    이 예외가 발생하면 수동 처리로 전환해야 합니다.
    """
    
    def __init__(
        self,
        message: str,
        error_budget_percent: Optional[float] = None,
        threshold_percent: float = 10.0,
        action: str = "",
    ):
        super().__init__(message)
        self.message = message
        self.error_budget_percent = error_budget_percent
        self.threshold_percent = threshold_percent
        self.action = action
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": "AutomationBlockedError",
            "message": self.message,
            "error_budget_percent": self.error_budget_percent,
            "threshold_percent": self.threshold_percent,
            "action": self.action,
            "manual_mode_enforced": True,
        }


__all__ = [
    "AutomationBlockedError",
]
