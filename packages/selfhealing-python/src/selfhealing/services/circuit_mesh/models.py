"""
Circuit Mesh Models

하류 CB 상태 시그널, 메쉬 상태 스냅샷 등 CircuitMeshService에서 사용하는 데이터 모델.
ThresholdOverride는 __init__.py에 정의되어 있으며 여기서 re-export한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from selfhealing.core.timezone import now
from selfhealing.services.circuit_breaker.config import CircuitState
from selfhealing.services.circuit_mesh import ThresholdOverride


@dataclass
class DownstreamHealthSignal:
    """하류 서비스의 CB 상태 시그널."""

    service_name: str
    state: CircuitState
    changed_at: datetime
    affected_upstream: list[str] = field(default_factory=list)


@dataclass
class MeshStateSnapshot:
    """메쉬 전체 상태 스냅샷."""

    timestamp: datetime
    cb_states: dict[str, str] = field(default_factory=dict)
    active_overrides: list[ThresholdOverride] = field(default_factory=list)
    downstream_signals: list[DownstreamHealthSignal] = field(default_factory=list)
    recovery_queue: list[str] = field(default_factory=list)

    @classmethod
    def empty(cls) -> MeshStateSnapshot:
        """빈 스냅샷 생성."""
        return cls(timestamp=now())


__all__ = [
    "DownstreamHealthSignal",
    "MeshStateSnapshot",
    "ThresholdOverride",
]
