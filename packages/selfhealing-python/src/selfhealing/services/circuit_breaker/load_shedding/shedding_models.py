"""
Load Shedding Data Models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SheddingState(str, Enum):
    """Load Shedding 상태."""

    INACTIVE = "inactive"  # Shedding 비활성화
    LEVEL_1 = "level_1"  # 1단계 (low 50% 제한)
    LEVEL_2 = "level_2"  # 2단계 (low+medium 80% 제한)
    LEVEL_3 = "level_3"  # 3단계 (low+medium 완전 차단)
    CUSTOM = "custom"  # 사용자 정의 레벨


@dataclass
class SheddingDecision:
    """
    Load Shedding 결정 결과.

    Attributes:
        allow_request: 요청 허용 여부
        allowed_traffic_percent: 해당 서비스에 허용된 트래픽 비율 (0~100)
        is_shed: Shedding 대상 여부
        reason: 결정 사유
        current_level: 현재 Shedding 레벨
        service_criticality: 서비스 criticality
    """

    allow_request: bool = True
    allowed_traffic_percent: float = 100.0
    is_shed: bool = False
    reason: str = ""
    current_level: str | None = None
    service_criticality: str | None = None


@dataclass
class SheddingStatus:
    """
    Load Shedding 현재 상태.

    Attributes:
        active: Shedding 활성화 여부
        current_state: 현재 Shedding 상태
        current_level_index: 현재 레벨 인덱스 (0-based, -1=inactive)
        critical_error_rate: critical 서비스 평균 에러율
        shed_services: 현재 Shedding 적용 중인 서비스 목록
        timestamp: 상태 조회 시간
    """

    active: bool = False
    current_state: SheddingState = SheddingState.INACTIVE
    current_level_index: int = -1
    current_level_description: str = ""
    critical_error_rate: float = 0.0
    shed_services: list[str] = field(default_factory=list)
    shed_criticality: list[str] = field(default_factory=list)
    traffic_limit: float = 100.0
    timestamp: str = ""
    activated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "active": self.active,
            "current_state": self.current_state.value,
            "current_level_index": self.current_level_index,
            "current_level_description": self.current_level_description,
            "critical_error_rate": self.critical_error_rate,
            "shed_services": self.shed_services,
            "shed_criticality": self.shed_criticality,
            "traffic_limit": self.traffic_limit,
            "timestamp": self.timestamp,
            "activated_at": self.activated_at,
        }


@dataclass
class SheddingAuditEntry:
    """
    Load Shedding Audit 로그 엔트리.

    Attributes:
        event_type: 이벤트 타입
        timestamp: 이벤트 시간
        previous_level: 이전 레벨
        new_level: 새 레벨
        critical_error_rate: critical 에러율
        affected_services: 영향받는 서비스
        reason: 변경 사유
    """

    event_type: str  # SHEDDING_ACTIVATED, SHEDDING_LEVEL_CHANGED, SHEDDING_DEACTIVATED
    timestamp: str
    previous_level: int = -1
    new_level: int = -1
    critical_error_rate: float = 0.0
    affected_services: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "previous_level": self.previous_level,
            "new_level": self.new_level,
            "critical_error_rate": self.critical_error_rate,
            "affected_services": self.affected_services,
            "reason": self.reason,
        }
