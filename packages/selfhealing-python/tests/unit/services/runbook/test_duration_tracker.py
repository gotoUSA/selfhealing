"""
DurationTracker 인메모리 모드의 동작 검증.

테스트 대상: selfhealing.services.runbook.duration_tracker.DurationTracker
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from selfhealing.services.runbook.duration_tracker import DurationTracker


# =============================================================================
# Contract Tests
# =============================================================================


class TestDurationTrackerConstantsContract:
    """DurationTracker 상수 계약값 검증."""

    def test_key_template_format(self):
        """KEY_TEMPLATE에 {runbook_id} 플레이스홀더가 포함된다."""
        assert "{runbook_id}" in DurationTracker.KEY_TEMPLATE

    def test_key_template_prefix(self):
        """KEY_TEMPLATE은 'selfhealing:pattern:' 프리픽스로 시작한다."""
        assert DurationTracker.KEY_TEMPLATE.startswith("selfhealing:pattern:")

    def test_extra_ttl_seconds_value(self):
        """EXTRA_TTL_SECONDS는 30초."""
        assert DurationTracker.EXTRA_TTL_SECONDS == 30


# =============================================================================
# Behavior Tests — 인메모리 모드
# =============================================================================


class TestDurationTrackerMemoryBehavior:
    """DurationTracker 인메모리 모드 동작 검증."""

    def test_initial_check_returns_false(self):
        """기록 없이 check 호출하면 False 반환."""
        tracker = DurationTracker()
        assert tracker.check_duration_met("rb1", 30) is False

    def test_record_then_check_before_duration_returns_false(self):
        """기록 직후 check 하면 (지속 시간 미충족) False."""
        tracker = DurationTracker()
        tracker.record_condition_met("rb1", 30)
        assert tracker.check_duration_met("rb1", 30) is False

    def test_record_preserves_first_met_time(self):
        """중복 기록 시 최초 시점이 보존된다 (SET NX 의미론)."""
        tracker = DurationTracker()

        # Given: 첫 기록
        tracker.record_condition_met("rb1", 30)
        first_time = tracker._memory_store["rb1"]

        # When: 두 번째 기록 시도
        tracker.record_condition_met("rb1", 30)

        # Then: 시점 변경 없음
        assert tracker._memory_store["rb1"] == first_time

    def test_clear_removes_record(self):
        """clear_condition 호출 시 기록이 삭제된다."""
        tracker = DurationTracker()
        tracker.record_condition_met("rb1", 30)
        tracker.clear_condition("rb1")
        assert "rb1" not in tracker._memory_store

    def test_clear_nonexistent_key_no_error(self):
        """존재하지 않는 키 clear 호출 시 에러가 발생하지 않는다."""
        tracker = DurationTracker()
        tracker.clear_condition("nonexistent")  # Should not raise

    def test_check_duration_met_after_sufficient_time(self):
        """지속 시간 충족 시 True 반환."""
        tracker = DurationTracker()

        # Given: 과거 시점을 직접 주입 (31초 전)
        import time

        tracker._memory_store["rb1"] = time.time() - 31
        assert tracker.check_duration_met("rb1", 30) is True

    def test_multiple_runbooks_independent(self):
        """서로 다른 런북의 기록은 독립적이다."""
        tracker = DurationTracker()
        tracker.record_condition_met("rb1", 30)
        tracker.record_condition_met("rb2", 60)

        assert "rb1" in tracker._memory_store
        assert "rb2" in tracker._memory_store

        tracker.clear_condition("rb1")
        assert "rb1" not in tracker._memory_store
        assert "rb2" in tracker._memory_store


# =============================================================================
# Behavior Tests — 멱등성
# =============================================================================


class TestDurationTrackerIdempotencyBehavior:
    """DurationTracker 멱등성 검증."""

    def test_record_idempotent(self):
        """동일 런북에 대해 여러 번 record 호출해도 최초 시점만 보존된다."""
        tracker = DurationTracker()

        tracker.record_condition_met("rb1", 30)
        first = tracker._memory_store["rb1"]

        for _ in range(5):
            tracker.record_condition_met("rb1", 30)

        assert tracker._memory_store["rb1"] == first

    def test_clear_idempotent(self):
        """clear 여러 번 호출해도 에러 없음."""
        tracker = DurationTracker()
        tracker.record_condition_met("rb1", 30)
        tracker.clear_condition("rb1")
        tracker.clear_condition("rb1")
        assert "rb1" not in tracker._memory_store
