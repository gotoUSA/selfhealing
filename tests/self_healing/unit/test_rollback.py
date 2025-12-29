"""
Phase 1 DNA Safety 단위 테스트 - Rollback DNA

Reference: docs/self_healing/33_DNA_SAFETY_FEATURES.md
"""
import pytest
from datetime import datetime
from load_tests.utils.selfhealing.dna_safety import (
    RollbackController,
    RollbackStrategy,
    RollbackTarget,
    RollbackResult,
    StateSnapshot,
)


class TestRollbackStrategy:
    """RollbackStrategy Enum 테스트"""
    
    def test_strategy_values(self):
        """전략 값 확인"""
        assert RollbackStrategy.AUTOMATIC.value == "automatic"
        assert RollbackStrategy.MANUAL.value == "manual"
        assert RollbackStrategy.HYBRID.value == "hybrid"


class TestRollbackTarget:
    """RollbackTarget Enum 테스트"""
    
    def test_target_values(self):
        """타겟 값 확인"""
        assert RollbackTarget.CONFIG.value == "config"
        assert RollbackTarget.FEATURE_FLAGS.value == "feature_flags"
        assert RollbackTarget.RATE_LIMITS.value == "rate_limits"
        assert RollbackTarget.CIRCUIT_BREAKERS.value == "circuit_breakers"
        assert RollbackTarget.ALL.value == "all"


class TestStateSnapshot:
    """StateSnapshot 데이터클래스 테스트"""
    
    def test_snapshot_creation(self):
        """스냅샷 생성"""
        snapshot = StateSnapshot(
            snapshot_id="snap_001",
            created_at="2025-12-29T12:00:00",
            state={"config": {"key": "value"}},
            metadata={"source": "test"},
        )
        
        assert snapshot.snapshot_id == "snap_001"
        assert snapshot.state["config"]["key"] == "value"
        assert snapshot.metadata["source"] == "test"
    
    def test_snapshot_to_dict(self):
        """스냅샷 딕셔너리 변환"""
        snapshot = StateSnapshot(
            snapshot_id="snap_002",
            created_at="2025-12-29T12:00:00",
            state={"config": {}, "cache": {}},
        )
        
        result = snapshot.to_dict()
        
        assert result["snapshot_id"] == "snap_002"
        assert "config" in result["state_keys"]
        assert "cache" in result["state_keys"]


class TestRollbackResult:
    """RollbackResult 데이터클래스 테스트"""
    
    def test_result_creation(self):
        """결과 생성"""
        result = RollbackResult(
            success=True,
            rollback_id="rb_001",
            triggered_at="2025-12-29T12:00:00",
            completed_at="2025-12-29T12:00:05",
            trigger_reason="Error rate exceeded",
            targets_rolled_back=["config"],
            snapshot_used="snap_001",
        )
        
        assert result.success
        assert result.rollback_id == "rb_001"
        assert "config" in result.targets_rolled_back
    
    def test_result_duration_calculation(self):
        """duration 계산"""
        result = RollbackResult(
            success=True,
            rollback_id="rb_002",
            triggered_at="2025-12-29T12:00:00",
            completed_at="2025-12-29T12:00:10",
            trigger_reason="Test",
            targets_rolled_back=[],
            snapshot_used=None,
        )
        
        assert result.duration_seconds == 10.0
    
    def test_result_duration_none_when_not_completed(self):
        """미완료 시 duration None"""
        result = RollbackResult(
            success=False,
            rollback_id="rb_003",
            triggered_at="2025-12-29T12:00:00",
            completed_at=None,
            trigger_reason="Test",
            targets_rolled_back=[],
            snapshot_used=None,
        )
        
        assert result.duration_seconds is None


class TestRollbackControllerInit:
    """RollbackController 초기화 테스트"""
    
    def test_default_config(self):
        """기본 설정으로 초기화"""
        controller = RollbackController({})
        
        assert controller.strategy == RollbackStrategy.AUTOMATIC
        assert controller.timeout == 120
        assert controller.observation_window == 30
    
    def test_custom_config(self):
        """커스텀 설정으로 초기화"""
        config = {
            "strategy": "hybrid",
            "timeout_seconds": 60,
            "observation_window_seconds": 15,
            "triggers": {
                "error_rate_threshold": 0.05,
            },
        }
        
        controller = RollbackController(config)
        
        assert controller.strategy == RollbackStrategy.HYBRID
        assert controller.timeout == 60
        assert controller.triggers["error_rate_threshold"] == 0.05
    
    def test_manual_strategy(self):
        """수동 전략 설정"""
        config = {"strategy": "manual"}
        controller = RollbackController(config)
        
        assert controller.strategy == RollbackStrategy.MANUAL


class TestRollbackControllerSnapshot:
    """RollbackController 스냅샷 테스트"""
    
    def test_take_snapshot(self):
        """스냅샷 생성"""
        controller = RollbackController({"snapshot": {"enabled": True}})
        
        state = {"config": {"key": "value"}}
        snapshot = controller.take_snapshot(state, force=True)
        
        assert snapshot is not None
        assert "snap_" in snapshot.snapshot_id
        assert snapshot.state["config"]["key"] == "value"
    
    def test_snapshot_disabled(self):
        """스냅샷 비활성화"""
        controller = RollbackController({"snapshot": {"enabled": False}})
        
        snapshot = controller.take_snapshot({}, force=True)
        
        assert snapshot is None
    
    def test_get_latest_snapshot(self):
        """최신 스냅샷 조회"""
        controller = RollbackController({"snapshot": {"enabled": True}})
        
        controller.take_snapshot({"v": 1}, force=True)
        controller.take_snapshot({"v": 2}, force=True)
        
        latest = controller.get_latest_snapshot()
        
        assert latest.state["v"] == 2
    
    def test_get_stable_snapshot(self):
        """안정 스냅샷 조회 (offset)"""
        controller = RollbackController({"snapshot": {"enabled": True}})
        
        controller.take_snapshot({"v": 1}, force=True)
        controller.take_snapshot({"v": 2}, force=True)
        controller.take_snapshot({"v": 3}, force=True)
        
        stable = controller.get_stable_snapshot(offset=1)
        
        assert stable.state["v"] == 2
    
    def test_max_snapshots_limit(self):
        """스냅샷 최대 개수 제한"""
        controller = RollbackController({
            "snapshot": {"enabled": True, "max_snapshots": 3}
        })
        
        for i in range(5):
            controller.take_snapshot({"v": i}, force=True)
        
        # 최대 3개만 유지
        assert len(controller.snapshots) == 3
        # 가장 오래된 것은 삭제됨
        assert controller.snapshots[0].state["v"] == 2


class TestRollbackControllerTriggerConditions:
    """RollbackController 트리거 조건 테스트"""
    
    def test_error_rate_trigger(self):
        """에러율 트리거"""
        controller = RollbackController({
            "triggers": {"error_rate_threshold": 0.1}
        })
        
        # 임계값 초과
        should_trigger, reason = controller.check_trigger_conditions({
            "error_rate": 0.15
        })
        
        assert should_trigger
        assert "Error rate" in reason
    
    def test_error_rate_no_trigger(self):
        """에러율 임계값 미만 - 트리거 안됨"""
        controller = RollbackController({
            "triggers": {"error_rate_threshold": 0.1}
        })
        
        should_trigger, _ = controller.check_trigger_conditions({
            "error_rate": 0.05
        })
        
        assert not should_trigger
    
    def test_latency_spike_trigger(self):
        """레이턴시 스파이크 트리거"""
        controller = RollbackController({
            "triggers": {"latency_spike_ratio": 3.0}
        })
        
        should_trigger, reason = controller.check_trigger_conditions({
            "baseline_p99": 100,
            "current_p99": 400,  # 4x spike
        })
        
        assert should_trigger
        assert "Latency spike" in reason
    
    def test_consecutive_failures_trigger(self):
        """연속 실패 트리거"""
        controller = RollbackController({
            "triggers": {"consecutive_failures": 5}
        })
        
        should_trigger, reason = controller.check_trigger_conditions({
            "consecutive_failures": 7
        })
        
        assert should_trigger
        assert "Consecutive failures" in reason
    
    def test_health_check_failures_trigger(self):
        """헬스체크 실패 트리거"""
        controller = RollbackController({
            "triggers": {"health_check_failures": 3}
        })
        
        should_trigger, reason = controller.check_trigger_conditions({
            "health_check_failures": 5
        })
        
        assert should_trigger
        assert "Health check" in reason
    
    def test_no_trigger_condition_met(self):
        """트리거 조건 없음"""
        controller = RollbackController({
            "triggers": {
                "error_rate_threshold": 0.1,
                "consecutive_failures": 5,
            }
        })
        
        should_trigger, reason = controller.check_trigger_conditions({
            "error_rate": 0.05,
            "consecutive_failures": 2,
        })
        
        assert not should_trigger
        assert "No trigger condition met" in reason


class TestRollbackControllerRollback:
    """RollbackController 롤백 실행 테스트"""
    
    def test_rollback_success(self):
        """롤백 성공"""
        controller = RollbackController({
            "snapshot": {"enabled": True},
            "targets": ["config"],
        })
        
        # 스냅샷 생성
        controller.take_snapshot({"config": {"old": True}}, force=True)
        controller.take_snapshot({"config": {"new": True}}, force=True)
        
        # 롤백 실행
        result = controller.rollback(reason="Test rollback")
        
        assert result.success
        assert "config" in result.targets_rolled_back
        assert result.snapshot_used is not None
    
    def test_rollback_no_snapshot(self):
        """스냅샷 없이 롤백 시도"""
        controller = RollbackController({"snapshot": {"enabled": True}})
        
        result = controller.rollback(reason="No snapshot test")
        
        assert not result.success
        assert "No snapshot available" in result.errors[0]
    
    def test_rollback_with_apply_fn(self):
        """커스텀 apply_fn으로 롤백"""
        controller = RollbackController({
            "snapshot": {"enabled": True},
            "targets": ["config"],
        })
        
        applied_states = []
        
        def apply_fn(state):
            applied_states.append(state)
            return True
        
        controller.take_snapshot({"config": {"v": 1}}, force=True)
        controller.take_snapshot({"config": {"v": 2}}, force=True)
        
        result = controller.rollback(reason="Test", apply_fn=apply_fn)
        
        assert result.success
        assert len(applied_states) == 1
        assert applied_states[0]["config"]["v"] == 1
    
    def test_rollback_already_in_progress(self):
        """이미 롤백 중일 때 중복 방지"""
        controller = RollbackController({
            "snapshot": {"enabled": True},
            "targets": ["config"],
        })
        
        controller.take_snapshot({"config": {}}, force=True)
        
        # 롤백 중 상태로 설정
        controller._is_rolling_back = True
        
        result = controller.rollback(reason="Duplicate test")
        
        assert not result.success
        assert "already in progress" in result.errors[0]


class TestRollbackControllerAutoRollback:
    """RollbackController 자동 롤백 테스트"""
    
    def test_auto_rollback_automatic_strategy(self):
        """자동 전략 - 조건 충족 시 롤백"""
        controller = RollbackController({
            "strategy": "automatic",
            "snapshot": {"enabled": True},
            "triggers": {"error_rate_threshold": 0.1},
            "targets": ["config"],
        })
        
        controller.take_snapshot({"config": {"stable": True}}, force=True)
        controller.take_snapshot({"config": {"unstable": True}}, force=True)
        
        result = controller.auto_rollback_check({
            "error_rate": 0.15  # 임계값 초과
        })
        
        assert result is not None
        assert result.success
    
    def test_auto_rollback_manual_strategy_skips(self):
        """수동 전략 - 자동 롤백 스킵"""
        controller = RollbackController({
            "strategy": "manual",
            "triggers": {"error_rate_threshold": 0.1},
        })
        
        result = controller.auto_rollback_check({
            "error_rate": 0.15
        })
        
        assert result is None
    
    def test_auto_rollback_no_trigger(self):
        """조건 미충족 - 롤백 안함"""
        controller = RollbackController({
            "strategy": "automatic",
            "triggers": {"error_rate_threshold": 0.1},
        })
        
        result = controller.auto_rollback_check({
            "error_rate": 0.05  # 임계값 미만
        })
        
        assert result is None


class TestRollbackControllerSummary:
    """RollbackController 요약 테스트"""
    
    def test_get_rollback_summary(self):
        """롤백 요약 조회"""
        controller = RollbackController({
            "strategy": "automatic",
            "snapshot": {"enabled": True},
            "triggers": {"error_rate_threshold": 0.1},
            "targets": ["config"],
        })
        
        controller.take_snapshot({"config": {}}, force=True)
        
        summary = controller.get_rollback_summary()
        
        assert summary["strategy"] == "automatic"
        assert summary["total_snapshots"] == 1
        assert summary["total_rollbacks"] == 0
        assert summary["is_rolling_back"] is False
        assert "triggers" in summary
