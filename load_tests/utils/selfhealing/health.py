"""
Health & Metrics API Client.

Health Check 및 Metrics 관련 API.
"""

from typing import Any, Dict

from .base import BaseClient


class HealthClient:
    """Health & Metrics API 클라이언트."""
    
    def __init__(self, base_client: BaseClient):
        """초기화."""
        self.client = base_client
    
    # =========================================================================
    # Public Endpoints (인증 불필요)
    # =========================================================================
    
    def ping(self) -> Dict[str, Any]:
        """
        Simple ping (Public).
        
        GET /health/ping/
        """
        response = self.client.api_get("health/ping/")
        return {"status_code": response.status_code, "ok": response.status_code == 200}
    
    def liveness(self) -> Dict[str, Any]:
        """
        Kubernetes Liveness probe (Public).
        
        GET /health/live/
        """
        response = self.client.api_get("health/live/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def readiness(self) -> Dict[str, Any]:
        """
        Kubernetes Readiness probe (Public).
        
        GET /health/ready/
        """
        response = self.client.api_get("health/ready/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Protected Endpoints (인증 필요)
    # =========================================================================
    
    def full_health(self) -> Dict[str, Any]:
        """
        Full health check (Protected).
        
        GET /health/
        """
        response = self.client.api_get("health/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def pool_health(self) -> Dict[str, Any]:
        """
        Connection pool health (Protected).
        
        GET /health/pool/
        """
        response = self.client.api_get("health/pool/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def gate_health(self) -> Dict[str, Any]:
        """
        Error Budget Gate health (Protected).
        
        GET /health/gate/
        """
        response = self.client.api_get("health/gate/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def metrics(self) -> str:
        """
        Prometheus metrics (Protected).
        
        GET /metrics/
        
        Returns:
            Prometheus format metrics text
        """
        response = self.client.api_get("metrics/")
        if response.status_code == 200:
            return response.text
        return ""
    
    def metrics_status(self) -> Dict[str, Any]:
        """
        Metrics status (Governance).
        
        GET /metrics/status/
        """
        response = self.client.api_get("metrics/status/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Circuit Breaker Pool
    # =========================================================================
    
    def cb_pool_status(self) -> Dict[str, Any]:
        """
        Connection pool circuit breaker status.
        
        GET /circuit-breaker/pool/status/
        """
        response = self.client.api_get("circuit-breaker/pool/status/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def cb_pool_reset(self) -> Dict[str, Any]:
        """
        Reset connection pool circuit breaker.
        
        POST /circuit-breaker/pool/reset/
        """
        response = self.client.api_post("circuit-breaker/pool/reset/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
