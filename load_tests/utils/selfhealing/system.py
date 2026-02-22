"""
System Configuration API Client.

동적 설정, 시스템 정보, 로그 레벨, 진단 관련 API.
"""

from typing import Any, Dict, Optional

from .base import BaseClient


class SystemClient:
    """System Configuration API 클라이언트."""
    
    def __init__(self, base_client: BaseClient):
        """초기화."""
        self.client = base_client
    
    # =========================================================================
    # Dynamic Config API (Admin)
    # =========================================================================
    
    def get_config(self, key: Optional[str] = None) -> Dict[str, Any]:
        """
        동적 설정 조회.
        
        GET /system/config/
        """
        params = {}
        if key:
            params["key"] = key
        
        response = self.client.api_get("system/config/", params=params)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_config(self, key: str, value: Any) -> Dict[str, Any]:
        """
        동적 설정 변경.
        
        POST /system/config/
        """
        response = self.client.api_post(
            "system/config/",
            json={"key": key, "value": value},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def bulk_set_config(self, configs: Dict[str, Any]) -> Dict[str, Any]:
        """
        여러 설정 일괄 변경.
        
        POST /system/config/bulk/
        """
        response = self.client.api_post("system/config/bulk/", json={"configs": configs})
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def reset_config(self, key: Optional[str] = None) -> Dict[str, Any]:
        """
        설정 리셋 (기본값으로).
        
        POST /system/config/reset/
        """
        data = {}
        if key:
            data["key"] = key
        
        response = self.client.api_post("system/config/reset/", json=data)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_config_history(self, key: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
        """
        설정 변경 이력 조회.
        
        GET /system/config/history/
        """
        params = {"limit": limit}
        if key:
            params["key"] = key
        
        response = self.client.api_get("system/config/history/", params=params)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # System Info API (Viewer)
    # =========================================================================
    
    def get_info(self) -> Dict[str, Any]:
        """
        시스템 정보 조회.
        
        GET /system/info/
        """
        response = self.client.api_get("system/info/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_version(self) -> Dict[str, Any]:
        """
        버전 정보 조회.
        
        GET /system/version/
        """
        response = self.client.api_get("system/version/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_components(self) -> Dict[str, Any]:
        """
        컴포넌트 목록 조회.
        
        GET /system/components/
        """
        response = self.client.api_get("system/components/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_component_status(self, component: str) -> Dict[str, Any]:
        """
        특정 컴포넌트 상태 조회.
        
        GET /system/components/{component}/
        """
        response = self.client.api_get(f"system/components/{component}/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Log Level API (Admin)
    # =========================================================================
    
    def get_log_level(self) -> Dict[str, Any]:
        """
        현재 로그 레벨 조회.
        
        GET /system/log-level/
        """
        response = self.client.api_get("system/log-level/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_log_level(
        self,
        level: str,
        logger: Optional[str] = None,
        duration_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        로그 레벨 변경.
        
        POST /system/log-level/
        
        Args:
            level: DEBUG, INFO, WARNING, ERROR, CRITICAL
            logger: 특정 로거 이름 (None이면 루트 로거)
            duration_seconds: 임시 변경 시간 (None이면 영구)
        """
        data = {"level": level}
        if logger:
            data["logger"] = logger
        if duration_seconds:
            data["duration_seconds"] = duration_seconds
        
        response = self.client.api_post("system/log-level/", json=data)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Diagnostics API (Admin)
    # =========================================================================
    
    def run_diagnostics(self) -> Dict[str, Any]:
        """
        진단 실행.
        
        POST /system/diagnostics/run/
        """
        response = self.client.api_post("system/diagnostics/run/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_diagnostics_report(self) -> Dict[str, Any]:
        """
        최근 진단 보고서 조회.
        
        GET /system/diagnostics/report/
        """
        response = self.client.api_get("system/diagnostics/report/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def check_dependencies(self) -> Dict[str, Any]:
        """
        의존성 체크.
        
        GET /system/dependencies/
        """
        response = self.client.api_get("system/dependencies/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Maintenance API (Admin)
    # =========================================================================
    
    def enable_maintenance_mode(
        self,
        reason: str = "Scheduled maintenance",
        duration_minutes: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        유지보수 모드 활성화.
        
        POST /system/maintenance/enable/
        """
        data = {"reason": reason}
        if duration_minutes:
            data["duration_minutes"] = duration_minutes
        
        response = self.client.api_post("system/maintenance/enable/", json=data)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def disable_maintenance_mode(self) -> Dict[str, Any]:
        """
        유지보수 모드 비활성화.
        
        POST /system/maintenance/disable/
        """
        response = self.client.api_post("system/maintenance/disable/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_maintenance_status(self) -> Dict[str, Any]:
        """
        유지보수 모드 상태 조회.
        
        GET /system/maintenance/status/
        """
        response = self.client.api_get("system/maintenance/status/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    def is_maintenance_mode(self) -> bool:
        """유지보수 모드 여부."""
        status = self.get_maintenance_status()
        return status.get("enabled", False)
    
    def get_config_value(self, key: str, default: Any = None) -> Any:
        """특정 설정 값 조회."""
        result = self.get_config(key)
        return result.get("value", default)
