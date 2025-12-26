"""
Observability API Client.

스냅샷, 타임라인, 포스트모템, 힐링 이벤트 관련 API.
"""

from typing import Any, Dict, List, Optional

from .base import BaseClient


class ObservabilityClient:
    """Observability (스냅샷/타임라인/포스트모템) API 클라이언트."""
    
    def __init__(self, base_client: BaseClient):
        """초기화."""
        self.client = base_client
    
    # =========================================================================
    # Snapshot API (Viewer)
    # =========================================================================
    
    def get_current_snapshot(self) -> Dict[str, Any]:
        """
        현재 시스템 스냅샷 조회.
        
        GET /snapshot/current/
        """
        response = self.client.api_get("snapshot/current/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_snapshot_detail(self, snapshot_id: str) -> Dict[str, Any]:
        """
        특정 스냅샷 상세 조회.
        
        GET /snapshot/{snapshot_id}/
        """
        response = self.client.api_get(f"snapshot/{snapshot_id}/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def list_snapshots(
        self,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        스냅샷 목록 조회.
        
        GET /snapshot/list/
        """
        params = {"limit": limit}
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time
        
        response = self.client.api_get("snapshot/list/", params=params)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def compare_snapshots(
        self,
        snapshot_id_1: str,
        snapshot_id_2: str,
    ) -> Dict[str, Any]:
        """
        두 스냅샷 비교.
        
        GET /snapshot/compare/
        """
        response = self.client.api_get(
            "snapshot/compare/",
            params={
                "snapshot_id_1": snapshot_id_1,
                "snapshot_id_2": snapshot_id_2,
            },
        )
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def create_snapshot(self, label: Optional[str] = None) -> Dict[str, Any]:
        """
        수동 스냅샷 생성.
        
        POST /snapshot/create/
        """
        data = {}
        if label:
            data["label"] = label
        
        response = self.client.api_post("snapshot/create/", json=data)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Timeline API (Viewer)
    # =========================================================================
    
    def get_timeline(
        self,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        event_types: Optional[List[str]] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        힐링 이벤트 타임라인 조회.
        
        GET /timeline/
        """
        params = {"limit": limit}
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time
        if event_types:
            params["event_types"] = ",".join(event_types)
        
        response = self.client.api_get("timeline/", params=params)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_timeline_summary(
        self,
        period: str = "1h",
    ) -> Dict[str, Any]:
        """
        타임라인 요약 조회.
        
        GET /timeline/summary/
        """
        response = self.client.api_get("timeline/summary/", params={"period": period})
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Postmortem API (Viewer/Operator)
    # =========================================================================
    
    def list_postmortems(
        self,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """
        포스트모템 목록 조회.
        
        GET /postmortem/
        """
        params = {"limit": limit}
        if status:
            params["status"] = status
        
        response = self.client.api_get("postmortem/", params=params)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_postmortem(self, postmortem_id: str) -> Dict[str, Any]:
        """
        포스트모템 상세 조회.
        
        GET /postmortem/{postmortem_id}/
        """
        response = self.client.api_get(f"postmortem/{postmortem_id}/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def create_postmortem(
        self,
        incident_id: str,
        title: str,
        summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        포스트모템 생성.
        
        POST /postmortem/
        """
        data = {
            "incident_id": incident_id,
            "title": title,
        }
        if summary:
            data["summary"] = summary
        
        response = self.client.api_post("postmortem/", json=data)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def update_postmortem(
        self,
        postmortem_id: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        포스트모템 수정.
        
        PATCH /postmortem/{postmortem_id}/
        """
        response = self.client.api_patch(f"postmortem/{postmortem_id}/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def generate_postmortem(self, incident_id: str) -> Dict[str, Any]:
        """
        AI 기반 포스트모템 자동 생성.
        
        POST /postmortem/generate/
        """
        response = self.client.api_post(
            "postmortem/generate/",
            json={"incident_id": incident_id},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Healing Events API (Viewer)
    # =========================================================================
    
    def list_healing_events(
        self,
        event_type: Optional[str] = None,
        component: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        힐링 이벤트 목록 조회.
        
        GET /healing-events/
        """
        params = {"limit": limit}
        if event_type:
            params["event_type"] = event_type
        if component:
            params["component"] = component
        
        response = self.client.api_get("healing-events/", params=params)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_healing_event(self, event_id: str) -> Dict[str, Any]:
        """
        힐링 이벤트 상세 조회.
        
        GET /healing-events/{event_id}/
        """
        response = self.client.api_get(f"healing-events/{event_id}/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_healing_statistics(self, period: str = "24h") -> Dict[str, Any]:
        """
        힐링 이벤트 통계 조회.
        
        GET /healing-events/statistics/
        """
        response = self.client.api_get(
            "healing-events/statistics/",
            params={"period": period},
        )
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
