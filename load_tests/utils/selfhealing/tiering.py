"""
API Tiering Configuration Client.

Tier 정의, 매핑, 오버라이드, Dry Run 관련 API.
"""

from typing import Any, Dict, List, Optional

from .base import BaseClient


class TieringClient:
    """API Tiering Configuration 클라이언트."""
    
    def __init__(self, base_client: BaseClient):
        """초기화."""
        self.client = base_client
    
    # =========================================================================
    # Tier Definitions
    # =========================================================================
    
    def get_definitions(self) -> Dict[str, Any]:
        """
        Tier 정의 조회.
        
        GET /config/tiers/
        """
        response = self.client.api_get("config/tiers/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_definitions(self, tiers: Dict[str, Any]) -> Dict[str, Any]:
        """
        Tier 정의 설정.
        
        PUT /config/tiers/
        """
        response = self.client.api_put("config/tiers/", json={"tiers": tiers})
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def reset(self) -> Dict[str, Any]:
        """
        Tier 설정 리셋.
        
        POST /config/tiers/reset/
        """
        response = self.client.api_post("config/tiers/reset/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Tier Mappings
    # =========================================================================
    
    def get_mappings(self) -> Dict[str, Any]:
        """
        Tier 매핑 조회.
        
        GET /config/tier-mappings/
        """
        response = self.client.api_get("config/tier-mappings/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_mappings(self, mappings: Dict[str, str]) -> Dict[str, Any]:
        """
        Tier 매핑 설정.
        
        PUT /config/tier-mappings/
        """
        response = self.client.api_put("config/tier-mappings/", json={"mappings": mappings})
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Tier Overrides
    # =========================================================================
    
    def get_overrides(self) -> Dict[str, Any]:
        """
        Tier 오버라이드 조회.
        
        GET /config/tier-overrides/
        """
        response = self.client.api_get("config/tier-overrides/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_overrides(self, overrides: Dict[str, Any]) -> Dict[str, Any]:
        """
        Tier 오버라이드 설정.
        
        PUT /config/tier-overrides/
        """
        response = self.client.api_put("config/tier-overrides/", json={"overrides": overrides})
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Dry Run & Resolve
    # =========================================================================
    
    def dry_run(self, endpoint: str, method: str = "GET") -> Dict[str, Any]:
        """
        Tier 적용 시뮬레이션 (Dry Run).
        
        POST /config/tiers/dry-run/
        """
        response = self.client.api_post(
            "config/tiers/dry-run/",
            json={"endpoint": endpoint, "method": method},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def resolve(self, endpoint: str, method: str = "GET") -> Dict[str, Any]:
        """
        특정 엔드포인트의 Tier 조회.
        
        GET /config/tiers/resolve/
        """
        response = self.client.api_get(
            "config/tiers/resolve/",
            params={"endpoint": endpoint, "method": method},
        )
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Export & Import
    # =========================================================================
    
    def export_config(self) -> Dict[str, Any]:
        """
        Tier 설정 내보내기.
        
        GET /config/tiers/export/
        """
        response = self.client.api_get("config/tiers/export/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def import_config(self, config: Dict[str, Any], dry_run: bool = True) -> Dict[str, Any]:
        """
        Tier 설정 가져오기.
        
        POST /config/tiers/import/
        """
        response = self.client.api_post(
            "config/tiers/import/",
            json={"config": config, "dry_run": dry_run},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    def get_tier_for_endpoint(self, endpoint: str) -> str:
        """특정 엔드포인트의 Tier 이름 반환."""
        result = self.resolve(endpoint)
        return result.get("tier", "unknown")
    
    def list_tier_names(self) -> List[str]:
        """모든 Tier 이름 목록."""
        definitions = self.get_definitions()
        tiers = definitions.get("tiers", {})
        return list(tiers.keys())
