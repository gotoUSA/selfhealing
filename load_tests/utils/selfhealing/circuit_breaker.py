"""
Circuit Breaker API Client.

Circuit Breaker 제어 및 상태 조회 API.
"""

from typing import Any, Dict, List, Optional

from .base import BaseClient


class CircuitBreakerClient:
    """Circuit Breaker API 클라이언트."""
    
    def __init__(self, base_client: BaseClient):
        """초기화."""
        self.client = base_client
    
    # =========================================================================
    # Control API (Protected)
    # =========================================================================
    
    def get_all_status(self) -> Dict[str, Any]:
        """
        모든 서비스 CB 상태 조회.
        
        GET /status/
        """
        response = self.client.api_get("status/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_service_status(self, service_name: str) -> Dict[str, Any]:
        """
        특정 서비스 CB 상태 조회.
        
        GET /status/{service_name}/
        """
        response = self.client.api_get(f"status/{service_name}/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def allow(self, service_name: str, reason: str = "API call") -> Dict[str, Any]:
        """
        서비스 Allow (CB Close).
        
        POST /allow/{service_name}/
        """
        response = self.client.api_post(
            f"allow/{service_name}/",
            json={"reason": reason},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def block(self, service_name: str, reason: str = "API call") -> Dict[str, Any]:
        """
        서비스 Block (CB Force Open).
        
        POST /block/{service_name}/
        """
        response = self.client.api_post(
            f"block/{service_name}/",
            json={"reason": reason},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def reset(self, service_name: str, reason: str = "API call") -> Dict[str, Any]:
        """
        서비스 Reset (CB Close with clean state).
        
        POST /reset/{service_name}/
        """
        response = self.client.api_post(
            f"reset/{service_name}/",
            json={"reason": reason},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def control(
        self,
        service_name: str,
        action: str,
        reason: str = "API call",
        environment: str = "ops",
    ) -> Dict[str, Any]:
        """
        CB 제어 액션 실행.
        
        POST /control/
        
        Args:
            service_name: 서비스 이름
            action: allow, block, reset
            reason: 사유
            environment: ops, test, chaos
        """
        response = self.client.api_post(
            "control/",
            json={
                "service_name": service_name,
                "action": action,
                "reason": reason,
                "environment": environment,
            },
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # XTest Mode API (Chaos)
    # =========================================================================
    
    def xtest_get_status(self, service: Optional[str] = None) -> Dict[str, Any]:
        """
        XTest: CB 상태 상세 조회.
        
        GET /xtest/cb-status/?service={service}
        """
        params = {}
        if service:
            params["service"] = service
        response = self.client.xtest_get("cb-status/", params=params)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def xtest_inject_failure(
        self,
        service: str = "database",
        count: int = 5,
    ) -> Dict[str, Any]:
        """
        XTest: CB 장애 주입.
        
        POST /xtest/inject-cb-failure/
        """
        response = self.client.xtest_post(
            "inject-cb-failure/",
            json={"service": service, "count": count},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def xtest_reset(self, service: str = "database") -> Dict[str, Any]:
        """
        XTest: CB 리셋.
        
        POST /xtest/reset-cb/
        """
        response = self.client.xtest_post(
            "reset-cb/",
            json={"service": service},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def xtest_trigger_recovery(
        self,
        service: str = "database",
        success_count: int = 3,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        XTest: CB 복구 트리거.
        
        POST /xtest/trigger-cb-recovery/
        """
        response = self.client.xtest_post(
            "trigger-cb-recovery/",
            json={
                "service": service,
                "success_count": success_count,
                "force": force,
            },
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def xtest_fast_fail_test(self, service: str = "database") -> Dict[str, Any]:
        """
        XTest: Fast Fail 검증.
        
        GET /xtest/fast-fail-test/?service={service}
        """
        response = self.client.xtest_get("fast-fail-test/", params={"service": service})
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    def is_open(self, service_name: str) -> bool:
        """서비스 CB가 Open 상태인지 확인."""
        status = self.get_service_status(service_name)
        return status.get("state") == "open"
    
    def is_closed(self, service_name: str) -> bool:
        """서비스 CB가 Closed 상태인지 확인."""
        status = self.get_service_status(service_name)
        return status.get("state") == "closed"
    
    def is_half_open(self, service_name: str) -> bool:
        """서비스 CB가 Half-Open 상태인지 확인."""
        status = self.get_service_status(service_name)
        return status.get("state") == "half_open"
    
    def get_state(self, service_name: str) -> str:
        """서비스 CB 상태 문자열 반환."""
        status = self.get_service_status(service_name)
        return status.get("state", "unknown")
