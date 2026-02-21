"""
Cell 토폴로지 모델.

Cell 상태(CellState)와 Cell 정보(CellInfo)를 정의합니다.
Cell은 논리적 트래픽 격벽으로, DB/Redis/캐시를 공유하며
물리적 데이터 파티셔닝이 아닙니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class CellState(str, Enum):
    """Cell 상태."""

    ACTIVE = "active"
    """정상 동작 — 트래픽 100% 수신 중."""

    WARMUP = "warmup"
    """예열 중 — 트래픽 점진적 투입 (percentage 기반)."""

    DRAINING = "draining"
    """대피 중 — 신규 트래픽 차단, 기존 요청 완료 대기."""

    ISOLATED = "isolated"
    """격리됨 — 모든 트래픽 차단."""


# Cell 상태 우선순위 (Most Restrictive Wins)
# ISOLATED(3) > DRAINING(2) > WARMUP(1) > ACTIVE(0)
CELL_STATE_PRIORITY: dict[CellState, int] = {
    CellState.ACTIVE: 0,
    CellState.WARMUP: 1,
    CellState.DRAINING: 2,
    CellState.ISOLATED: 3,
}


@dataclass
class CellInfo:
    """Cell 정보."""

    cell_id: str
    """Cell 식별자. 예: 'cell-0', 'cell-3'."""

    state: CellState = CellState.ACTIVE
    """현재 상태."""

    assigned_services: set[str] = field(default_factory=set)
    """할당된 서비스 목록."""

    health_score: float = 1.0
    """건강도 (0.0~1.0). CellHealthAggregator가 갱신."""

    warmup_percentage: float = 0.0
    """WARMUP 상태일 때 트래픽 투입 비율 (0.0~100.0). ACTIVE일 때는 무시."""

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """생성 시각."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """추가 메타데이터."""
