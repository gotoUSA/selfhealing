"""
Tests for CorrelationIndex — rolling 누적 인덱스 및 Copy-on-Write 패턴.

테스트 분류 (UNIT_TEST_GUIDELINES §0):
- Contract: CorrelationIndex frozen 특성, 필드 기본값 검증
- Behavior: _rebuild_correlation_index()의 누적/중복제거/최대크기 동작 검증

참조 소스:
- services/correlation_engine/co_occurrence_tracker.py
  (CorrelationIndex, CoOccurrenceTracker._rebuild_correlation_index)
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from selfhealing.services.correlation_engine.co_occurrence_tracker import (
    CoOccurrenceTracker,
    CorrelationIndex,
    CorrelationResult,
    EventPairKey,
)

# =============================================================================
# CorrelationIndex 계약 검증
# =============================================================================


class TestCorrelationIndexContract:
    """CorrelationIndex 설계 계약값 검증."""

    def test_frozen_dataclass_prevents_mutation(self):
        """CorrelationIndex는 frozen=True이므로 필드 변경이 금지된다."""
        index = CorrelationIndex()
        with pytest.raises(FrozenInstanceError):
            index.by_event_type = {"x": []}  # type: ignore[misc]

    def test_default_empty_index(self):
        """기본 생성 시 빈 인덱스와 result_count=0."""
        index = CorrelationIndex()
        assert index.by_event_type == {}
        assert index.result_count == 0

    def test_result_count_matches_provided_data(self):
        """result_count 필드가 초기화 시 전달한 값과 일치."""
        index = CorrelationIndex(by_event_type={"A": []}, result_count=5)
        assert index.result_count == 5


# =============================================================================
# _rebuild_correlation_index 동작 검증
# =============================================================================


def _make_correlation_result(
    type_a: str,
    type_b: str,
    score: float = 0.5,
) -> CorrelationResult:
    """테스트용 CorrelationResult 생성 헬퍼."""
    pair = EventPairKey(type_a, type_b)
    return CorrelationResult(
        pair=pair,
        correlation_score=score,
        direction=None,
        evidence="test",
        sample_count=10,
        confidence=0.8,
    )


class TestRebuildCorrelationIndexBehavior:
    """_rebuild_correlation_index() 동작 검증."""

    def _make_tracker(self, max_accumulated: int = 500) -> CoOccurrenceTracker:
        """테스트용 CoOccurrenceTracker 생성."""
        tracker = CoOccurrenceTracker()
        tracker._max_accumulated_results = max_accumulated
        return tracker

    def test_single_rebuild_creates_index(self):
        """새 결과 한 번 투입하면 인덱스가 생성된다."""
        tracker = self._make_tracker()
        results = [_make_correlation_result("A", "B")]

        tracker._rebuild_correlation_index(results)

        index = tracker.get_correlation_index()
        assert index.result_count == 1
        assert "A" in index.by_event_type
        assert "B" in index.by_event_type

    def test_rolling_accumulation_preserves_previous(self):
        """연속 호출 시 이전 결과가 누적 보존된다."""
        tracker = self._make_tracker()

        tracker._rebuild_correlation_index([_make_correlation_result("A", "B")])
        tracker._rebuild_correlation_index([_make_correlation_result("C", "D")])

        index = tracker.get_correlation_index()
        assert index.result_count == 2
        assert "A" in index.by_event_type
        assert "C" in index.by_event_type

    def test_duplicate_pair_key_overwrites_old_result(self):
        """동일 pair.key에 대해 새 결과가 이전 결과를 덮어쓴다."""
        tracker = self._make_tracker()

        old = _make_correlation_result("A", "B", score=0.3)
        new = _make_correlation_result("A", "B", score=0.9)

        tracker._rebuild_correlation_index([old])
        tracker._rebuild_correlation_index([new])

        index = tracker.get_correlation_index()
        assert index.result_count == 1
        # 최신 score로 덮어써짐
        results_for_a = index.by_event_type["A"]
        assert len(results_for_a) == 1
        assert results_for_a[0].correlation_score == 0.9

    def test_max_accumulated_results_caps_old_entries(self):
        """_max_accumulated_results 초과 시 오래된 결과가 제거된다."""
        tracker = self._make_tracker(max_accumulated=3)

        for i in range(5):
            tracker._rebuild_correlation_index([_make_correlation_result(f"E{i}", f"F{i}")])

        index = tracker.get_correlation_index()
        assert index.result_count == 3

    def test_both_event_types_indexed(self):
        """각 CorrelationResult가 event_type_a와 event_type_b 모두에 인덱싱된다."""
        tracker = self._make_tracker()
        tracker._rebuild_correlation_index([_make_correlation_result("X", "Y")])

        index = tracker.get_correlation_index()
        assert len(index.by_event_type["X"]) == 1
        assert len(index.by_event_type["Y"]) == 1
        assert index.by_event_type["X"][0] is index.by_event_type["Y"][0]

    def test_copy_on_write_reference_swap(self):
        """rebuild 후 이전 인덱스 참조는 변경되지 않는다 (Copy-on-Write)."""
        tracker = self._make_tracker()
        tracker._rebuild_correlation_index([_make_correlation_result("A", "B")])

        old_index = tracker.get_correlation_index()

        tracker._rebuild_correlation_index([_make_correlation_result("C", "D")])

        new_index = tracker.get_correlation_index()
        assert old_index is not new_index
        assert old_index.result_count == 1
        assert new_index.result_count == 2

    def test_initial_index_is_empty(self):
        """초기 상태에서 get_correlation_index()는 빈 인덱스를 반환한다."""
        tracker = self._make_tracker()
        index = tracker.get_correlation_index()
        assert index.result_count == 0
        assert index.by_event_type == {}
