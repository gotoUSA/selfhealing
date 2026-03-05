"""
Circuit Breaker Mesh Coordinator

하류 CB 상태 기반 상류 임계치 동적 조정을 위한 메쉬 조율 계층.

Components:
- MeshCoordinator: 하류 장애 전파, 프리엠티브 Fallback, 순차 복구 조율
- MeshOverrideStore: 오버라이드 상태 저장소 (Two-Tier Cache)
- Data models: DownstreamHealthSignal, ThresholdOverride, MeshStateSnapshot
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from selfhealing.services.circuit_breaker.config import CircuitState


@dataclass
class DownstreamHealthSignal:
    """하류 서비스의 CB 상태 시그널."""

    service_name: str
    state: CircuitState
    changed_at: datetime
    affected_upstream: list[str] = field(default_factory=list)


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


@dataclass
class MeshStateSnapshot:
    """메쉬 전체 상태 스냅샷."""

    timestamp: datetime
    cb_states: dict[str, CircuitState] = field(default_factory=dict)
    active_overrides: list[ThresholdOverride] = field(default_factory=list)
    downstream_signals: list[DownstreamHealthSignal] = field(default_factory=list)
    recovery_queue: list[str] = field(default_factory=list)


__all__ = [
    "DownstreamHealthSignal",
    "ThresholdOverride",
    "MeshStateSnapshot",
]
