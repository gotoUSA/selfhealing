"""
Error Budget API Client.

Error Budget 상태, 이력, 배포 정책 관련 API.
"""

from typing import Any, Dict, Optional

from .base import BaseClient


class ErrorBudgetClient:
    """Error Budget API 클라이언트."""
    
    def __init__(self, base_client: BaseClient):
        """초기화."""
        self.client = base_client
    
    # =========================================================================
    # Error Budget Status
    # =========================================================================
    
    def get_status(self, slo_name: str = "availability") -> Dict[str, Any]:
        """
        Error Budget 상태 조회.
        
        GET /error-budget/status/
        """
        response = self.client.api_get(
            "error-budget/status/",
            params={"slo_name": slo_name},
        )
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_history(
        self,
        limit: int = 50,
        decision_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Error Budget 결정 이력 조회.
        
        GET /error-budget/history/
        """
        params = {"limit": limit}
        if decision_type:
            params["decision_type"] = decision_type
        
        response = self.client.api_get("error-budget/history/", params=params)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Chaos/Test APIs
    # =========================================================================
    
    def record_error(
        self,
        error_count: int = 1,
        error_type: str = "simulated",
        service_name: str = "test",
    ) -> Dict[str, Any]:
        """
        Error Budget 에러 기록 (Chaos용).
        
        POST /error-budget/record/
        """
        response = self.client.api_post(
            "error-budget/record/",
            json={
                "error_count": error_count,
                "error_type": error_type,
                "service_name": service_name,
            },
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def exhaust(self) -> Dict[str, Any]:
        """
        Error Budget 소진 시뮬레이션 (Test용).
        
        POST /error-budget/exhaust/
        """
        response = self.client.api_post("error-budget/exhaust/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def reset_simulation(self) -> Dict[str, Any]:
        """
        시뮬레이션 리셋.
        
        POST /error-budget/reset-simulation/
        """
        response = self.client.api_post("error-budget/reset-simulation/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # XTest Mode
    # =========================================================================
    
    def xtest_inject(self, amount: float = 1.0) -> Dict[str, Any]:
        """
        XTest: Error Budget 차감.
        
        POST /xtest/inject-error-budget/
        """
        response = self.client.xtest_post(
            "inject-error-budget/",
            json={"amount": amount},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Deployment Policy
    # =========================================================================
    
    def get_deployment_verdict(self) -> Dict[str, Any]:
        """
        배포 판정 조회.
        
        GET /deployment-policy/verdict/
        """
        response = self.client.api_get("deployment-policy/verdict/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def acknowledge_freeze(self, reason: str = "Acknowledged") -> Dict[str, Any]:
        """
        배포 동결 확인.
        
        POST /deployment-policy/acknowledge/
        """
        response = self.client.api_post(
            "deployment-policy/acknowledge/",
            json={"reason": reason},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def override_freeze(
        self,
        reason: str,
        duration_minutes: int = 30,
    ) -> Dict[str, Any]:
        """
        배포 동결 오버라이드.
        
        POST /deployment-policy/override/
        """
        response = self.client.api_post(
            "deployment-policy/override/",
            json={
                "reason": reason,
                "duration_minutes": duration_minutes,
            },
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def lift_freeze(self, reason: str = "Freeze lifted") -> Dict[str, Any]:
        """
        배포 동결 해제.
        
        POST /deployment-policy/lift/
        """
        response = self.client.api_post(
            "deployment-policy/lift/",
            json={"reason": reason},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_active_override(self) -> Dict[str, Any]:
        """
        활성 오버라이드 조회.
        
        GET /deployment-policy/active-override/
        """
        response = self.client.api_get("deployment-policy/active-override/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    def get_remaining_percent(self) -> float:
        """남은 Error Budget 퍼센트."""
        status = self.get_status()
        data = status.get("data", {})
        return data.get("remaining_percent", 0.0)
    
    def is_budget_exhausted(self) -> bool:
        """Error Budget이 소진되었는지 확인."""
        return self.get_remaining_percent() <= 0.0
    
    def can_deploy(self) -> bool:
        """배포 가능 여부."""
        verdict = self.get_deployment_verdict()
        return verdict.get("data", {}).get("verdict") == "proceed"
