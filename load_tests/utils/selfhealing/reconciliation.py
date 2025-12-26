"""
Reconciliation API Client.

Shadow Budget, Excluded Periods, FailSafe Periods 관리 API.
Error Budget의 "시스템은 계산하고, 반영은 사람이 결정한다" 철학.
"""

from typing import Any, Dict, Optional

from .base import BaseClient


class ReconciliationClient:
    """Reconciliation (Shadow Budget) API 클라이언트."""
    
    def __init__(self, base_client: BaseClient):
        """초기화."""
        self.client = base_client
    
    # =========================================================================
    # Status & Config
    # =========================================================================
    
    def get_status(self) -> Dict[str, Any]:
        """
        Reconciliation 상태 조회.
        
        GET /reconciliation/status/
        """
        response = self.client.api_get("reconciliation/status/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_config(self) -> Dict[str, Any]:
        """
        Reconciliation 설정 조회.
        
        GET /reconciliation/config/
        """
        response = self.client.api_get("reconciliation/config/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_config(self, **kwargs) -> Dict[str, Any]:
        """
        Reconciliation 설정 변경.
        
        PUT /reconciliation/config/
        """
        response = self.client.api_put("reconciliation/config/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # FailSafe Periods
    # =========================================================================
    
    def get_failsafe_periods(self) -> Dict[str, Any]:
        """
        FailSafe 기간 조회.
        
        GET /reconciliation/failsafe-periods/
        """
        response = self.client.api_get("reconciliation/failsafe-periods/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def add_failsafe_period(
        self,
        start_time: str,
        end_time: str,
        reason: str,
    ) -> Dict[str, Any]:
        """
        FailSafe 기간 추가.
        
        POST /reconciliation/failsafe-periods/
        """
        response = self.client.api_post(
            "reconciliation/failsafe-periods/",
            json={
                "start_time": start_time,
                "end_time": end_time,
                "reason": reason,
            },
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Shadow Budgets
    # =========================================================================
    
    def list_shadow_budgets(
        self,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """
        Shadow Budget 목록 조회.
        
        GET /reconciliation/shadow-budgets/
        """
        params = {"limit": limit}
        if status:
            params["status"] = status
        
        response = self.client.api_get("reconciliation/shadow-budgets/", params=params)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_shadow_budget(self, calculation_id: str) -> Dict[str, Any]:
        """
        Shadow Budget 상세 조회.
        
        GET /reconciliation/shadow-budgets/{calculation_id}/
        """
        response = self.client.api_get(f"reconciliation/shadow-budgets/{calculation_id}/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def approve_shadow_budget(
        self,
        calculation_id: str,
        comment: str = "",
    ) -> Dict[str, Any]:
        """
        Shadow Budget 승인 (실제 예산에 반영).
        
        POST /reconciliation/shadow-budgets/{calculation_id}/approve/
        """
        response = self.client.api_post(
            f"reconciliation/shadow-budgets/{calculation_id}/approve/",
            json={"comment": comment},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def reject_shadow_budget(
        self,
        calculation_id: str,
        reason: str,
    ) -> Dict[str, Any]:
        """
        Shadow Budget 거부.
        
        POST /reconciliation/shadow-budgets/{calculation_id}/reject/
        """
        response = self.client.api_post(
            f"reconciliation/shadow-budgets/{calculation_id}/reject/",
            json={"reason": reason},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Excluded Periods
    # =========================================================================
    
    def list_excluded_periods(self) -> Dict[str, Any]:
        """
        제외 기간 목록 조회.
        
        GET /reconciliation/excluded-periods/
        """
        response = self.client.api_get("reconciliation/excluded-periods/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def add_excluded_period(
        self,
        start_time: str,
        end_time: str,
        reason: str,
        exclusion_type: str = "maintenance",
    ) -> Dict[str, Any]:
        """
        제외 기간 추가.
        
        POST /reconciliation/excluded-periods/
        """
        response = self.client.api_post(
            "reconciliation/excluded-periods/",
            json={
                "start_time": start_time,
                "end_time": end_time,
                "reason": reason,
                "exclusion_type": exclusion_type,
            },
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_excluded_period(self, exclusion_id: str) -> Dict[str, Any]:
        """
        제외 기간 상세 조회.
        
        GET /reconciliation/excluded-periods/{exclusion_id}/
        """
        response = self.client.api_get(f"reconciliation/excluded-periods/{exclusion_id}/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def delete_excluded_period(self, exclusion_id: str) -> Dict[str, Any]:
        """
        제외 기간 삭제.
        
        DELETE /reconciliation/excluded-periods/{exclusion_id}/
        """
        response = self.client.api_delete(f"reconciliation/excluded-periods/{exclusion_id}/")
        if response.status_code in (200, 204):
            return {"status": "deleted"}
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    def has_pending_approvals(self) -> bool:
        """승인 대기 중인 Shadow Budget이 있는지."""
        result = self.list_shadow_budgets(status="pending")
        budgets = result.get("shadow_budgets", [])
        return len(budgets) > 0
    
    def get_pending_count(self) -> int:
        """승인 대기 중인 Shadow Budget 개수."""
        result = self.list_shadow_budgets(status="pending")
        budgets = result.get("shadow_budgets", [])
        return len(budgets)
    
    def approve_all_pending(self, comment: str = "Bulk approval") -> Dict[str, Any]:
        """모든 대기 중인 Shadow Budget 승인."""
        result = self.list_shadow_budgets(status="pending")
        budgets = result.get("shadow_budgets", [])
        
        approved = []
        failed = []
        for budget in budgets:
            calc_id = budget.get("calculation_id")
            if calc_id:
                approval = self.approve_shadow_budget(calc_id, comment)
                if approval.get("status") != "error":
                    approved.append(calc_id)
                else:
                    failed.append(calc_id)
        
        return {
            "approved": approved,
            "failed": failed,
            "total": len(budgets),
        }
