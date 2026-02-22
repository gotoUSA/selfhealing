"""
Rate Limiter API Client.

L1/L2 Rate Limiting, Quota, Throttling 관련 API.
"""

from typing import Any, Dict, Optional

from .base import BaseClient


class RateLimiterClient:
    """Rate Limiter API 클라이언트."""
    
    def __init__(self, base_client: BaseClient):
        """초기화."""
        self.client = base_client
    
    # =========================================================================
    # Rate Limit Status API (Viewer)
    # =========================================================================
    
    def get_status(self) -> Dict[str, Any]:
        """
        Rate Limiter 상태 조회.
        
        GET /rate-limiter/status/
        """
        response = self.client.api_get("rate-limiter/status/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_client_status(self, client_id: str) -> Dict[str, Any]:
        """
        특정 클라이언트의 Rate Limit 상태 조회.
        
        GET /rate-limiter/clients/{client_id}/
        """
        response = self.client.api_get(f"rate-limiter/clients/{client_id}/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_endpoint_status(self, endpoint: str) -> Dict[str, Any]:
        """
        특정 엔드포인트의 Rate Limit 상태 조회.
        
        GET /rate-limiter/endpoints/
        """
        response = self.client.api_get(
            "rate-limiter/endpoints/",
            params={"endpoint": endpoint},
        )
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_metrics(self) -> Dict[str, Any]:
        """
        Rate Limiter 메트릭 조회.
        
        GET /rate-limiter/metrics/
        """
        response = self.client.api_get("rate-limiter/metrics/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Rate Limit Config API (Admin)
    # =========================================================================
    
    def get_config(self) -> Dict[str, Any]:
        """
        Rate Limit 설정 조회.
        
        GET /rate-limiter/config/
        """
        response = self.client.api_get("rate-limiter/config/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def update_config(
        self,
        default_rate: Optional[int] = None,
        default_burst: Optional[int] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Rate Limit 설정 변경.
        
        PUT /rate-limiter/config/
        """
        data = {**kwargs}
        if default_rate is not None:
            data["default_rate"] = default_rate
        if default_burst is not None:
            data["default_burst"] = default_burst
        
        response = self.client.api_put("rate-limiter/config/", json=data)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_endpoint_limit(
        self,
        endpoint: str,
        rate: int,
        burst: int,
        window_seconds: int = 60,
    ) -> Dict[str, Any]:
        """
        특정 엔드포인트의 Rate Limit 설정.
        
        POST /rate-limiter/endpoints/config/
        """
        response = self.client.api_post(
            "rate-limiter/endpoints/config/",
            json={
                "endpoint": endpoint,
                "rate": rate,
                "burst": burst,
                "window_seconds": window_seconds,
            },
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_client_limit(
        self,
        client_id: str,
        rate: int,
        burst: int,
    ) -> Dict[str, Any]:
        """
        특정 클라이언트의 Rate Limit 설정.
        
        POST /rate-limiter/clients/config/
        """
        response = self.client.api_post(
            "rate-limiter/clients/config/",
            json={
                "client_id": client_id,
                "rate": rate,
                "burst": burst,
            },
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Whitelist/Blacklist API (Admin)
    # =========================================================================
    
    def add_to_whitelist(
        self,
        client_id: str,
        reason: str = "Manual addition",
    ) -> Dict[str, Any]:
        """
        클라이언트를 화이트리스트에 추가.
        
        POST /rate-limiter/whitelist/
        """
        response = self.client.api_post(
            "rate-limiter/whitelist/",
            json={"client_id": client_id, "reason": reason},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def remove_from_whitelist(self, client_id: str) -> Dict[str, Any]:
        """
        클라이언트를 화이트리스트에서 제거.
        
        DELETE /rate-limiter/whitelist/{client_id}/
        """
        response = self.client.api_delete(f"rate-limiter/whitelist/{client_id}/")
        if response.status_code in (200, 204):
            return {"status": "removed"}
        return {"status": "error", "status_code": response.status_code}
    
    def add_to_blacklist(
        self,
        client_id: str,
        reason: str,
        duration_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        클라이언트를 블랙리스트에 추가.
        
        POST /rate-limiter/blacklist/
        """
        data = {"client_id": client_id, "reason": reason}
        if duration_seconds:
            data["duration_seconds"] = duration_seconds
        
        response = self.client.api_post("rate-limiter/blacklist/", json=data)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def remove_from_blacklist(self, client_id: str) -> Dict[str, Any]:
        """
        클라이언트를 블랙리스트에서 제거.
        
        DELETE /rate-limiter/blacklist/{client_id}/
        """
        response = self.client.api_delete(f"rate-limiter/blacklist/{client_id}/")
        if response.status_code in (200, 204):
            return {"status": "removed"}
        return {"status": "error", "status_code": response.status_code}
    
    def get_whitelist(self) -> Dict[str, Any]:
        """
        화이트리스트 조회.
        
        GET /rate-limiter/whitelist/
        """
        response = self.client.api_get("rate-limiter/whitelist/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_blacklist(self) -> Dict[str, Any]:
        """
        블랙리스트 조회.
        
        GET /rate-limiter/blacklist/
        """
        response = self.client.api_get("rate-limiter/blacklist/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # XTest API (Chaos)
    # =========================================================================
    
    def xtest_simulate_rate_limit(
        self,
        client_id: str,
        blocked_requests: int = 100,
    ) -> Dict[str, Any]:
        """
        XTest 모드에서 Rate Limit 시뮬레이션.
        
        POST /xtest/rate-limiter/simulate/
        """
        response = self.client.xtest_post(
            "rate-limiter/simulate/",
            json={
                "client_id": client_id,
                "blocked_requests": blocked_requests,
            },
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def xtest_reset(self) -> Dict[str, Any]:
        """
        XTest 모드에서 Rate Limiter 리셋.
        
        POST /xtest/rate-limiter/reset/
        """
        response = self.client.xtest_post("rate-limiter/reset/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def xtest_exhaust_quota(self, client_id: str) -> Dict[str, Any]:
        """
        XTest 모드에서 클라이언트 할당량 소진.
        
        POST /xtest/rate-limiter/exhaust/
        """
        response = self.client.xtest_post(
            "rate-limiter/exhaust/",
            json={"client_id": client_id},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    def is_client_rate_limited(self, client_id: str) -> bool:
        """클라이언트가 Rate Limit에 걸렸는지 확인."""
        status = self.get_client_status(client_id)
        return status.get("is_limited", False)
    
    def get_remaining_quota(self, client_id: str) -> int:
        """클라이언트의 남은 할당량."""
        status = self.get_client_status(client_id)
        return status.get("remaining", 0)
    
    def is_blacklisted(self, client_id: str) -> bool:
        """클라이언트가 블랙리스트에 있는지 확인."""
        blacklist = self.get_blacklist()
        clients = blacklist.get("clients", [])
        return client_id in [c.get("client_id") for c in clients]
