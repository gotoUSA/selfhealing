"""
Governance & Audit API Client.

변경 관리, 거버넌스 정책, 감사 로그 관련 API.
"""

from typing import Any, Dict, Optional

from .base import BaseClient


class GovernanceClient:
    """Governance & Audit API 클라이언트."""
    
    def __init__(self, base_client: BaseClient):
        """초기화."""
        self.client = base_client
    
    # =========================================================================
    # Change Request API (Operator)
    # =========================================================================
    
    def list_change_requests(
        self,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """
        변경 요청 목록 조회.
        
        GET /governance/changes/
        """
        params = {"limit": limit}
        if status:
            params["status"] = status
        
        response = self.client.api_get("governance/changes/", params=params)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_change_request(self, request_id: str) -> Dict[str, Any]:
        """
        변경 요청 상세 조회.
        
        GET /governance/changes/{request_id}/
        """
        response = self.client.api_get(f"governance/changes/{request_id}/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def create_change_request(
        self,
        title: str,
        description: str,
        change_type: str,
        target_component: str,
        changes: Dict[str, Any],
        scheduled_time: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        변경 요청 생성.
        
        POST /governance/changes/
        """
        data = {
            "title": title,
            "description": description,
            "change_type": change_type,
            "target_component": target_component,
            "changes": changes,
        }
        if scheduled_time:
            data["scheduled_time"] = scheduled_time
        
        response = self.client.api_post("governance/changes/", json=data)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def approve_change_request(
        self,
        request_id: str,
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        변경 요청 승인.
        
        POST /governance/changes/{request_id}/approve/
        """
        data = {}
        if comment:
            data["comment"] = comment
        
        response = self.client.api_post(f"governance/changes/{request_id}/approve/", json=data)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def reject_change_request(
        self,
        request_id: str,
        reason: str,
    ) -> Dict[str, Any]:
        """
        변경 요청 거부.
        
        POST /governance/changes/{request_id}/reject/
        """
        response = self.client.api_post(
            f"governance/changes/{request_id}/reject/",
            json={"reason": reason},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def execute_change_request(self, request_id: str) -> Dict[str, Any]:
        """
        승인된 변경 요청 실행.
        
        POST /governance/changes/{request_id}/execute/
        """
        response = self.client.api_post(f"governance/changes/{request_id}/execute/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def rollback_change_request(
        self,
        request_id: str,
        reason: str = "Manual rollback",
    ) -> Dict[str, Any]:
        """
        변경 요청 롤백.
        
        POST /governance/changes/{request_id}/rollback/
        """
        response = self.client.api_post(
            f"governance/changes/{request_id}/rollback/",
            json={"reason": reason},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Policy API (Admin)
    # =========================================================================
    
    def list_policies(self) -> Dict[str, Any]:
        """
        거버넌스 정책 목록 조회.
        
        GET /governance/policies/
        """
        response = self.client.api_get("governance/policies/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_policy(self, policy_id: str) -> Dict[str, Any]:
        """
        정책 상세 조회.
        
        GET /governance/policies/{policy_id}/
        """
        response = self.client.api_get(f"governance/policies/{policy_id}/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def update_policy(
        self,
        policy_id: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        정책 수정.
        
        PATCH /governance/policies/{policy_id}/
        """
        response = self.client.api_patch(f"governance/policies/{policy_id}/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def evaluate_policy(
        self,
        policy_id: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        정책 평가 (드라이런).
        
        POST /governance/policies/{policy_id}/evaluate/
        """
        response = self.client.api_post(
            f"governance/policies/{policy_id}/evaluate/",
            json={"context": context},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Audit Log API (Viewer)
    # =========================================================================
    
    def list_audit_logs(
        self,
        action: Optional[str] = None,
        actor: Optional[str] = None,
        resource_type: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        감사 로그 목록 조회.
        
        GET /governance/audit-logs/
        """
        params = {"limit": limit}
        if action:
            params["action"] = action
        if actor:
            params["actor"] = actor
        if resource_type:
            params["resource_type"] = resource_type
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time
        
        response = self.client.api_get("governance/audit-logs/", params=params)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_audit_log(self, log_id: str) -> Dict[str, Any]:
        """
        감사 로그 상세 조회.
        
        GET /governance/audit-logs/{log_id}/
        """
        response = self.client.api_get(f"governance/audit-logs/{log_id}/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_audit_summary(
        self,
        period: str = "24h",
    ) -> Dict[str, Any]:
        """
        감사 로그 요약 조회.
        
        GET /governance/audit-logs/summary/
        """
        response = self.client.api_get(
            "governance/audit-logs/summary/",
            params={"period": period},
        )
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def export_audit_logs(
        self,
        start_time: str,
        end_time: str,
        format: str = "json",
    ) -> Dict[str, Any]:
        """
        감사 로그 내보내기.
        
        POST /governance/audit-logs/export/
        """
        response = self.client.api_post(
            "governance/audit-logs/export/",
            json={
                "start_time": start_time,
                "end_time": end_time,
                "format": format,
            },
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Compliance API (Viewer)
    # =========================================================================
    
    def get_compliance_status(self) -> Dict[str, Any]:
        """
        컴플라이언스 상태 조회.
        
        GET /governance/compliance/status/
        """
        response = self.client.api_get("governance/compliance/status/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def run_compliance_check(self) -> Dict[str, Any]:
        """
        컴플라이언스 체크 실행.
        
        POST /governance/compliance/check/
        """
        response = self.client.api_post("governance/compliance/check/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    def has_pending_changes(self) -> bool:
        """대기 중인 변경 요청이 있는지 확인."""
        result = self.list_change_requests(status="pending")
        changes = result.get("changes", [])
        return len(changes) > 0
    
    def is_compliant(self) -> bool:
        """컴플라이언스 준수 여부."""
        status = self.get_compliance_status()
        return status.get("is_compliant", False)
    
    # =========================================================================
    # Metrics Status API (신규 추가 - Gap 분석 기반)
    # Reference: selfhealing/api/django/urls.py
    # =========================================================================
    
    def get_metrics_status(self) -> Dict[str, Any]:
        """
        메트릭 상태 조회.
        
        GET /metrics/status/
        """
        response = self.client.api_get("metrics/status/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def sync_metrics(self) -> Dict[str, Any]:
        """
        메트릭 동기화 (Deprecated - governance/reconcile/ 사용 권장).
        
        POST /metrics/sync/
        """
        response = self.client.api_post("metrics/sync/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_drift_report(self) -> Dict[str, Any]:
        """
        드리프트 리포트 조회 (Deprecated - metrics/status/ 사용 권장).
        
        GET /metrics/drift-report/
        """
        response = self.client.api_get("metrics/drift-report/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Governance Reconcile & Mode API (신규 추가)
    # =========================================================================
    
    def reconcile(self, force: bool = False) -> Dict[str, Any]:
        """
        거버넌스 정합성 조정.
        
        POST /governance/reconcile/
        """
        response = self.client.api_post("governance/reconcile/", json={"force": force})
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_governance_mode(self) -> Dict[str, Any]:
        """
        거버넌스 모드 조회.
        
        GET /governance/mode/
        """
        response = self.client.api_get("governance/mode/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_governance_mode(self, mode: str, reason: str = "") -> Dict[str, Any]:
        """
        거버넌스 모드 변경.
        
        POST /governance/mode/
        
        Args:
            mode: 모드 (strict, relaxed, audit-only 등)
            reason: 변경 사유
        """
        response = self.client.api_post(
            "governance/mode/",
            json={"mode": mode, "reason": reason},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_governance_status(self) -> Dict[str, Any]:
        """
        거버넌스 RBAC 상태 조회.
        
        GET /governance/status/
        """
        response = self.client.api_get("governance/status/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # 4-Eyes Approval Workflow API (신규 추가)
    # Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_ROADMAP.md
    # =========================================================================
    
    def list_approval_requests(
        self,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """
        승인 요청 목록 조회.
        
        GET /governance/approval-requests/
        """
        params = {"limit": limit}
        if status:
            params["status"] = status
        
        response = self.client.api_get("governance/approval-requests/", params=params)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def approve_approval_request(
        self,
        request_id: str,
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        승인 요청 승인 (4-Eyes Approval).
        
        POST /governance/approval-requests/{request_id}/approve/
        """
        data = {}
        if comment:
            data["comment"] = comment
        
        response = self.client.api_post(
            f"governance/approval-requests/{request_id}/approve/",
            json=data,
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def reject_approval_request(
        self,
        request_id: str,
        reason: str,
    ) -> Dict[str, Any]:
        """
        승인 요청 거부 (4-Eyes Approval).
        
        POST /governance/approval-requests/{request_id}/reject/
        """
        response = self.client.api_post(
            f"governance/approval-requests/{request_id}/reject/",
            json={"reason": reason},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
