"""
CellState / CellInfo 모델 테스트.

테스트 분류 (UNIT_TEST_GUIDELINES §0):
- Contract: CellState 열거값, CellInfo 기본값, 상태 우선순위 계약 검증
- Behavior: CellInfo 인스턴스 동작 검증

참조 소스:
- services/cell_topology/models.py (CellState, CellInfo, CELL_STATE_PRIORITY)
"""

from __future__ import annotations

from datetime import timezone

from selfhealing.services.cell_topology.models import (
    CELL_STATE_PRIORITY,
    CellInfo,
    CellState,
)


class TestCellStateContract:
    """CellState 열거값 계약 검증."""

    def test_active_value(self):
        """ACTIVE 값: 'active'."""
        assert CellState.ACTIVE.value == "active"

    def test_warmup_value(self):
        """WARMUP 값: 'warmup'."""
        assert CellState.WARMUP.value == "warmup"

    def test_draining_value(self):
        """DRAINING 값: 'draining'."""
        assert CellState.DRAINING.value == "draining"

    def test_isolated_value(self):
        """ISOLATED 값: 'isolated'."""
        assert CellState.ISOLATED.value == "isolated"

    def test_state_count(self):
        """CellState는 정확히 4개 상태를 가져야 한다."""
        assert len(CellState) == 4

    def test_is_str_enum(self):
        """CellState는 str 서브클래스여야 한다."""
        assert isinstance(CellState.ACTIVE, str)


class TestCellStatePriorityContract:
    """Cell 상태 우선순위 계약 검증 (Most Restrictive Wins)."""

    def test_active_priority_0(self):
        """ACTIVE 우선순위: 0 (가장 낮음)."""
        assert CELL_STATE_PRIORITY[CellState.ACTIVE] == 0

    def test_warmup_priority_1(self):
        """WARMUP 우선순위: 1."""
        assert CELL_STATE_PRIORITY[CellState.WARMUP] == 1

    def test_draining_priority_2(self):
        """DRAINING 우선순위: 2."""
        assert CELL_STATE_PRIORITY[CellState.DRAINING] == 2

    def test_isolated_priority_3(self):
        """ISOLATED 우선순위: 3 (가장 높음)."""
        assert CELL_STATE_PRIORITY[CellState.ISOLATED] == 3

    def test_all_states_have_priority(self):
        """모든 CellState에 우선순위가 정의되어야 한다."""
        for state in CellState:
            assert state in CELL_STATE_PRIORITY


class TestCellInfoContract:
    """CellInfo 기본값 계약 검증."""

    def test_default_state_is_active(self):
        """기본 상태: ACTIVE."""
        info = CellInfo(cell_id="cell-0")
        assert info.state == CellState.ACTIVE

    def test_default_health_score_1_0(self):
        """기본 건강도: 1.0."""
        info = CellInfo(cell_id="cell-0")
        assert info.health_score == 1.0

    def test_default_warmup_percentage_0(self):
        """기본 warmup_percentage: 0.0."""
        info = CellInfo(cell_id="cell-0")
        assert info.warmup_percentage == 0.0

    def test_default_assigned_services_empty(self):
        """기본 할당 서비스: 빈 set."""
        info = CellInfo(cell_id="cell-0")
        assert info.assigned_services == set()

    def test_default_metadata_empty(self):
        """기본 메타데이터: 빈 dict."""
        info = CellInfo(cell_id="cell-0")
        assert info.metadata == {}


class TestCellInfoBehavior:
    """CellInfo 동작 검증."""

    def test_cell_id_is_stored(self):
        """cell_id가 올바르게 저장되어야 한다."""
        info = CellInfo(cell_id="cell-5")
        assert info.cell_id == "cell-5"

    def test_created_at_is_utc(self):
        """생성 시각은 UTC여야 한다."""
        info = CellInfo(cell_id="cell-0")
        assert info.created_at.tzinfo == timezone.utc

    def test_assigned_services_are_independent(self):
        """서로 다른 CellInfo의 assigned_services는 독립적이어야 한다."""
        info1 = CellInfo(cell_id="cell-0")
        info2 = CellInfo(cell_id="cell-1")
        info1.assigned_services.add("service-a")
        assert "service-a" not in info2.assigned_services

    def test_metadata_are_independent(self):
        """서로 다른 CellInfo의 metadata는 독립적이어야 한다."""
        info1 = CellInfo(cell_id="cell-0")
        info2 = CellInfo(cell_id="cell-1")
        info1.metadata["key"] = "value"
        assert "key" not in info2.metadata

    def test_state_can_be_changed(self):
        """상태를 변경할 수 있어야 한다."""
        info = CellInfo(cell_id="cell-0")
        info.state = CellState.DRAINING
        assert info.state == CellState.DRAINING

    def test_warmup_state_with_percentage(self):
        """WARMUP 상태에서 percentage를 설정할 수 있어야 한다."""
        info = CellInfo(
            cell_id="cell-0",
            state=CellState.WARMUP,
            warmup_percentage=30.0,
        )
        assert info.state == CellState.WARMUP
        assert info.warmup_percentage == 30.0
