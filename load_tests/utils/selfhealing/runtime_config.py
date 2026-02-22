"""
Runtime Configuration API Client.

동적 설정 관리 (Circuit Breaker, DLQ, Retry, SLA, SLO, Rate Limit 등).
"""

from typing import Any, Dict

from .base import BaseClient


class RuntimeConfigClient:
    """Runtime Configuration API 클라이언트."""
    
    def __init__(self, base_client: BaseClient):
        """초기화."""
        self.client = base_client
    
    # =========================================================================
    # All Config (전체 설정)
    # =========================================================================
    
    def get_all(self) -> Dict[str, Any]:
        """
        전체 설정 조회.
        
        GET /config/
        """
        response = self.client.api_get("config/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def reset_all(self) -> Dict[str, Any]:
        """
        전체 설정 리셋.
        
        POST /config/reset/
        """
        response = self.client.api_post("config/reset/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Pending Changes (대기 중인 변경)
    # =========================================================================
    
    def get_pending(self) -> Dict[str, Any]:
        """
        대기 중인 설정 변경 조회.
        
        GET /config/pending/
        """
        response = self.client.api_get("config/pending/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def cancel_pending(self, pending_id: str) -> Dict[str, Any]:
        """
        대기 중인 변경 취소.
        
        POST /config/pending/{pending_id}/cancel/
        """
        response = self.client.api_post(f"config/pending/{pending_id}/cancel/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Circuit Breaker Config
    # =========================================================================
    
    def get_circuit_breaker(self) -> Dict[str, Any]:
        """GET /config/circuit-breaker/"""
        response = self.client.api_get("config/circuit-breaker/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_circuit_breaker(self, **kwargs) -> Dict[str, Any]:
        """PUT /config/circuit-breaker/"""
        response = self.client.api_put("config/circuit-breaker/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # DLQ Config
    # =========================================================================
    
    def get_dlq(self) -> Dict[str, Any]:
        """GET /config/dlq/"""
        response = self.client.api_get("config/dlq/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_dlq(self, **kwargs) -> Dict[str, Any]:
        """PUT /config/dlq/"""
        response = self.client.api_put("config/dlq/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Retry Config
    # =========================================================================
    
    def get_retry(self) -> Dict[str, Any]:
        """GET /config/retry/"""
        response = self.client.api_get("config/retry/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_retry(self, **kwargs) -> Dict[str, Any]:
        """PUT /config/retry/"""
        response = self.client.api_put("config/retry/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # SLA Config
    # =========================================================================
    
    def get_sla(self) -> Dict[str, Any]:
        """GET /config/sla/"""
        response = self.client.api_get("config/sla/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_sla(self, **kwargs) -> Dict[str, Any]:
        """PUT /config/sla/"""
        response = self.client.api_put("config/sla/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # SLO Config
    # =========================================================================
    
    def get_slo(self) -> Dict[str, Any]:
        """GET /config/slo/"""
        response = self.client.api_get("config/slo/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_slo(self, **kwargs) -> Dict[str, Any]:
        """PUT /config/slo/"""
        response = self.client.api_put("config/slo/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Rate Limit Config
    # =========================================================================
    
    def get_rate_limit(self) -> Dict[str, Any]:
        """GET /config/rate-limit/"""
        response = self.client.api_get("config/rate-limit/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_rate_limit(self, **kwargs) -> Dict[str, Any]:
        """PUT /config/rate-limit/"""
        response = self.client.api_put("config/rate-limit/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Security Config
    # =========================================================================
    
    def get_security(self) -> Dict[str, Any]:
        """GET /config/security/"""
        response = self.client.api_get("config/security/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_security(self, **kwargs) -> Dict[str, Any]:
        """PUT /config/security/"""
        response = self.client.api_put("config/security/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Idempotency Config
    # =========================================================================
    
    def get_idempotency(self) -> Dict[str, Any]:
        """GET /config/idempotency/"""
        response = self.client.api_get("config/idempotency/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_idempotency(self, **kwargs) -> Dict[str, Any]:
        """PUT /config/idempotency/"""
        response = self.client.api_put("config/idempotency/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Notification Config
    # =========================================================================
    
    def get_notification(self) -> Dict[str, Any]:
        """GET /config/notification/"""
        response = self.client.api_get("config/notification/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_notification(self, **kwargs) -> Dict[str, Any]:
        """PUT /config/notification/"""
        response = self.client.api_put("config/notification/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Forensic Config
    # =========================================================================
    
    def get_forensic(self) -> Dict[str, Any]:
        """GET /config/forensic/"""
        response = self.client.api_get("config/forensic/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_forensic(self, **kwargs) -> Dict[str, Any]:
        """PUT /config/forensic/"""
        response = self.client.api_put("config/forensic/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Logging Config
    # =========================================================================
    
    def get_logging(self) -> Dict[str, Any]:
        """GET /config/logging/"""
        response = self.client.api_get("config/logging/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_logging(self, **kwargs) -> Dict[str, Any]:
        """PUT /config/logging/"""
        response = self.client.api_put("config/logging/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Metrics Config
    # =========================================================================
    
    def get_metrics(self) -> Dict[str, Any]:
        """GET /config/metrics/"""
        response = self.client.api_get("config/metrics/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_metrics(self, **kwargs) -> Dict[str, Any]:
        """PUT /config/metrics/"""
        response = self.client.api_put("config/metrics/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Error Budget Config
    # =========================================================================
    
    def get_error_budget(self) -> Dict[str, Any]:
        """GET /config/error-budget/"""
        response = self.client.api_get("config/error-budget/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_error_budget(self, **kwargs) -> Dict[str, Any]:
        """PUT /config/error-budget/"""
        response = self.client.api_put("config/error-budget/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Gate Config
    # =========================================================================
    
    def get_gate(self) -> Dict[str, Any]:
        """GET /config/gate/"""
        response = self.client.api_get("config/gate/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_gate(self, **kwargs) -> Dict[str, Any]:
        """PUT /config/gate/"""
        response = self.client.api_put("config/gate/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Drift Thresholds Config
    # =========================================================================
    
    def get_drift_thresholds(self) -> Dict[str, Any]:
        """GET /config/drift-thresholds/"""
        response = self.client.api_get("config/drift-thresholds/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_drift_thresholds(self, **kwargs) -> Dict[str, Any]:
        """PUT /config/drift-thresholds/"""
        response = self.client.api_put("config/drift-thresholds/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def reset_drift_thresholds(self) -> Dict[str, Any]:
        """POST /config/drift-thresholds/reset/"""
        response = self.client.api_post("config/drift-thresholds/reset/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Governance Config
    # =========================================================================
    
    def get_governance(self) -> Dict[str, Any]:
        """GET /config/governance/"""
        response = self.client.api_get("config/governance/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_governance(self, **kwargs) -> Dict[str, Any]:
        """PUT /config/governance/"""
        response = self.client.api_put("config/governance/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # L2 Storage Config (Governance Managed)
    # =========================================================================
    
    def get_l2_storage(self) -> Dict[str, Any]:
        """GET /config/l2-storage/"""
        response = self.client.api_get("config/l2-storage/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_l2_storage(self, **kwargs) -> Dict[str, Any]:
        """PUT /config/l2-storage/"""
        response = self.client.api_put("config/l2-storage/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Config History & Rollback
    # =========================================================================
    
    def get_history(self, config_type: str, limit: int = 50) -> Dict[str, Any]:
        """
        설정 변경 이력 조회.
        
        GET /config/{config_type}/history/
        """
        response = self.client.api_get(
            f"config/{config_type}/history/",
            params={"limit": limit},
        )
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_version(self, config_type: str, version: int) -> Dict[str, Any]:
        """
        특정 버전 설정 조회.
        
        GET /config/{config_type}/history/{version}/
        """
        response = self.client.api_get(f"config/{config_type}/history/{version}/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def rollback(self, config_type: str, version: int, reason: str = "") -> Dict[str, Any]:
        """
        특정 버전으로 롤백.
        
        POST /config/{config_type}/rollback/
        """
        response = self.client.api_post(
            f"config/{config_type}/rollback/",
            json={"version": version, "reason": reason},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def compare(self, config_type: str, version1: int, version2: int) -> Dict[str, Any]:
        """
        두 버전 비교.
        
        GET /config/{config_type}/compare/
        """
        response = self.client.api_get(
            f"config/{config_type}/compare/",
            params={"version1": version1, "version2": version2},
        )
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
