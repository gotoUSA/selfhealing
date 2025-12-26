"""
XTest Mode API Client.

X-Test-Mode 헤더가 필요한 Chaos Monkey 테스트 전용 API.
Stage 48-51에서 추가된 XTest 엔드포인트들.
"""

from typing import Any, Dict, List, Optional

from .base import BaseClient


class XTestClient:
    """XTest Mode (Chaos Monkey) API 클라이언트."""
    
    def __init__(self, base_client: BaseClient):
        """초기화."""
        self.client = base_client
    
    # =========================================================================
    # Circuit Breaker XTest (Stage 48)
    # =========================================================================
    
    def inject_cb_failure(
        self,
        service_name: str,
        failure_type: str = "exception",
        failure_rate: float = 1.0,
        duration_seconds: int = 60,
    ) -> Dict[str, Any]:
        """
        Circuit Breaker 장애 주입.
        
        POST /xtest/inject-cb-failure/
        """
        response = self.client.xtest_post(
            "inject-cb-failure/",
            json={
                "service_name": service_name,
                "failure_type": failure_type,
                "failure_rate": failure_rate,
                "duration_seconds": duration_seconds,
            },
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def reset_cb(self, service_name: str) -> Dict[str, Any]:
        """
        Circuit Breaker 리셋.
        
        POST /xtest/reset-cb/
        """
        response = self.client.xtest_post(
            "reset-cb/",
            json={"service_name": service_name},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_cb_status(self, service_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Circuit Breaker 상세 상태 조회.
        
        GET /xtest/cb-status/
        """
        params = {}
        if service_name:
            params["service_name"] = service_name
        
        response = self.client.xtest_get("cb-status/", params=params)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def fast_fail_test(
        self,
        service_name: str,
        request_count: int = 10,
    ) -> Dict[str, Any]:
        """
        Fast Fail 테스트 (다량 요청 시뮬레이션).
        
        POST /xtest/fast-fail-test/
        """
        response = self.client.xtest_post(
            "fast-fail-test/",
            json={
                "service_name": service_name,
                "request_count": request_count,
            },
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def trigger_cb_recovery(self, service_name: str) -> Dict[str, Any]:
        """
        Circuit Breaker 복구 트리거.
        
        POST /xtest/trigger-cb-recovery/
        """
        response = self.client.xtest_post(
            "trigger-cb-recovery/",
            json={"service_name": service_name},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Error Budget XTest (Stage 48)
    # =========================================================================
    
    def inject_error_budget(
        self,
        slo_name: str = "availability",
        error_count: int = 100,
    ) -> Dict[str, Any]:
        """
        Error Budget 에러 주입.
        
        POST /xtest/inject-error-budget/
        """
        response = self.client.xtest_post(
            "inject-error-budget/",
            json={
                "slo_name": slo_name,
                "error_count": error_count,
            },
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Snapshot XTest (Stage 48-50)
    # =========================================================================
    
    def get_snapshot(self) -> Dict[str, Any]:
        """
        시스템 스냅샷 조회.
        
        GET /xtest/snapshot/
        """
        response = self.client.xtest_get("snapshot/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Observability XTest (Stage 51)
    # =========================================================================
    
    def get_healing_timeline(
        self,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        힐링 타임라인 조회.
        
        GET /xtest/healing-timeline/
        """
        params = {"limit": limit}
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time
        
        response = self.client.xtest_get("healing-timeline/", params=params)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def test_blast_radius(
        self,
        service_name: str,
        failure_type: str = "exception",
    ) -> Dict[str, Any]:
        """
        단일 서비스 Blast Radius 격리 테스트.
        
        POST /xtest/blast-radius-test/
        """
        response = self.client.xtest_post(
            "blast-radius-test/",
            json={
                "service_name": service_name,
                "failure_type": failure_type,
            },
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def test_multi_blast_radius(
        self,
        services: List[str],
        failure_type: str = "exception",
    ) -> Dict[str, Any]:
        """
        다중 서비스 Blast Radius 격리 매트릭스 테스트.
        
        POST /xtest/multi-blast-radius/
        """
        response = self.client.xtest_post(
            "multi-blast-radius/",
            json={
                "services": services,
                "failure_type": failure_type,
            },
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def generate_postmortem(self, incident_id: str) -> Dict[str, Any]:
        """
        Post-mortem 자동 생성.
        
        POST /xtest/generate-postmortem/
        """
        response = self.client.xtest_post(
            "generate-postmortem/",
            json={"incident_id": incident_id},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def record_healing_event(
        self,
        event_type: str,
        service_name: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        힐링 이벤트 기록.
        
        POST /xtest/record-healing-event/
        """
        data = {
            "event_type": event_type,
            "service_name": service_name,
        }
        if details:
            data["details"] = details
        
        response = self.client.xtest_post("record-healing-event/", json=data)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_healing_incidents(
        self,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """
        인시던트 목록 조회.
        
        GET /xtest/healing-incidents/
        """
        params = {"limit": limit}
        if status:
            params["status"] = status
        
        response = self.client.xtest_get("healing-incidents/", params=params)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    def open_circuit(self, service_name: str) -> Dict[str, Any]:
        """Circuit Breaker를 Open 상태로 만들기."""
        return self.inject_cb_failure(
            service_name=service_name,
            failure_type="exception",
            failure_rate=1.0,
        )
    
    def exhaust_error_budget(self, slo_name: str = "availability") -> Dict[str, Any]:
        """Error Budget 소진."""
        return self.inject_error_budget(slo_name=slo_name, error_count=10000)
    
    def full_chaos_test(self, services: List[str]) -> Dict[str, Any]:
        """전체 카오스 테스트 실행."""
        results = {
            "services": services,
            "blast_radius": self.test_multi_blast_radius(services),
            "snapshot": self.get_snapshot(),
            "timeline": self.get_healing_timeline(limit=10),
        }
        return results
