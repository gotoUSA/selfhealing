"""
Corruption Shield API Client.

Multi-Layer Data Integrity Protection API 클라이언트.
L1 (Schema), L2 (Business Rules), L3 (Anomaly Detection).
"""

from typing import Any, Dict, List, Optional

from .base import BaseClient


class CorruptionShieldClient:
    """
    Corruption Shield API 클라이언트.
    
    3계층 데이터 무결성 보호 시스템:
    - L1: 스키마 검증 (SQL Injection, XSS, 타입)
    - L2: 비즈니스 규칙 (금액 범위, 상태 값)
    - L3: 이상 탐지 (Z-Score, IQR)
    """
    
    def __init__(self, base_client: BaseClient):
        """초기화."""
        self.client = base_client
    
    # =========================================================================
    # Status API
    # =========================================================================
    
    def get_status(self) -> Dict[str, Any]:
        """
        Corruption Shield 상태 조회.
        
        GET /corruption-shield/status/
        
        Returns:
            {
                "enabled": true,
                "l1_enabled": true,
                "l2_enabled": true,
                "l3_enabled": true,
                "total_validations": 10000,
                "block_rate_percent": 5.2
            }
        """
        response = self.client.api_get("corruption-shield/status/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Corruption Shield 통계 조회.
        
        GET /corruption-shield/stats/
        
        Returns:
            {
                "total_validations": 10000,
                "passed": 9500,
                "blocked": 500,
                "l1_violations": 200,
                "l2_violations": 250,
                "l3_violations": 50
            }
        """
        response = self.client.api_get("corruption-shield/stats/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Validation API
    # =========================================================================
    
    def validate(
        self,
        data: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        데이터 검증 요청.
        
        POST /corruption-shield/validate/
        
        Args:
            data: 검증할 데이터
            context: 추가 컨텍스트 (expected_amount 등)
            
        Returns:
            {
                "is_valid": true,
                "blocked": false,
                "violations": [],
                "layers": {
                    "l1_passed": true,
                    "l2_passed": true,
                    "l3_passed": true
                },
                "validation_time_ms": 1.5
            }
        """
        payload = {"data": data}
        if context:
            payload["context"] = context
        
        response = self.client.api_post("corruption-shield/validate/", json=payload)
        if response.status_code == 200:
            return response.json()
        return {
            "is_valid": False,
            "blocked": True,
            "status_code": response.status_code
        }
    
    def validate_batch(
        self,
        items: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        배치 데이터 검증 요청.
        
        POST /corruption-shield/validate/batch/
        
        Args:
            items: [{"data": {...}, "context": {...}}, ...]
            
        Returns:
            {
                "total": 10,
                "passed": 8,
                "failed": 2,
                "results": [...]
            }
        """
        response = self.client.api_post(
            "corruption-shield/validate/batch/",
            json={"items": items}
        )
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Violation History API
    # =========================================================================
    
    def get_violations(
        self,
        layer: Optional[str] = None,
        severity: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        최근 위반 이력 조회.
        
        GET /corruption-shield/violations/
        
        Args:
            layer: 필터링할 레이어 (L1, L2, L3)
            severity: 필터링할 심각도 (critical, high, medium, low)
            limit: 조회 개수
            
        Returns:
            {
                "violations": [
                    {
                        "timestamp": "2024-01-15T10:30:00Z",
                        "layer": "L1",
                        "code": "injection_attempt",
                        "message": "SQL injection detected",
                        "severity": "critical",
                        "field": "order_id"
                    }
                ],
                "total_count": 500
            }
        """
        params = {"limit": limit}
        if layer:
            params["layer"] = layer
        if severity:
            params["severity"] = severity
        
        response = self.client.api_get("corruption-shield/violations/", params=params)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Config API (Admin)
    # =========================================================================
    
    def get_config(self) -> Dict[str, Any]:
        """
        Shield 설정 조회.
        
        GET /corruption-shield/config/
        """
        response = self.client.api_get("corruption-shield/config/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def update_config(
        self,
        l1_enabled: Optional[bool] = None,
        l2_enabled: Optional[bool] = None,
        l3_enabled: Optional[bool] = None,
        min_amount: Optional[int] = None,
        max_amount: Optional[int] = None,
        z_score_threshold: Optional[float] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Shield 설정 변경.
        
        PUT /corruption-shield/config/
        """
        data = {**kwargs}
        if l1_enabled is not None:
            data["l1_enabled"] = l1_enabled
        if l2_enabled is not None:
            data["l2_enabled"] = l2_enabled
        if l3_enabled is not None:
            data["l3_enabled"] = l3_enabled
        if min_amount is not None:
            data["min_amount"] = min_amount
        if max_amount is not None:
            data["max_amount"] = max_amount
        if z_score_threshold is not None:
            data["z_score_threshold"] = z_score_threshold
        
        response = self.client.api_put("corruption-shield/config/", json=data)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def reset_stats(self) -> Dict[str, Any]:
        """
        통계 리셋.
        
        POST /corruption-shield/reset/
        """
        response = self.client.api_post("corruption-shield/reset/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # L3 Anomaly Detector API
    # =========================================================================
    
    def get_anomaly_stats(self) -> Dict[str, Any]:
        """
        L3 이상 탐지 통계 조회.
        
        GET /corruption-shield/anomaly/stats/
        """
        response = self.client.api_get("corruption-shield/anomaly/stats/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def train_anomaly_model(
        self,
        field: str,
        samples: List[float],
    ) -> Dict[str, Any]:
        """
        L3 이상 탐지 모델 학습.
        
        POST /corruption-shield/anomaly/train/
        
        Args:
            field: 학습할 필드명 (예: "amount")
            samples: 정상 샘플 값들
        """
        response = self.client.api_post(
            "corruption-shield/anomaly/train/",
            json={"field": field, "samples": samples}
        )
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
