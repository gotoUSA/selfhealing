"""
Circuit Breaker Mesh Coordinator

하류 CB 상태 기반 상류 임계치 동적 조정을 위한 메쉬 조율 계층.

Components:
- MeshCoordinator: 하류 장애 전파, 프리엠티브 Fallback, 순차 복구 조율
- MeshOverrideStore: 오버라이드 상태 저장소 (Two-Tier Cache)
- ThresholdOverride: 상류 CB 임계치 오버라이드 데이터 모델
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ThresholdOverride:
    """상류 CB에 적용할 동적 임계치 오버라이드."""

    service_name: str
    original_failure_threshold: int
    adjusted_failure_threshold: int
    original_recovery_timeout: int
    adjusted_recovery_timeout: int
    reason: str
    expires_at: datetime
    renewal_count: int = 0


__all__ = [
    "ThresholdOverride",
]
