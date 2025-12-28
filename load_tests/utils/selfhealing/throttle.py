"""
Adaptive Throttle API Client.

Netflix Gradient Algorithm 기반 Adaptive Throttling API 클라이언트.
"""

from typing import Any, Dict, Optional

from .base import BaseClient


class AdaptiveThrottleClient:
    """
    Adaptive Throttle API 클라이언트.
    
    Netflix Gradient Algorithm을 사용하여 RTT 기반으로
    동적으로 rate limit을 조절하는 스로틀러 관리 API.
    """
    
    def __init__(self, base_client: BaseClient):
        """초기화."""
        self.client = base_client
    
    # =========================================================================
    # Status API
    # =========================================================================
    
    def get_status(self) -> Dict[str, Any]:
        """
        Adaptive Throttle 상태 조회.
        
        GET /throttle/status/
        
        Returns:
            {
                "current_limit": 100,
                "min_limit": 10,
                "max_limit": 500,
                "window_seconds": 60,
                "avg_rtt_ms": 150.5,
                "gradient": 0.05,
                "state": "normal"  # normal, warning, critical
            }
        """
        response = self.client.api_get("throttle/status/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Adaptive Throttle 통계 조회.
        
        GET /throttle/stats/
        
        Returns:
            {
                "total_requests": 10000,
                "allowed": 9500,
                "denied": 500,
                "limit_adjustments": 50,
                "avg_rtt_ms": 150.5
            }
        """
        response = self.client.api_get("throttle/stats/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Check API
    # =========================================================================
    
    def check(self, client_id: str) -> Dict[str, Any]:
        """
        요청 허용 여부 확인.
        
        POST /throttle/check/
        
        Args:
            client_id: 클라이언트 식별자
            
        Returns:
            {
                "allowed": true,
                "current_count": 50,
                "limit": 100,
                "remaining": 50,
                "reset_at": 1735344000.0
            }
        """
        response = self.client.api_post(
            "throttle/check/",
            json={"client_id": client_id}
        )
        if response.status_code == 200:
            return response.json()
        return {"allowed": False, "status_code": response.status_code}
    
    def record_response(self, rtt_ms: float) -> Dict[str, Any]:
        """
        응답 RTT 기록 (limit 조절에 사용).
        
        POST /throttle/record/
        
        Args:
            rtt_ms: Round-Trip Time in milliseconds
            
        Returns:
            {
                "recorded": true,
                "new_limit": 95,
                "gradient": 0.1
            }
        """
        response = self.client.api_post(
            "throttle/record/",
            json={"rtt_ms": rtt_ms}
        )
        if response.status_code == 200:
            return response.json()
        return {"recorded": False, "status_code": response.status_code}
    
    # =========================================================================
    # Config API (Admin)
    # =========================================================================
    
    def get_config(self) -> Dict[str, Any]:
        """
        Throttle 설정 조회.
        
        GET /throttle/config/
        """
        response = self.client.api_get("throttle/config/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def update_config(
        self,
        initial_limit: Optional[int] = None,
        min_limit: Optional[int] = None,
        max_limit: Optional[int] = None,
        sla_warning_ms: Optional[float] = None,
        sla_critical_ms: Optional[float] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Throttle 설정 변경.
        
        PUT /throttle/config/
        """
        data = {**kwargs}
        if initial_limit is not None:
            data["initial_limit"] = initial_limit
        if min_limit is not None:
            data["min_limit"] = min_limit
        if max_limit is not None:
            data["max_limit"] = max_limit
        if sla_warning_ms is not None:
            data["sla_warning_ms"] = sla_warning_ms
        if sla_critical_ms is not None:
            data["sla_critical_ms"] = sla_critical_ms
        
        response = self.client.api_put("throttle/config/", json=data)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def reset(self) -> Dict[str, Any]:
        """
        Throttle 상태 리셋.
        
        POST /throttle/reset/
        """
        response = self.client.api_post("throttle/reset/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
