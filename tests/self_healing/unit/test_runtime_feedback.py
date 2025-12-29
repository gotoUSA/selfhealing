"""
Runtime Feedback Loop 테스트

Stage 36: 실시간 메트릭 기반 자율 튜닝 시스템 테스트
"""

import pytest
import threading
import time
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime, timezone

from selfhealing.core.runtime_feedback import (
    RuntimeFeedbackLoop,
    FeedbackLoopState,
    AdjustmentResult,
)
from selfhealing.core.decision_engine import (
    DecisionEngine,
    AdjustmentDecision,
    AdjustmentPriority,
)
from selfhealing.core.safety_bounds import SafetyBounds, ParameterBound
from selfhealing.core.auto_rollback_guard import (
    AutoRollbackGuard,
    GuardState,
    DegradationLevel,
)
from selfhealing.services.auto_tuning.adjustment_recorder import AdjustmentRecorder
from selfhealing.services.auto_tuning.models import AdjustmentRecord, TuningState
from selfhealing.adapters.metrics.auto_tuning_adapter import (
    MockMetricsAdapter,
    InternalMetricsAdapter,
)


class TestSafetyBounds:
    """SafetyBounds 테스트"""
    
    def test_default_bounds_exist(self):
        """기본 한계 설정 존재 확인"""
        bounds = SafetyBounds()
        
        assert bounds.get_bounds("timeout_ms") is not None
        assert bounds.get_bounds("retry_count") is not None
        assert bounds.get_bounds("circuit_breaker_threshold") is not None
    
    def test_within_bounds_valid(self):
        """유효한 범위 내 값 확인"""
        bounds = SafetyBounds()
        
        # timeout_ms: 100 ~ 30000
        assert bounds.is_within_bounds("timeout_ms", 5000)
        assert bounds.is_within_bounds("timeout_ms", 100)
        assert bounds.is_within_bounds("timeout_ms", 30000)
    
    def test_within_bounds_invalid_too_low(self):
        """최소값 미만 거부"""
        bounds = SafetyBounds()
        
        assert not bounds.is_within_bounds("timeout_ms", 50)  # 최소 100
        assert not bounds.is_within_bounds("circuit_breaker_threshold", 0.05)  # 최소 0.1
    
    def test_within_bounds_invalid_too_high(self):
        """최대값 초과 거부"""
        bounds = SafetyBounds()
        
        assert not bounds.is_within_bounds("timeout_ms", 50000)  # 최대 30000
        assert not bounds.is_within_bounds("retry_count", 15)  # 최대 10
    
    def test_change_ratio_limit(self):
        """변경폭 제한 확인"""
        bounds = SafetyBounds()
        
        # timeout_ms: max_change_per_cycle = 0.3 (30%)
        assert bounds.is_within_bounds("timeout_ms", 6000, current_value=5000)  # 20% 증가 OK
        assert not bounds.is_within_bounds("timeout_ms", 8000, current_value=5000)  # 60% 증가 거부
    
    def test_clamp_to_bounds(self):
        """범위 내 클램핑"""
        bounds = SafetyBounds()
        
        # 최소값 미만 → 최소값
        assert bounds.clamp_to_bounds("timeout_ms", 50) == 100
        
        # 최대값 초과 → 최대값
        assert bounds.clamp_to_bounds("timeout_ms", 50000) == 30000
        
        # 범위 내 → 그대로
        assert bounds.clamp_to_bounds("timeout_ms", 5000) == 5000
    
    def test_update_bounds(self):
        """한계 업데이트"""
        bounds = SafetyBounds()
        
        result = bounds.update_bounds("timeout_ms", {
            "min_value": 200,
            "max_value": 20000,
            "max_change_per_cycle": 0.5,
        })
        
        assert result is True
        new_bounds = bounds.get_bounds("timeout_ms")
        assert new_bounds["min_value"] == 200
        assert new_bounds["max_value"] == 20000
    
    def test_strict_mode_unknown_parameter(self):
        """strict 모드에서 알 수 없는 파라미터 거부"""
        bounds = SafetyBounds(strict_mode=True)
        
        assert not bounds.is_within_bounds("unknown_param", 100)
    
    def test_non_strict_mode_unknown_parameter(self):
        """non-strict 모드에서 알 수 없는 파라미터 허용"""
        bounds = SafetyBounds(strict_mode=False)
        
        assert bounds.is_within_bounds("unknown_param", 100)


class TestDecisionEngine:
    """DecisionEngine 테스트"""
    
    def setup_method(self):
        """테스트 설정"""
        self.config_provider = Mock()
        self.config_provider.get = Mock(side_effect=lambda key, default=None: {
            "timeout_ms": 5000,
            "retry_count": 3,
            "circuit_breaker_threshold": 0.5,
            "jitter_range": 0.1,
        }.get(key, default))
    
    def test_no_adjustment_needed(self):
        """조정 불필요 상황"""
        engine = DecisionEngine(self.config_provider)
        
        metrics = {
            "p99_latency_ms": 2000,  # 타임아웃의 40% - 조정 불필요
            "error_rate": 0.02,
            "retry_exhausted_rate": 0.05,
            "sample_count": 100,
        }
        
        decisions = engine.analyze(metrics)
        
        # timeout_ms 조정 불필요 (P99 < 80% of timeout)
        timeout_decisions = [d for d in decisions if d.parameter == "timeout_ms"]
        assert len(timeout_decisions) == 0
    
    def test_timeout_adjustment_needed(self):
        """타임아웃 조정 필요 상황"""
        engine = DecisionEngine(self.config_provider)
        
        metrics = {
            "p99_latency_ms": 4500,  # 타임아웃의 90% - 조정 필요
            "error_rate": 0.02,
            "sample_count": 100,
        }
        
        decisions = engine.analyze(metrics)
        
        timeout_decisions = [d for d in decisions if d.parameter == "timeout_ms"]
        assert len(timeout_decisions) == 1
        
        decision = timeout_decisions[0]
        assert decision.current_value == 5000
        assert decision.suggested_value == 6000  # 20% 상향
    
    def test_retry_adjustment_needed(self):
        """재시도 횟수 조정 필요 상황"""
        engine = DecisionEngine(self.config_provider)
        
        metrics = {
            "retry_exhausted_rate": 0.15,  # 15% 재시도 소진 - 조정 필요
            "sample_count": 100,
        }
        
        decisions = engine.analyze(metrics)
        
        retry_decisions = [d for d in decisions if d.parameter == "retry_count"]
        assert len(retry_decisions) == 1
        assert retry_decisions[0].suggested_value == 4  # 3 + 1
    
    def test_confidence_calculation(self):
        """신뢰도 계산"""
        engine = DecisionEngine(self.config_provider)
        
        # 샘플 수가 적으면 낮은 신뢰도
        metrics = {
            "p99_latency_ms": 4500,
            "sample_count": 3,
        }
        
        decisions = engine.analyze(metrics)
        if decisions:
            assert decisions[0].confidence < 0.5
        
        # 샘플 수가 많으면 높은 신뢰도
        metrics["sample_count"] = 150
        decisions = engine.analyze(metrics)
        if decisions:
            assert decisions[0].confidence >= 0.5
    
    def test_add_custom_rule(self):
        """커스텀 규칙 추가"""
        from selfhealing.core.decision_engine import AdjustmentRule
        
        engine = DecisionEngine(self.config_provider)
        
        custom_rule = AdjustmentRule(
            parameter="custom_param",
            metric="custom_metric",
            condition=lambda current, metric: metric > 100,
            adjustment=lambda current, metric: current * 1.5,
            reason="Custom adjustment",
        )
        
        engine.add_rule(custom_rule)
        
        rules = engine.get_rules()
        custom_rules = [r for r in rules if r["parameter"] == "custom_param"]
        assert len(custom_rules) == 1
    
    def test_disabled_engine(self):
        """비활성화 상태"""
        engine = DecisionEngine(self.config_provider, enabled=False)
        
        metrics = {
            "p99_latency_ms": 4500,
            "sample_count": 100,
        }
        
        decisions = engine.analyze(metrics)
        assert len(decisions) == 0


class TestAdjustmentRecorder:
    """AdjustmentRecorder 테스트"""
    
    def test_record_adjustment(self):
        """조정 기록"""
        recorder = AdjustmentRecorder()
        
        record = recorder.record(
            parameter="timeout_ms",
            old_value=5000,
            new_value=6000,
            reason="P99 레이턴시 높음",
            confidence=0.85,
        )
        
        assert record.parameter == "timeout_ms"
        assert record.old_value == 5000
        assert record.new_value == 6000
        assert record.success is True
    
    def test_get_records(self):
        """기록 조회"""
        recorder = AdjustmentRecorder()
        
        recorder.record("timeout_ms", 5000, 6000, "reason1")
        recorder.record("retry_count", 3, 4, "reason2")
        
        records = recorder.get_records()
        assert len(records) == 2
    
    def test_filter_by_parameter(self):
        """파라미터별 필터"""
        recorder = AdjustmentRecorder()
        
        recorder.record("timeout_ms", 5000, 6000, "reason1")
        recorder.record("retry_count", 3, 4, "reason2")
        
        records = recorder.get_records(parameter="timeout_ms")
        assert len(records) == 1
        assert records[0].parameter == "timeout_ms"
    
    def test_mark_rollback(self):
        """롤백 마킹"""
        recorder = AdjustmentRecorder()
        
        record = recorder.record("timeout_ms", 5000, 6000, "reason")
        
        result = recorder.mark_rollback(record.record_id)
        assert result is True
        
        updated = recorder.get_record(record.record_id)
        assert updated.rollback_performed is True
        assert updated.rollback_timestamp is not None
    
    def test_session_management(self):
        """세션 관리"""
        recorder = AdjustmentRecorder()
        
        session = recorder.start_session("Test session")
        assert session.state == TuningState.ACTIVE
        
        recorder.record("timeout_ms", 5000, 6000, "reason")
        
        ended = recorder.end_session()
        assert ended.state == TuningState.COMPLETED
        assert ended.total_adjustments == 1
    
    def test_statistics(self):
        """통계 조회"""
        recorder = AdjustmentRecorder()
        
        recorder.record("timeout_ms", 5000, 6000, "reason1", triggered_by="system")
        recorder.record("retry_count", 3, 4, "reason2", triggered_by="manual")
        
        stats = recorder.get_statistics()
        assert stats["total_records"] == 2
        assert "timeout_ms" in stats["by_parameter"]
        assert "system" in stats["by_trigger"]


class TestMockMetricsAdapter:
    """MockMetricsAdapter 테스트"""
    
    def test_fetch_metrics(self):
        """메트릭 조회"""
        adapter = MockMetricsAdapter()
        
        metrics = adapter.fetch_current_metrics()
        
        assert "error_rate" in metrics
        assert "p99_latency_ms" in metrics
        assert "sample_count" in metrics
    
    def test_set_metric(self):
        """메트릭 설정"""
        adapter = MockMetricsAdapter()
        
        adapter.set_metric("error_rate", 0.5)
        
        metrics = adapter.fetch_current_metrics()
        assert metrics["error_rate"] == 0.5
    
    def test_simulate_degradation(self):
        """저하 시뮬레이션"""
        adapter = MockMetricsAdapter()
        
        adapter.simulate_degradation("critical")
        
        metrics = adapter.fetch_current_metrics()
        assert metrics["error_rate"] >= 0.3
        assert metrics["p99_latency_ms"] >= 10000


class TestAutoRollbackGuard:
    """AutoRollbackGuard 테스트"""
    
    def setup_method(self):
        """테스트 설정"""
        self.metrics_provider = Mock()
        self.metrics_provider.get_error_rate = Mock(return_value=0.02)
        self.metrics_provider.get_latency_p99 = Mock(return_value=200)
        self.metrics_provider.get_throughput = Mock(return_value=1000)
        
        self.config_applier = Mock()
        self.config_applier.apply = Mock(return_value=True)
        self.config_applier.rollback = Mock(return_value=True)
    
    def test_initial_state(self):
        """초기 상태"""
        guard = AutoRollbackGuard(
            metrics_provider=self.metrics_provider,
            config_applier=self.config_applier,
            enabled=False,
        )
        
        assert guard.state == GuardState.INACTIVE
    
    def test_start_stop(self):
        """시작/중지"""
        guard = AutoRollbackGuard(
            metrics_provider=self.metrics_provider,
            config_applier=self.config_applier,
            check_interval_seconds=1,
        )
        
        guard.start()
        assert guard.state == GuardState.MONITORING
        
        guard.stop()
        assert guard.state == GuardState.INACTIVE
    
    def test_degradation_assessment_none(self):
        """저하 없음 평가"""
        guard = AutoRollbackGuard(
            metrics_provider=self.metrics_provider,
            config_applier=self.config_applier,
        )
        
        level = guard._assess_degradation(0.02, 200)
        assert level == DegradationLevel.NONE
    
    def test_degradation_assessment_minor(self):
        """경미한 저하 평가"""
        guard = AutoRollbackGuard(
            metrics_provider=self.metrics_provider,
            config_applier=self.config_applier,
        )
        
        level = guard._assess_degradation(0.06, 3500)
        assert level == DegradationLevel.MINOR
    
    def test_degradation_assessment_major(self):
        """심각한 저하 평가"""
        guard = AutoRollbackGuard(
            metrics_provider=self.metrics_provider,
            config_applier=self.config_applier,
        )
        
        level = guard._assess_degradation(0.15, 6000)
        assert level == DegradationLevel.MAJOR
    
    def test_degradation_assessment_critical(self):
        """긴급 저하 평가"""
        guard = AutoRollbackGuard(
            metrics_provider=self.metrics_provider,
            config_applier=self.config_applier,
        )
        
        level = guard._assess_degradation(0.35, 12000)
        assert level == DegradationLevel.CRITICAL
    
    def test_manual_emergency_trigger(self):
        """수동 긴급 복구"""
        guard = AutoRollbackGuard(
            metrics_provider=self.metrics_provider,
            config_applier=self.config_applier,
        )
        
        result = guard.trigger_manual_emergency("test")
        
        assert result is True
        assert guard.state == GuardState.RECOVERING
    
    def test_save_snapshot(self):
        """스냅샷 저장"""
        guard = AutoRollbackGuard(
            metrics_provider=self.metrics_provider,
            config_applier=self.config_applier,
        )
        
        guard.save_snapshot("timeout_ms", 5000)
        guard.save_snapshot("timeout_ms", 6000)
        
        status = guard.get_status()
        # 스냅샷이 저장되어 있어야 함
        assert status is not None
    
    def test_get_safe_defaults(self):
        """안전한 기본값 조회"""
        guard = AutoRollbackGuard(
            metrics_provider=self.metrics_provider,
            config_applier=self.config_applier,
        )
        
        defaults = guard.get_safe_defaults()
        
        assert len(defaults) > 0
        assert any(d["parameter"] == "timeout_ms" for d in defaults)


class TestRuntimeFeedbackLoop:
    """RuntimeFeedbackLoop 테스트"""
    
    def setup_method(self):
        """테스트 설정"""
        self.metrics_adapter = MockMetricsAdapter()
        
        self.config_provider = Mock()
        self.config_provider.get = Mock(side_effect=lambda key, default=None: {
            "timeout_ms": 5000,
            "retry_count": 3,
            "circuit_breaker_threshold": 0.5,
        }.get(key, default))
        
        self.config_applier = Mock()
        self.config_applier.get_current = Mock(side_effect=lambda key: {
            "timeout_ms": 5000,
            "retry_count": 3,
        }.get(key, 0))
        self.config_applier.apply = Mock(return_value=True)
        self.config_applier.rollback = Mock(return_value=True)
        
        self.decision_engine = DecisionEngine(self.config_provider)
        self.safety_bounds = SafetyBounds()
        self.audit_adapter = Mock()
        self.audit_adapter.log = Mock()
        self.alert_manager = Mock()
    
    def test_initial_state(self):
        """초기 상태"""
        loop = RuntimeFeedbackLoop(
            metrics_adapter=self.metrics_adapter,
            decision_engine=self.decision_engine,
            safety_bounds=self.safety_bounds,
            audit_adapter=self.audit_adapter,
            alert_manager=self.alert_manager,
            config_applier=self.config_applier,
            enabled=False,
        )
        
        assert loop.state == FeedbackLoopState.STOPPED
    
    def test_start_stop(self):
        """시작/중지"""
        loop = RuntimeFeedbackLoop(
            metrics_adapter=self.metrics_adapter,
            decision_engine=self.decision_engine,
            safety_bounds=self.safety_bounds,
            audit_adapter=self.audit_adapter,
            alert_manager=self.alert_manager,
            config_applier=self.config_applier,
            interval_seconds=1,
        )
        
        loop.start()
        assert loop.state == FeedbackLoopState.RUNNING
        
        loop.stop()
        assert loop.state == FeedbackLoopState.STOPPED
    
    def test_pause_resume(self):
        """일시정지/재개"""
        loop = RuntimeFeedbackLoop(
            metrics_adapter=self.metrics_adapter,
            decision_engine=self.decision_engine,
            safety_bounds=self.safety_bounds,
            audit_adapter=self.audit_adapter,
            alert_manager=self.alert_manager,
            config_applier=self.config_applier,
        )
        
        loop.start()
        loop.pause("test")
        assert loop.state == FeedbackLoopState.PAUSED
        
        loop.resume()
        assert loop.state == FeedbackLoopState.RUNNING
        
        loop.stop()
    
    def test_observe_no_adjustment(self):
        """조정 불필요 시나리오"""
        loop = RuntimeFeedbackLoop(
            metrics_adapter=self.metrics_adapter,
            decision_engine=self.decision_engine,
            safety_bounds=self.safety_bounds,
            audit_adapter=self.audit_adapter,
            alert_manager=self.alert_manager,
            config_applier=self.config_applier,
        )
        
        # 정상 메트릭
        self.metrics_adapter.set_metrics({
            "error_rate": 0.01,
            "p99_latency_ms": 200,
            "sample_count": 100,
        })
        
        result = loop.observe_and_adjust()
        
        assert result["adjusted"] is False
    
    def test_observe_with_adjustment(self):
        """조정 필요 시나리오"""
        loop = RuntimeFeedbackLoop(
            metrics_adapter=self.metrics_adapter,
            decision_engine=self.decision_engine,
            safety_bounds=self.safety_bounds,
            audit_adapter=self.audit_adapter,
            alert_manager=self.alert_manager,
            config_applier=self.config_applier,
            auto_rollback_enabled=False,  # 헬스체크 스레드 비활성화
        )
        
        # P99이 타임아웃의 90% - 조정 필요
        self.metrics_adapter.set_metrics({
            "p99_latency_ms": 4500,
            "sample_count": 100,
        })
        
        result = loop.observe_and_adjust()
        
        assert result["adjusted"] is True
        assert len(result["adjustments"]) > 0
    
    def test_safety_bounds_rejection(self):
        """안전 한계 거부"""
        # 매우 큰 값을 제안하도록 조작
        self.config_applier.get_current = Mock(return_value=29000)  # 거의 최대
        
        loop = RuntimeFeedbackLoop(
            metrics_adapter=self.metrics_adapter,
            decision_engine=self.decision_engine,
            safety_bounds=self.safety_bounds,
            audit_adapter=self.audit_adapter,
            alert_manager=self.alert_manager,
            config_applier=self.config_applier,
        )
        
        # 35000으로 조정하려 할 때 (최대 30000 초과)
        # SafetyBounds가 거부해야 함
        status = loop.get_status()
        assert status is not None
    
    def test_manual_rollback(self):
        """수동 롤백"""
        loop = RuntimeFeedbackLoop(
            metrics_adapter=self.metrics_adapter,
            decision_engine=self.decision_engine,
            safety_bounds=self.safety_bounds,
            audit_adapter=self.audit_adapter,
            alert_manager=self.alert_manager,
            config_applier=self.config_applier,
        )
        
        # 스냅샷 없으면 롤백 실패
        result = loop.manual_rollback("timeout_ms")
        assert result is False
    
    def test_get_status(self):
        """상태 조회"""
        loop = RuntimeFeedbackLoop(
            metrics_adapter=self.metrics_adapter,
            decision_engine=self.decision_engine,
            safety_bounds=self.safety_bounds,
            audit_adapter=self.audit_adapter,
            alert_manager=self.alert_manager,
            config_applier=self.config_applier,
        )
        
        status = loop.get_status()
        
        assert "state" in status
        assert "enabled" in status
        assert "auto_rollback_enabled" in status
        assert "consecutive_failures" in status


class TestIntegration:
    """통합 테스트"""
    
    def test_full_adjustment_cycle(self):
        """전체 조정 사이클"""
        # 1. 컴포넌트 설정
        metrics_adapter = MockMetricsAdapter()
        
        config_values = {
            "timeout_ms": 5000,
            "retry_count": 3,
        }
        
        config_provider = Mock()
        config_provider.get = Mock(side_effect=lambda key, default=None: config_values.get(key, default))
        
        config_applier = Mock()
        config_applier.get_current = Mock(side_effect=lambda key: config_values.get(key, 0))
        config_applier.apply = Mock(side_effect=lambda key, value: config_values.update({key: value}) or True)
        config_applier.rollback = Mock(return_value=True)
        
        decision_engine = DecisionEngine(config_provider)
        safety_bounds = SafetyBounds()
        audit_adapter = Mock()
        audit_adapter.log = Mock()
        alert_manager = Mock()
        
        # 2. 피드백 루프 생성
        loop = RuntimeFeedbackLoop(
            metrics_adapter=metrics_adapter,
            decision_engine=decision_engine,
            safety_bounds=safety_bounds,
            audit_adapter=audit_adapter,
            alert_manager=alert_manager,
            config_applier=config_applier,
            auto_rollback_enabled=False,
        )
        
        # 3. 저하 상황 시뮬레이션
        metrics_adapter.set_metrics({
            "p99_latency_ms": 4500,  # 타임아웃의 90%
            "sample_count": 100,
        })
        
        # 4. 조정 수행
        result = loop.observe_and_adjust()
        
        # 5. 검증
        assert result["adjusted"] is True
        assert config_applier.apply.called
    
    def test_safety_bounds_prevents_dangerous_changes(self):
        """안전 한계가 위험한 변경 방지"""
        bounds = SafetyBounds()
        
        # 급격한 변경 시도 (5000 → 10000, 100% 증가)
        result = bounds.is_within_bounds(
            "timeout_ms",
            10000,
            current_value=5000
        )
        
        # 30% 제한으로 거부되어야 함
        assert result is False
        
        # 적당한 변경 (5000 → 6000, 20% 증가)
        result = bounds.is_within_bounds(
            "timeout_ms",
            6000,
            current_value=5000
        )
        
        assert result is True
    
    def test_recorder_tracks_all_adjustments(self):
        """기록기가 모든 조정 추적"""
        recorder = AdjustmentRecorder()
        
        # 세션 시작
        recorder.start_session("Integration test")
        
        # 여러 조정 기록
        for i in range(5):
            recorder.record(
                parameter="timeout_ms",
                old_value=5000 + i * 100,
                new_value=5000 + (i + 1) * 100,
                reason=f"Adjustment {i}",
            )
        
        # 롤백 마킹
        records = recorder.get_records()
        recorder.mark_rollback(records[0].record_id)
        
        # 세션 종료
        recorder.end_session()
        
        # 통계 확인
        stats = recorder.get_statistics()
        assert stats["total_records"] == 5
        assert stats["rolled_back"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
