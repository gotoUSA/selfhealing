"""
Emergency Mode API Client.

비상 모드 상태, 트리거, 해제, 점진적 복구 관련 API.
"""

from typing import Any, Dict, Optional

from .base import BaseClient


class EmergencyClient:
    """Emergency Mode API 클라이언트."""
    
    def __init__(self, base_client: BaseClient):
        """초기화."""
        self.client = base_client
    
    # =========================================================================
    # Status (Viewer)
    # =========================================================================
    
    def get_status(self) -> Dict[str, Any]:
        """
        비상 모드 상태 조회.
        
        GET /emergency/status/
        """
        response = self.client.api_get("emergency/status/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_history(self, limit: int = 50) -> Dict[str, Any]:
        """
        비상 모드 변경 이력 조회.
        
        GET /emergency/history/
        """
        response = self.client.api_get("emergency/history/", params={"limit": limit})
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_levels(self) -> Dict[str, Any]:
        """
        비상 레벨 목록 조회.
        
        GET /emergency/levels/
        """
        response = self.client.api_get("emergency/levels/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_config(self) -> Dict[str, Any]:
        """
        비상 모드 설정 조회.
        
        GET /emergency/config/
        """
        response = self.client.api_get("emergency/config/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Control (Admin)
    # =========================================================================
    
    def trigger(
        self,
        level: str = "LEVEL_1",
        reason: str = "Manual trigger",
        duration_minutes: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        비상 모드 활성화.
        
        POST /emergency/trigger/
        
        Args:
            level: LEVEL_1, LEVEL_2, LEVEL_3
            reason: 활성화 사유
            duration_minutes: 지속 시간 (None이면 수동 해제 필요)
        """
        data = {
            "level": level,
            "reason": reason,
        }
        if duration_minutes:
            data["duration_minutes"] = duration_minutes
        
        response = self.client.api_post("emergency/trigger/", json=data)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def release(self, reason: str = "Manual release") -> Dict[str, Any]:
        """
        비상 모드 해제.
        
        POST /emergency/release/
        """
        response = self.client.api_post(
            "emergency/release/",
            json={"reason": reason},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def start_gradual_recovery(
        self,
        target_level: str = "NORMAL",
        step_duration_seconds: int = 60,
    ) -> Dict[str, Any]:
        """
        점진적 복구 시작.
        
        POST /emergency/gradual-recovery/
        """
        response = self.client.api_post(
            "emergency/gradual-recovery/",
            json={
                "target_level": target_level,
                "step_duration_seconds": step_duration_seconds,
            },
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def stop_recovery(self) -> Dict[str, Any]:
        """
        점진적 복구 중지.
        
        POST /emergency/stop-recovery/
        """
        response = self.client.api_post("emergency/stop-recovery/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def update_config(self, **kwargs) -> Dict[str, Any]:
        """
        비상 모드 설정 변경.
        
        PUT /emergency/config/
        """
        response = self.client.api_put("emergency/config/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    def is_active(self) -> bool:
        """비상 모드 활성화 여부."""
        status = self.get_status()
        return status.get("is_active", False)
    
    def get_current_level(self) -> str:
        """현재 비상 레벨."""
        status = self.get_status()
        return status.get("level", "NORMAL")
    
    def is_recovering(self) -> bool:
        """점진적 복구 중인지 확인."""
        status = self.get_status()
        return status.get("is_recovering", False)
