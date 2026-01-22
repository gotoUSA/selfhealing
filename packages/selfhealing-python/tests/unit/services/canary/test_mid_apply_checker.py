"""
Phase 3: MidApplyCheckResult, MidApplyInterlockChecker 단위 테스트.

적용 중 인터락 체크 기능 테스트.

Reference: docs/self_healing/middleware_system/74_CANARY_SAFETY_INTERLOCK.md
"""

import pytest
from unittest.mock import MagicMock

from selfhealing.services.canary.mid_apply_checker import (
    MidApplyCheckResult,
    MidApplyInterlockChecker,
)
from selfhealing.services.canary.interlock import (
    InterlockAction,
    InterlockResult,
    CanarySafetyInterlock,
)
from selfhealing.services.emergency_mode.enums import EmergencyLevel


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_tracker():
    """Mock NamespacedEmergencyTracker."""
    tracker = MagicMock()
    mock_state = MagicMock()
    mock_state.emergency_level = EmergencyLevel.NORMAL
    mock_state.namespace = "test-namespace"
    tracker.get_effective_state.return_value = mock_state
    return tracker


def create_mock_state(level: EmergencyLevel, namespace: str = "test-namespace"):
    """Mock ScopedEmergencyState 생성 헬퍼."""
    mock_state = MagicMock()
    mock_state.emergency_level = level
    mock_state.namespace = namespace
    return mock_state


# =============================================================================
# Test: MidApplyCheckResult
# =============================================================================


class TestMidApplyCheckResult:
    """MidApplyCheckResult 테스트."""

    def test_create_success_result(self):
        """성공 결과 생성."""
        result = MidApplyCheckResult(
            should_continue=True,
            interlock_triggered=False,
            applied_clusters=["cluster-1", "cluster-2"],
            remaining_clusters=[],
            rollback_required=False,
        )
        
        assert result.should_continue is True
        assert result.interlock_triggered is False
        assert len(result.applied_clusters) == 2
        assert result.rollback_required is False

    def test_create_interrupted_result(self):
        """중단 결과 생성."""
        interlock_result = InterlockResult.pause(
            emergency_level=2,
            emergency_level_name="LEVEL_2",
            namespace="test",
        )
        
        result = MidApplyCheckResult(
            should_continue=False,
            interlock_triggered=True,
            applied_clusters=["cluster-1"],
            remaining_clusters=["cluster-2", "cluster-3"],
            rollback_required=True,
            interlock_result=interlock_result,
        )
        
        assert result.should_continue is False
        assert result.interlock_triggered is True
        assert result.rollback_required is True
        assert result.interlock_result.action == InterlockAction.PAUSE

    def test_to_dict(self):
        """딕셔너리 변환."""
        result = MidApplyCheckResult(
            should_continue=False,
            interlock_triggered=True,
            applied_clusters=["cluster-1"],
            remaining_clusters=["cluster-2"],
            rollback_required=True,
            interlock_result=InterlockResult.rollback(
                emergency_level=3,
                emergency_level_name="LEVEL_3",
                namespace="test",
            ),
        )
        
        data = result.to_dict()
        
        assert data["should_continue"] is False
        assert data["interlock_triggered"] is True
        assert data["interlock_result"]["action"] == "rollback"


# =============================================================================
# Test: MidApplyInterlockChecker
# =============================================================================


class TestMidApplyInterlockChecker:
    """MidApplyInterlockChecker 테스트."""

    def test_all_clusters_applied_successfully(self, mock_tracker):
        """모든 클러스터 성공적으로 적용."""
        mock_tracker.get_effective_state.return_value = create_mock_state(
            EmergencyLevel.NORMAL
        )
        
        interlock = CanarySafetyInterlock(
            emergency_tracker_factory=lambda: mock_tracker
        )
        checker = MidApplyInterlockChecker(safety_interlock=interlock)
        
        apply_fn = MagicMock(return_value=True)
        
        result = checker.apply_with_check(
            rollout_id="rollout-1",
            target_clusters=["cluster-1", "cluster-2", "cluster-3"],
            apply_fn=apply_fn,
        )
        
        assert result.should_continue is True
        assert result.interlock_triggered is False
        assert len(result.applied_clusters) == 3
        assert len(result.remaining_clusters) == 0
        assert apply_fn.call_count == 3

    def test_interlock_triggered_mid_apply(self, mock_tracker):
        """적용 중 인터락 발동."""
        # 처음엔 NORMAL, 두 번째 체크에서 LEVEL_3
        call_count = 0
        def mock_get_state(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return create_mock_state(EmergencyLevel.NORMAL)
            return create_mock_state(EmergencyLevel.LEVEL_3)
        
        mock_tracker.get_effective_state.side_effect = mock_get_state
        
        interlock = CanarySafetyInterlock(
            emergency_tracker_factory=lambda: mock_tracker
        )
        checker = MidApplyInterlockChecker(safety_interlock=interlock)
        
        apply_fn = MagicMock(return_value=True)
        rollback_fn = MagicMock(return_value=True)
        
        result = checker.apply_with_check(
            rollout_id="rollout-1",
            target_clusters=["cluster-1", "cluster-2", "cluster-3"],
            apply_fn=apply_fn,
            rollback_fn=rollback_fn,
        )
        
        # 첫 번째 클러스터만 적용됨
        assert result.should_continue is False
        assert result.interlock_triggered is True
        assert len(result.applied_clusters) == 1
        assert "cluster-2" in result.remaining_clusters
        assert result.rollback_required is True
        
        # 롤백 호출됨
        rollback_fn.assert_called_once_with("cluster-1")

    def test_check_interval_respected(self, mock_tracker):
        """check_interval 설정 확인."""
        mock_tracker.get_effective_state.return_value = create_mock_state(
            EmergencyLevel.NORMAL
        )
        
        interlock = CanarySafetyInterlock(
            emergency_tracker_factory=lambda: mock_tracker
        )
        # 2개마다 체크
        checker = MidApplyInterlockChecker(
            safety_interlock=interlock,
            check_interval=2,
        )
        
        apply_fn = MagicMock(return_value=True)
        
        result = checker.apply_with_check(
            rollout_id="rollout-1",
            target_clusters=["c1", "c2", "c3", "c4"],
            apply_fn=apply_fn,
        )
        
        # get_effective_state는 0, 2번 인덱스에서만 호출 (2회)
        assert mock_tracker.get_effective_state.call_count == 2

    def test_no_rollback_when_no_applied_clusters(self, mock_tracker):
        """적용된 클러스터 없으면 롤백 불필요."""
        mock_tracker.get_effective_state.return_value = create_mock_state(
            EmergencyLevel.LEVEL_3
        )
        
        interlock = CanarySafetyInterlock(
            emergency_tracker_factory=lambda: mock_tracker
        )
        checker = MidApplyInterlockChecker(safety_interlock=interlock)
        
        apply_fn = MagicMock(return_value=True)
        rollback_fn = MagicMock(return_value=True)
        
        result = checker.apply_with_check(
            rollout_id="rollout-1",
            target_clusters=["cluster-1"],
            apply_fn=apply_fn,
            rollback_fn=rollback_fn,
        )
        
        # 첫 체크에서 바로 인터락 발동
        assert result.interlock_triggered is True
        assert len(result.applied_clusters) == 0
        assert result.rollback_required is False
        rollback_fn.assert_not_called()
