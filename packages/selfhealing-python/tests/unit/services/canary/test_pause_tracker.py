"""
Phase 2: PauseContext, PauseReasonTracker 단위 테스트.

일시 중지 사유 추적 기능 테스트.

Reference: docs/self_healing/middleware_system/74_CANARY_SAFETY_INTERLOCK.md
"""

import pytest

from selfhealing.services.canary.pause_tracker import (
    PauseContext,
    PauseReasonTracker,
    get_pause_reason_tracker,
    reset_pause_reason_tracker,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_tracker():
    """각 테스트 전후로 PauseReasonTracker 싱글톤 초기화."""
    reset_pause_reason_tracker()
    yield
    reset_pause_reason_tracker()


# =============================================================================
# Test: PauseContext
# =============================================================================


class TestPauseContext:
    """PauseContext 테스트."""

    def test_create_pause_context(self):
        """PauseContext 생성."""
        context = PauseContext(
            reason="Emergency LEVEL_2 발생으로 일시 중지",
            triggered_by="interlock",
            emergency_level=2,
            emergency_level_name="LEVEL_2",
            namespace="seoul",
        )

        assert context.reason == "Emergency LEVEL_2 발생으로 일시 중지"
        assert context.triggered_by == "interlock"
        assert context.emergency_level == 2

    def test_explain_interlock_triggered(self):
        """인터락으로 인한 중지 설명."""
        context = PauseContext(
            reason="시스템 안정화 필요",
            triggered_by="interlock",
            emergency_level=2,
            emergency_level_name="LEVEL_2",
            namespace="seoul",
        )

        explanation = context.explain()

        assert "일시 중지" in explanation
        assert "LEVEL_2" in explanation
        assert "seoul" in explanation

    def test_explain_manual_triggered(self):
        """수동 중지 설명."""
        context = PauseContext(
            reason="운영자 요청",
            triggered_by="manual",
        )

        explanation = context.explain()

        assert "수동 중지" in explanation

    def test_explain_chaos_guard_triggered(self):
        """Chaos Guard로 인한 중지 설명."""
        context = PauseContext(
            reason="Chaos 실험 충돌",
            triggered_by="chaos_guard",
        )

        explanation = context.explain()

        assert "Chaos" in explanation

    def test_explain_metrics_triggered(self):
        """메트릭 악화로 인한 중지 설명."""
        context = PauseContext(
            reason="에러율 증가",
            triggered_by="metrics",
        )

        explanation = context.explain()

        assert "메트릭" in explanation

    def test_explain_with_causation_chain(self):
        """CausationChain ID 포함 설명."""
        context = PauseContext(
            reason="테스트",
            triggered_by="interlock",
            causation_chain_id="chain-123",
        )

        explanation = context.explain()

        assert "chain-123" in explanation

    def test_to_dict(self):
        """딕셔너리 변환."""
        context = PauseContext(
            reason="테스트 사유",
            triggered_by="interlock",
            emergency_level=2,
            emergency_level_name="LEVEL_2",
            namespace="seoul",
        )

        data = context.to_dict()

        assert data["reason"] == "테스트 사유"
        assert data["triggered_by"] == "interlock"
        assert data["emergency_level"] == 2
        assert data["namespace"] == "seoul"


# =============================================================================
# Test: PauseReasonTracker
# =============================================================================


class TestPauseReasonTracker:
    """PauseReasonTracker 테스트."""

    def test_record_pause(self):
        """PAUSE 이벤트 기록."""
        tracker = PauseReasonTracker()
        context = PauseContext(
            reason="테스트 사유",
            triggered_by="interlock",
        )

        chain_id = tracker.record_pause("rollout-123", context)

        assert chain_id is not None
        assert len(chain_id) > 0

    def test_record_pause_sets_causation_chain_id(self):
        """record_pause가 causation_chain_id를 설정."""
        tracker = PauseReasonTracker()
        context = PauseContext(
            reason="테스트 사유",
            triggered_by="interlock",
        )

        chain_id = tracker.record_pause("rollout-123", context)

        assert context.causation_chain_id == chain_id

    def test_record_pause_sets_paused_at(self):
        """record_pause가 paused_at을 설정."""
        tracker = PauseReasonTracker()
        context = PauseContext(
            reason="테스트 사유",
            triggered_by="interlock",
        )

        tracker.record_pause("rollout-123", context)

        assert context.paused_at is not None

    def test_get_pause_history(self):
        """PAUSE 이력 조회."""
        tracker = PauseReasonTracker()
        context1 = PauseContext(reason="첫 번째 중지", triggered_by="interlock")
        context2 = PauseContext(reason="두 번째 중지", triggered_by="manual")

        tracker.record_pause("rollout-123", context1)
        tracker.record_pause("rollout-123", context2)

        history = tracker.get_pause_history("rollout-123")

        assert len(history) == 2
        assert history[0].reason == "첫 번째 중지"
        assert history[1].reason == "두 번째 중지"

    def test_get_pause_history_empty(self):
        """존재하지 않는 롤아웃의 이력은 빈 리스트."""
        tracker = PauseReasonTracker()

        history = tracker.get_pause_history("nonexistent")

        assert history == []

    def test_get_latest_pause(self):
        """최신 PAUSE 컨텍스트 조회."""
        tracker = PauseReasonTracker()
        context1 = PauseContext(reason="첫 번째", triggered_by="interlock")
        context2 = PauseContext(reason="두 번째", triggered_by="manual")

        tracker.record_pause("rollout-123", context1)
        tracker.record_pause("rollout-123", context2)

        latest = tracker.get_latest_pause("rollout-123")

        assert latest is not None
        assert latest.reason == "두 번째"

    def test_get_latest_pause_none(self):
        """이력 없으면 None 반환."""
        tracker = PauseReasonTracker()

        latest = tracker.get_latest_pause("nonexistent")

        assert latest is None

    def test_clear(self):
        """특정 롤아웃 이력 삭제."""
        tracker = PauseReasonTracker()
        tracker.record_pause("rollout-1", PauseContext(reason="테스트", triggered_by="manual"))
        tracker.record_pause("rollout-2", PauseContext(reason="테스트", triggered_by="manual"))

        tracker.clear("rollout-1")

        assert tracker.get_pause_history("rollout-1") == []
        assert len(tracker.get_pause_history("rollout-2")) == 1

    def test_clear_all(self):
        """모든 이력 삭제."""
        tracker = PauseReasonTracker()
        tracker.record_pause("rollout-1", PauseContext(reason="테스트", triggered_by="manual"))
        tracker.record_pause("rollout-2", PauseContext(reason="테스트", triggered_by="manual"))

        tracker.clear_all()

        assert tracker.get_pause_history("rollout-1") == []
        assert tracker.get_pause_history("rollout-2") == []


# =============================================================================
# Test: PauseReasonTracker Singleton
# =============================================================================


class TestPauseReasonTrackerSingleton:
    """PauseReasonTracker 싱글톤 테스트."""

    def test_get_returns_same_instance(self):
        """get_pause_reason_tracker()가 동일 인스턴스 반환."""
        instance1 = get_pause_reason_tracker()
        instance2 = get_pause_reason_tracker()

        assert instance1 is instance2

    def test_reset_clears_singleton(self):
        """reset_pause_reason_tracker()가 싱글톤 초기화."""
        instance1 = get_pause_reason_tracker()
        reset_pause_reason_tracker()
        instance2 = get_pause_reason_tracker()

        assert instance1 is not instance2
