"""
DLQ (Dead Letter Queue) API Client.

DLQ 조회, 재시도, 리플레이, 아카이브 관련 API.
"""

from typing import Any, Dict, Optional

from .base import BaseClient


class DLQClient:
    """DLQ API 클라이언트."""
    
    def __init__(self, base_client: BaseClient):
        """초기화."""
        self.client = base_client
    
    # =========================================================================
    # 조회 (Viewer)
    # =========================================================================
    
    def list(
        self,
        status: Optional[str] = None,
        domain: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        DLQ 목록 조회.
        
        GET /dlq/list/
        
        Args:
            status: pending, processing, resolved, archived
            domain: payment, order, etc.
            limit: 최대 개수
            offset: 시작 위치
        """
        params = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        if domain:
            params["domain"] = domain
        
        response = self.client.api_get("dlq/list/", params=params)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get(self, pk: int) -> Dict[str, Any]:
        """
        DLQ 상세 조회.
        
        GET /dlq/<pk>/
        """
        response = self.client.api_get(f"dlq/{pk}/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def stats(self) -> Dict[str, Any]:
        """
        DLQ 통계 조회.
        
        GET /dlq/cleanup/stats/
        """
        response = self.client.api_get("dlq/cleanup/stats/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # 작업 (Operator)
    # =========================================================================
    
    def retry(self, pk: int) -> Dict[str, Any]:
        """
        DLQ 단건 재시도.
        
        POST /dlq/<pk>/retry/
        """
        response = self.client.api_post(f"dlq/{pk}/retry/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def resolve(self, pk: int, notes: str = "Manually resolved") -> Dict[str, Any]:
        """
        DLQ 수동 해결.
        
        POST /dlq/<pk>/resolve/
        """
        response = self.client.api_post(
            f"dlq/{pk}/resolve/",
            json={"notes": notes},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def replay(
        self,
        domain: Optional[str] = None,
        batch_size: int = 50,
    ) -> Dict[str, Any]:
        """
        DLQ 배치 리플레이.
        
        POST /dlq/replay/
        """
        data = {"batch_size": batch_size}
        if domain:
            data["domain"] = domain
        
        response = self.client.api_post("dlq/replay/", json=data)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def archive(self, older_than_days: int = 30) -> Dict[str, Any]:
        """
        오래된 해결 항목 아카이브.
        
        POST /dlq/cleanup/archive/
        """
        response = self.client.api_post(
            "dlq/cleanup/archive/",
            json={"older_than_days": older_than_days},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # 관리 (Admin)
    # =========================================================================
    
    def purge(self, older_than_days: int = 90) -> Dict[str, Any]:
        """
        아카이브된 항목 영구 삭제.
        
        POST /dlq/cleanup/purge/
        """
        response = self.client.api_post(
            "dlq/cleanup/purge/",
            json={"older_than_days": older_than_days},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def create_test_entry(
        self,
        domain: str = "test",
        failure_type: str = "test_failure",
        error_message: str = "Test failure for load testing",
    ) -> Dict[str, Any]:
        """
        테스트 DLQ 엔트리 생성.
        
        POST /dlq/test/create/
        """
        response = self.client.api_post(
            "dlq/test/create/",
            json={
                "domain": domain,
                "failure_type": failure_type,
                "error_message": error_message,
            },
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    def get_pending_count(self) -> int:
        """대기 중인 DLQ 항목 수."""
        stats = self.stats()
        by_status = stats.get("by_status", {})
        return by_status.get("pending", 0)
    
    def get_total_count(self) -> int:
        """전체 DLQ 항목 수."""
        stats = self.stats()
        return stats.get("total", 0)
    
    def has_pending(self) -> bool:
        """대기 중인 항목이 있는지 확인."""
        return self.get_pending_count() > 0
