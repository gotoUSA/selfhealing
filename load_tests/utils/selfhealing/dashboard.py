"""
Dashboard & Control API Client.

대시보드, 시스템 제어, Pool Circuit Breaker 관련 API.
"""

from typing import Any, Dict, Optional

from .base import BaseClient


class DashboardClient:
    """Dashboard & Control API 클라이언트."""
    
    def __init__(self, base_client: BaseClient):
        """초기화."""
        self.client = base_client
    
    # =========================================================================
    # Dashboard
    # =========================================================================
    
    def get_summary(self) -> Dict[str, Any]:
        """
        대시보드 요약 조회.
        
        GET /dashboard/summary/
        """
        response = self.client.api_get("dashboard/summary/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Control API (Service Level)
    # =========================================================================
    
    def control_action(self, action: str, service_name: str, **kwargs) -> Dict[str, Any]:
        """
        제어 액션 실행.
        
        POST /control/
        """
        data = {"action": action, "service_name": service_name, **kwargs}
        response = self.client.api_post("control/", json=data)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_status(self) -> Dict[str, Any]:
        """
        전체 상태 조회.
        
        GET /status/
        """
        response = self.client.api_get("status/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_service_status(self, service_name: str) -> Dict[str, Any]:
        """
        특정 서비스 상태 조회.
        
        GET /status/{service_name}/
        """
        response = self.client.api_get(f"status/{service_name}/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_audit_logs(self, limit: int = 100) -> Dict[str, Any]:
        """
        감사 로그 조회.
        
        GET /audit/
        """
        response = self.client.api_get("audit/", params={"limit": limit})
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Quick Actions
    # =========================================================================
    
    def quick_allow(self, service_name: str, reason: str = "") -> Dict[str, Any]:
        """POST /allow/{service_name}/"""
        response = self.client.api_post(
            f"allow/{service_name}/",
            json={"reason": reason},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def quick_block(self, service_name: str, reason: str = "") -> Dict[str, Any]:
        """POST /block/{service_name}/"""
        response = self.client.api_post(
            f"block/{service_name}/",
            json={"reason": reason},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def quick_reset(self, service_name: str, reason: str = "") -> Dict[str, Any]:
        """POST /reset/{service_name}/"""
        response = self.client.api_post(
            f"reset/{service_name}/",
            json={"reason": reason},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # System Control (Kill Switch)
    # =========================================================================
    
    def get_system_status(self) -> Dict[str, Any]:
        """GET /system/status/"""
        response = self.client.api_get("system/status/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def enable_system(self, reason: str = "") -> Dict[str, Any]:
        """POST /system/enable/"""
        response = self.client.api_post("system/enable/", json={"reason": reason})
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def disable_system(self, reason: str = "") -> Dict[str, Any]:
        """POST /system/disable/"""
        response = self.client.api_post("system/disable/", json={"reason": reason})
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def enable_dry_run(self) -> Dict[str, Any]:
        """POST /system/dry-run/enable/"""
        response = self.client.api_post("system/dry-run/enable/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def disable_dry_run(self) -> Dict[str, Any]:
        """POST /system/dry-run/disable/"""
        response = self.client.api_post("system/dry-run/disable/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Pool Circuit Breaker
    # =========================================================================
    
    def get_pool_cb_status(self) -> Dict[str, Any]:
        """GET /circuit-breaker/pool/status/"""
        response = self.client.api_get("circuit-breaker/pool/status/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def reset_pool_cb(self) -> Dict[str, Any]:
        """POST /circuit-breaker/pool/reset/"""
        response = self.client.api_post("circuit-breaker/pool/reset/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Gate
    # =========================================================================
    
    def reset_gate(self) -> Dict[str, Any]:
        """POST /gate/reset/"""
        response = self.client.api_post("gate/reset/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Metrics Sync (Deprecated but available)
    # =========================================================================
    
    def sync_metrics(self) -> Dict[str, Any]:
        """POST /metrics/sync/ (Deprecated)"""
        response = self.client.api_post("metrics/sync/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_drift_report(self) -> Dict[str, Any]:
        """GET /metrics/drift-report/ (Deprecated)"""
        response = self.client.api_get("metrics/drift-report/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_metrics_status(self) -> Dict[str, Any]:
        """GET /metrics/status/"""
        response = self.client.api_get("metrics/status/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    def is_system_enabled(self) -> bool:
        """시스템 활성화 여부."""
        status = self.get_system_status()
        return status.get("enabled", False)
    
    def is_dry_run_mode(self) -> bool:
        """Dry Run 모드 여부."""
        status = self.get_system_status()
        return status.get("dry_run", False)
