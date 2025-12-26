"""
L2 Storage Resilience API Client.

L2 저장소 상태, 동기화, Shadow Log, Drift Reconciliation 관련 API.
"""

from typing import Any, Dict, Optional

from .base import BaseClient


class L2StorageClient:
    """L2 Storage Resilience API 클라이언트."""
    
    def __init__(self, base_client: BaseClient):
        """초기화."""
        self.client = base_client
    
    # =========================================================================
    # Configuration
    # =========================================================================
    
    def get_config(self) -> Dict[str, Any]:
        """GET /l2-storage/config/"""
        response = self.client.api_get("l2-storage/config/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_config(self, **kwargs) -> Dict[str, Any]:
        """PUT /l2-storage/config/"""
        response = self.client.api_put("l2-storage/config/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def reset_config(self) -> Dict[str, Any]:
        """POST /l2-storage/config/reset/"""
        response = self.client.api_post("l2-storage/config/reset/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Status & Health
    # =========================================================================
    
    def get_status(self) -> Dict[str, Any]:
        """GET /l2-storage/status/"""
        response = self.client.api_get("l2-storage/status/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_health(self) -> Dict[str, Any]:
        """GET /l2-storage/health/"""
        response = self.client.api_get("l2-storage/health/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def reset_health(self) -> Dict[str, Any]:
        """POST /l2-storage/health/reset/"""
        response = self.client.api_post("l2-storage/health/reset/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_metrics(self) -> Dict[str, Any]:
        """GET /l2-storage/metrics/"""
        response = self.client.api_get("l2-storage/metrics/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Shadow Log
    # =========================================================================
    
    def list_shadow_logs(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """GET /l2-storage/shadow-log/"""
        response = self.client.api_get(
            "l2-storage/shadow-log/",
            params={"limit": limit, "offset": offset},
        )
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_shadow_log_stats(self) -> Dict[str, Any]:
        """GET /l2-storage/shadow-log/stats/"""
        response = self.client.api_get("l2-storage/shadow-log/stats/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def clear_shadow_log(self) -> Dict[str, Any]:
        """POST /l2-storage/shadow-log/clear/"""
        response = self.client.api_post("l2-storage/shadow-log/clear/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def analyze_shadow_log(self) -> Dict[str, Any]:
        """POST /l2-storage/shadow-log/analyze/"""
        response = self.client.api_post("l2-storage/shadow-log/analyze/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def replay_shadow_log(self, dry_run: bool = True) -> Dict[str, Any]:
        """POST /l2-storage/shadow-log/replay/"""
        response = self.client.api_post(
            "l2-storage/shadow-log/replay/",
            json={"dry_run": dry_run},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_shadow_log_by_service(self, service_name: str) -> Dict[str, Any]:
        """GET /l2-storage/shadow-log/service/{service_name}/"""
        response = self.client.api_get(f"l2-storage/shadow-log/service/{service_name}/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Sync Operations
    # =========================================================================
    
    def sync_from_l2(self, services: Optional[list] = None) -> Dict[str, Any]:
        """POST /l2-storage/sync/from-l2/"""
        data = {}
        if services:
            data["services"] = services
        response = self.client.api_post("l2-storage/sync/from-l2/", json=data)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def sync_to_l2(self, services: Optional[list] = None) -> Dict[str, Any]:
        """POST /l2-storage/sync/to-l2/"""
        data = {}
        if services:
            data["services"] = services
        response = self.client.api_post("l2-storage/sync/to-l2/", json=data)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Drift Reconciliation
    # =========================================================================
    
    def get_drift_stats(self) -> Dict[str, Any]:
        """GET /l2-storage/drift/stats/"""
        response = self.client.api_get("l2-storage/drift/stats/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_drift_history(self, limit: int = 50) -> Dict[str, Any]:
        """GET /l2-storage/drift/history/"""
        response = self.client.api_get(
            "l2-storage/drift/history/",
            params={"limit": limit},
        )
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def trigger_reconciliation(self, dry_run: bool = True) -> Dict[str, Any]:
        """POST /l2-storage/drift/reconcile/"""
        response = self.client.api_post(
            "l2-storage/drift/reconcile/",
            json={"dry_run": dry_run},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def reconcile_service(
        self,
        service_name: str,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        """POST /l2-storage/drift/reconcile/{service_name}/"""
        response = self.client.api_post(
            f"l2-storage/drift/reconcile/{service_name}/",
            json={"dry_run": dry_run},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    def is_healthy(self) -> bool:
        """L2 Storage 건강 상태."""
        health = self.get_health()
        return health.get("healthy", False)
    
    def has_drift(self) -> bool:
        """Drift 발생 여부."""
        stats = self.get_drift_stats()
        return stats.get("has_drift", False)
    
    def get_pending_shadow_count(self) -> int:
        """대기 중인 Shadow Log 개수."""
        stats = self.get_shadow_log_stats()
        return stats.get("pending_count", 0)
