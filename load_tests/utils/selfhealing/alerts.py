"""
Alerts & Notifications API Client.

알림 설정, 알림 채널, 알림 이력 관련 API.
"""

from typing import Any, Dict, List, Optional

from .base import BaseClient


class AlertsClient:
    """Alerts & Notifications API 클라이언트."""
    
    def __init__(self, base_client: BaseClient):
        """초기화."""
        self.client = base_client
    
    # =========================================================================
    # Alert Rules API (Operator)
    # =========================================================================
    
    def list_rules(self, enabled: Optional[bool] = None) -> Dict[str, Any]:
        """
        알림 규칙 목록 조회.
        
        GET /alerts/rules/
        """
        params = {}
        if enabled is not None:
            params["enabled"] = str(enabled).lower()
        
        response = self.client.api_get("alerts/rules/", params=params)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_rule(self, rule_id: str) -> Dict[str, Any]:
        """
        알림 규칙 상세 조회.
        
        GET /alerts/rules/{rule_id}/
        """
        response = self.client.api_get(f"alerts/rules/{rule_id}/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def create_rule(
        self,
        name: str,
        condition: str,
        severity: str = "warning",
        channels: Optional[List[str]] = None,
        enabled: bool = True,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        알림 규칙 생성.
        
        POST /alerts/rules/
        """
        data = {
            "name": name,
            "condition": condition,
            "severity": severity,
            "enabled": enabled,
            **kwargs,
        }
        if channels:
            data["channels"] = channels
        
        response = self.client.api_post("alerts/rules/", json=data)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def update_rule(self, rule_id: str, **kwargs) -> Dict[str, Any]:
        """
        알림 규칙 수정.
        
        PATCH /alerts/rules/{rule_id}/
        """
        response = self.client.api_patch(f"alerts/rules/{rule_id}/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def delete_rule(self, rule_id: str) -> Dict[str, Any]:
        """
        알림 규칙 삭제.
        
        DELETE /alerts/rules/{rule_id}/
        """
        response = self.client.api_delete(f"alerts/rules/{rule_id}/")
        if response.status_code in (200, 204):
            return {"status": "deleted"}
        return {"status": "error", "status_code": response.status_code}
    
    def toggle_rule(self, rule_id: str, enabled: bool) -> Dict[str, Any]:
        """
        알림 규칙 활성화/비활성화.
        
        POST /alerts/rules/{rule_id}/toggle/
        """
        response = self.client.api_post(
            f"alerts/rules/{rule_id}/toggle/",
            json={"enabled": enabled},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Alert Channels API (Admin)
    # =========================================================================
    
    def list_channels(self) -> Dict[str, Any]:
        """
        알림 채널 목록 조회.
        
        GET /alerts/channels/
        """
        response = self.client.api_get("alerts/channels/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_channel(self, channel_id: str) -> Dict[str, Any]:
        """
        알림 채널 상세 조회.
        
        GET /alerts/channels/{channel_id}/
        """
        response = self.client.api_get(f"alerts/channels/{channel_id}/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def create_channel(
        self,
        name: str,
        channel_type: str,
        config: Dict[str, Any],
        enabled: bool = True,
    ) -> Dict[str, Any]:
        """
        알림 채널 생성.
        
        POST /alerts/channels/
        
        Args:
            name: 채널 이름
            channel_type: email, slack, webhook, sms 등
            config: 채널별 설정 (webhook_url, email_address 등)
            enabled: 활성화 여부
        """
        response = self.client.api_post(
            "alerts/channels/",
            json={
                "name": name,
                "channel_type": channel_type,
                "config": config,
                "enabled": enabled,
            },
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def update_channel(self, channel_id: str, **kwargs) -> Dict[str, Any]:
        """
        알림 채널 수정.
        
        PATCH /alerts/channels/{channel_id}/
        """
        response = self.client.api_patch(f"alerts/channels/{channel_id}/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def delete_channel(self, channel_id: str) -> Dict[str, Any]:
        """
        알림 채널 삭제.
        
        DELETE /alerts/channels/{channel_id}/
        """
        response = self.client.api_delete(f"alerts/channels/{channel_id}/")
        if response.status_code in (200, 204):
            return {"status": "deleted"}
        return {"status": "error", "status_code": response.status_code}
    
    def test_channel(self, channel_id: str) -> Dict[str, Any]:
        """
        알림 채널 테스트.
        
        POST /alerts/channels/{channel_id}/test/
        """
        response = self.client.api_post(f"alerts/channels/{channel_id}/test/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Alert History API (Viewer)
    # =========================================================================
    
    def list_alerts(
        self,
        severity: Optional[str] = None,
        status: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        알림 이력 조회.
        
        GET /alerts/
        """
        params = {"limit": limit}
        if severity:
            params["severity"] = severity
        if status:
            params["status"] = status
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time
        
        response = self.client.api_get("alerts/", params=params)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_alert(self, alert_id: str) -> Dict[str, Any]:
        """
        알림 상세 조회.
        
        GET /alerts/{alert_id}/
        """
        response = self.client.api_get(f"alerts/{alert_id}/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def acknowledge_alert(
        self,
        alert_id: str,
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        알림 확인 (acknowledge).
        
        POST /alerts/{alert_id}/acknowledge/
        """
        data = {}
        if comment:
            data["comment"] = comment
        
        response = self.client.api_post(f"alerts/{alert_id}/acknowledge/", json=data)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def resolve_alert(
        self,
        alert_id: str,
        resolution: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        알림 해결.
        
        POST /alerts/{alert_id}/resolve/
        """
        data = {}
        if resolution:
            data["resolution"] = resolution
        
        response = self.client.api_post(f"alerts/{alert_id}/resolve/", json=data)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def silence_alert(
        self,
        alert_id: str,
        duration_minutes: int = 60,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        알림 묵음.
        
        POST /alerts/{alert_id}/silence/
        """
        data = {"duration_minutes": duration_minutes}
        if reason:
            data["reason"] = reason
        
        response = self.client.api_post(f"alerts/{alert_id}/silence/", json=data)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_alert_statistics(self, period: str = "24h") -> Dict[str, Any]:
        """
        알림 통계 조회.
        
        GET /alerts/statistics/
        """
        response = self.client.api_get("alerts/statistics/", params={"period": period})
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Silences API (Operator)
    # =========================================================================
    
    def list_silences(self, active_only: bool = True) -> Dict[str, Any]:
        """
        묵음 목록 조회.
        
        GET /alerts/silences/
        """
        response = self.client.api_get(
            "alerts/silences/",
            params={"active_only": str(active_only).lower()},
        )
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def create_silence(
        self,
        matcher: Dict[str, Any],
        duration_minutes: int,
        reason: str,
    ) -> Dict[str, Any]:
        """
        묵음 규칙 생성.
        
        POST /alerts/silences/
        """
        response = self.client.api_post(
            "alerts/silences/",
            json={
                "matcher": matcher,
                "duration_minutes": duration_minutes,
                "reason": reason,
            },
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def delete_silence(self, silence_id: str) -> Dict[str, Any]:
        """
        묵음 규칙 삭제.
        
        DELETE /alerts/silences/{silence_id}/
        """
        response = self.client.api_delete(f"alerts/silences/{silence_id}/")
        if response.status_code in (200, 204):
            return {"status": "deleted"}
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    def get_active_alerts_count(self) -> int:
        """활성 알림 개수."""
        result = self.list_alerts(status="active", limit=1000)
        alerts = result.get("alerts", [])
        return len(alerts)
    
    def has_critical_alerts(self) -> bool:
        """치명적 알림 존재 여부."""
        result = self.list_alerts(severity="critical", status="active")
        alerts = result.get("alerts", [])
        return len(alerts) > 0
