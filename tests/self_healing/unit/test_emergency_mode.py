"""
Emergency Mode Tests.

비상 모드 고급 기능 (EmergencyLevel, GracefulDegradationManager, RecoveryGate)
단위 테스트 및 통합 테스트.

Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1A.md (Section 4)
"""

import pytest
import time
import threading
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from selfhealing.services.emergency_mode import (
    EmergencyLevel,
    EmergencyState,
    RecoveryGateConfig,
    RecoveryGate,
    GracefulDegradationManager,
    EMERGENCY_LEVEL_RULES,
    get_emergency_manager,
    is_emergency_active,
    get_emergency_level,
    get_tier_multiplier,
)


# =============================================================================
# EmergencyLevel Tests
# =============================================================================


class TestEmergencyLevel:
    """EmergencyLevel Enum 테스트."""
    
    def test_emergency_levels_defined(self):
        """모든 비상 레벨이 정의되어 있어야 함."""
        assert EmergencyLevel.NORMAL.value == 0
        assert EmergencyLevel.LEVEL_1.value == 1
        assert EmergencyLevel.LEVEL_2.value == 2
        assert EmergencyLevel.LEVEL_3.value == 3
    
    def test_emergency_level_ordering(self):
        """비상 레벨은 값 순서가 있어야 함."""
        # Enum 값 기반 비교 사용
        assert EmergencyLevel.NORMAL.value < EmergencyLevel.LEVEL_1.value
        assert EmergencyLevel.LEVEL_1.value < EmergencyLevel.LEVEL_2.value
        assert EmergencyLevel.LEVEL_2.value < EmergencyLevel.LEVEL_3.value
    
    def test_all_levels_have_rules(self):
        """모든 레벨에 대한 규칙이 정의되어 있어야 함."""
        for level in EmergencyLevel:
            assert level in EMERGENCY_LEVEL_RULES
            rules = EMERGENCY_LEVEL_RULES[level]
            assert "critical" in rules
            assert "standard" in rules
            assert "non_essential" in rules


class TestEmergencyLevelRules:
    """비상 레벨별 규칙 테스트."""
    
    def test_normal_level_allows_all(self):
        """NORMAL 레벨은 모든 트래픽을 허용해야 함."""
        rules = EMERGENCY_LEVEL_RULES[EmergencyLevel.NORMAL]
        assert rules["critical"] == 1.0
        assert rules["standard"] == 1.0
        assert rules["non_essential"] == 1.0
    
    def test_level_1_blocks_non_essential(self):
        """LEVEL_1은 non_essential만 차단해야 함."""
        rules = EMERGENCY_LEVEL_RULES[EmergencyLevel.LEVEL_1]
        assert rules["critical"] == 1.0
        assert rules["standard"] == 1.0
        assert rules["non_essential"] == 0.0
    
    def test_level_2_limits_standard(self):
        """LEVEL_2는 standard를 10%로 제한해야 함."""
        rules = EMERGENCY_LEVEL_RULES[EmergencyLevel.LEVEL_2]
        assert rules["critical"] == 1.0
        assert rules["standard"] == 0.1
        assert rules["non_essential"] == 0.0
    
    def test_level_3_limits_critical(self):
        """LEVEL_3은 critical도 50%로 제한해야 함."""
        rules = EMERGENCY_LEVEL_RULES[EmergencyLevel.LEVEL_3]
        assert rules["critical"] == 0.5
        assert rules["standard"] == 0.0
        assert rules["non_essential"] == 0.0
    
    def test_higher_level_more_restrictive(self):
        """더 높은 레벨일수록 더 제한적이어야 함."""
        for i, level in enumerate(EmergencyLevel):
            if i == 0:
                continue
            prev_level = list(EmergencyLevel)[i - 1]
            
            current_rules = EMERGENCY_LEVEL_RULES[level]
            prev_rules = EMERGENCY_LEVEL_RULES[prev_level]
            
            # 현재 레벨의 합계가 이전 레벨보다 작거나 같아야 함
            current_sum = sum(current_rules.values())
            prev_sum = sum(prev_rules.values())
            assert current_sum <= prev_sum, (
                f"{level.name} should be more restrictive than {prev_level.name}"
            )


# =============================================================================
# RecoveryGate Tests
# =============================================================================


class TestRecoveryGateConfig:
    """RecoveryGateConfig 테스트."""
    
    def test_default_config(self):
        """기본 설정이 올바르게 설정되어야 함."""
        config = RecoveryGateConfig()
        assert config.stabilization_period_seconds == 300
        assert config.require_metrics_stable is True
        assert config.cpu_threshold_percent == 80.0
        assert config.error_rate_threshold == 0.05
        assert config.gradual_recovery is True
        assert config.level_step_delay_seconds == 60
        assert config.auto_rollback_on_failure is True
    
    def test_config_serialization(self):
        """설정이 직렬화/역직렬화되어야 함."""
        config = RecoveryGateConfig(
            stabilization_period_seconds=600,
            cpu_threshold_percent=90.0,
        )
        
        data = config.to_dict()
        restored = RecoveryGateConfig.from_dict(data)
        
        assert restored.stabilization_period_seconds == 600
        assert restored.cpu_threshold_percent == 90.0


class TestRecoveryGate:
    """RecoveryGate 테스트."""
    
    def test_check_recovery_allowed_when_metrics_check_disabled(self):
        """메트릭 확인이 비활성화되면 항상 허용해야 함."""
        config = RecoveryGateConfig(require_metrics_stable=False)
        gate = RecoveryGate(config=config)
        
        allowed, reason = gate.check_recovery_allowed()
        assert allowed is True
        assert "disabled" in reason.lower()
    
    def test_check_recovery_allowed_with_good_metrics(self):
        """메트릭이 양호하면 복구를 허용해야 함."""
        def good_metrics():
            return {"cpu_percent": 50.0, "error_rate": 0.01}
        
        config = RecoveryGateConfig(
            require_metrics_stable=True,
            cpu_threshold_percent=80.0,
            error_rate_threshold=0.05,
        )
        gate = RecoveryGate(config=config, metrics_checker=good_metrics)
        
        allowed, reason = gate.check_recovery_allowed()
        assert allowed is True
    
    def test_check_recovery_blocked_by_high_cpu(self):
        """CPU가 높으면 복구를 차단해야 함."""
        def high_cpu_metrics():
            return {"cpu_percent": 95.0, "error_rate": 0.01}
        
        config = RecoveryGateConfig(
            require_metrics_stable=True,
            cpu_threshold_percent=80.0,
        )
        gate = RecoveryGate(config=config, metrics_checker=high_cpu_metrics)
        
        allowed, reason = gate.check_recovery_allowed()
        assert allowed is False
        assert "CPU" in reason
    
    def test_check_recovery_blocked_by_high_error_rate(self):
        """에러율이 높으면 복구를 차단해야 함."""
        def high_error_metrics():
            return {"cpu_percent": 50.0, "error_rate": 0.10}
        
        config = RecoveryGateConfig(
            require_metrics_stable=True,
            error_rate_threshold=0.05,
        )
        gate = RecoveryGate(config=config, metrics_checker=high_error_metrics)
        
        allowed, reason = gate.check_recovery_allowed()
        assert allowed is False
        assert "Error" in reason
    
    def test_get_next_recovery_level(self):
        """점진적 복구에서 다음 레벨이 올바르게 반환되어야 함."""
        gate = RecoveryGate()
        
        assert gate.get_next_recovery_level(EmergencyLevel.LEVEL_3) == EmergencyLevel.LEVEL_2
        assert gate.get_next_recovery_level(EmergencyLevel.LEVEL_2) == EmergencyLevel.LEVEL_1
        assert gate.get_next_recovery_level(EmergencyLevel.LEVEL_1) == EmergencyLevel.NORMAL
        assert gate.get_next_recovery_level(EmergencyLevel.NORMAL) is None


# =============================================================================
# EmergencyState Tests
# =============================================================================


class TestEmergencyState:
    """EmergencyState 테스트."""
    
    def test_default_state(self):
        """기본 상태는 NORMAL이어야 함."""
        state = EmergencyState()
        assert state.level == EmergencyLevel.NORMAL
        assert state.is_active is False
    
    def test_state_serialization(self):
        """상태가 직렬화/역직렬화되어야 함."""
        state = EmergencyState(
            level=EmergencyLevel.LEVEL_2,
            is_active=True,
            activated_by="admin",
            activation_reason="Test",
        )
        
        data = state.to_dict()
        assert data["level"] == 2
        assert data["is_active"] is True
        
        restored = EmergencyState.from_dict(data)
        assert restored.level == EmergencyLevel.LEVEL_2
        assert restored.is_active is True
        assert restored.activated_by == "admin"


# =============================================================================
# GracefulDegradationManager Tests
# =============================================================================


class TestGracefulDegradationManager:
    """GracefulDegradationManager 테스트."""
    
    @pytest.fixture(autouse=True)
    def reset_manager(self):
        """각 테스트 전에 매니저 초기화."""
        # 싱글톤 인스턴스 초기화
        GracefulDegradationManager._instance = None
        manager = get_emergency_manager()
        manager.reset()
        yield
        manager.reset()
    
    def test_initial_state_is_normal(self):
        """초기 상태는 NORMAL이어야 함."""
        manager = get_emergency_manager()
        assert manager.get_current_level() == EmergencyLevel.NORMAL
        assert manager.is_active() is False
    
    def test_activate_manual(self):
        """수동 비상 모드 활성화가 동작해야 함."""
        manager = get_emergency_manager()
        
        state = manager.activate_manual(
            level=EmergencyLevel.LEVEL_2,
            reason="Test activation",
            activated_by="test_user",
            duration_minutes=30,
        )
        
        assert state.level == EmergencyLevel.LEVEL_2
        assert state.is_active is True
        assert state.activated_by == "test_user"
        assert state.activation_reason == "Test activation"
        assert state.is_auto_triggered is False
        assert state.expires_at is not None
    
    def test_activate_manual_requires_reason(self):
        """수동 활성화 시 사유가 필수여야 함."""
        manager = get_emergency_manager()
        
        with pytest.raises(ValueError, match="reason is required"):
            manager.activate_manual(
                level=EmergencyLevel.LEVEL_1,
                reason="",  # 빈 사유
                activated_by="test_user",
            )
    
    def test_activate_auto(self):
        """자동 비상 모드 활성화가 동작해야 함."""
        manager = get_emergency_manager()
        
        state = manager.activate_auto(
            level=EmergencyLevel.LEVEL_1,
            reason="High error rate detected",
            duration_minutes=15,
        )
        
        assert state.level == EmergencyLevel.LEVEL_1
        assert state.is_active is True
        assert state.is_auto_triggered is True
        assert state.activated_by == "system"
    
    def test_auto_trigger_ignores_lower_level(self):
        """자동 트리거는 더 낮은 레벨을 무시해야 함."""
        manager = get_emergency_manager()
        
        # LEVEL_2로 먼저 활성화
        manager.activate_manual(
            level=EmergencyLevel.LEVEL_2,
            reason="Initial",
            activated_by="admin",
        )
        
        # LEVEL_1로 자동 트리거 시도 (무시되어야 함)
        state = manager.activate_auto(
            level=EmergencyLevel.LEVEL_1,
            reason="Lower level trigger",
        )
        
        # 여전히 LEVEL_2여야 함
        assert state.level == EmergencyLevel.LEVEL_2
    
    @patch("selfhealing.services.emergency_mode.RecoveryGate.check_recovery_allowed")
    def test_deactivate(self, mock_check):
        """비상 모드 해제가 동작해야 함."""
        mock_check.return_value = (True, "OK")
        
        manager = get_emergency_manager()
        
        # 먼저 활성화
        manager.activate_manual(
            level=EmergencyLevel.LEVEL_1,
            reason="Test",
            activated_by="admin",
        )
        
        # 해제
        state = manager.deactivate(deactivated_by="admin")
        
        assert state.level == EmergencyLevel.NORMAL
        assert state.is_active is False
        assert state.deactivated_by == "admin"
    
    @patch("selfhealing.services.emergency_mode.RecoveryGate.check_recovery_allowed")
    def test_deactivate_blocked_by_metrics(self, mock_check):
        """메트릭이 불안정하면 해제가 차단되어야 함."""
        mock_check.return_value = (False, "CPU too high")
        
        manager = get_emergency_manager()
        
        # 먼저 활성화
        manager.activate_manual(
            level=EmergencyLevel.LEVEL_1,
            reason="Test",
            activated_by="admin",
        )
        
        # 해제 시도 (차단되어야 함)
        with pytest.raises(ValueError, match="Recovery not allowed"):
            manager.deactivate(deactivated_by="admin")
    
    @patch("selfhealing.services.emergency_mode.RecoveryGate.check_recovery_allowed")
    def test_deactivate_force(self, mock_check):
        """force=True면 메트릭 확인을 무시해야 함."""
        mock_check.return_value = (False, "CPU too high")
        
        manager = get_emergency_manager()
        
        # 먼저 활성화
        manager.activate_manual(
            level=EmergencyLevel.LEVEL_1,
            reason="Test",
            activated_by="admin",
        )
        
        # 강제 해제
        state = manager.deactivate(deactivated_by="admin", force=True)
        
        assert state.is_active is False
    
    def test_get_tier_multiplier(self):
        """현재 레벨에 따른 티어 배율이 올바르게 반환되어야 함."""
        manager = get_emergency_manager()
        
        # NORMAL 상태
        assert manager.get_tier_multiplier("critical") == 1.0
        assert manager.get_tier_multiplier("standard") == 1.0
        assert manager.get_tier_multiplier("non_essential") == 1.0
        
        # LEVEL_2로 활성화
        manager.activate_manual(
            level=EmergencyLevel.LEVEL_2,
            reason="Test",
            activated_by="admin",
        )
        
        assert manager.get_tier_multiplier("critical") == 1.0
        assert manager.get_tier_multiplier("standard") == 0.1
        assert manager.get_tier_multiplier("non_essential") == 0.0
    
    def test_expiration_auto_deactivates(self):
        """만료 시간이 지나면 자동으로 비활성화되어야 함."""
        manager = get_emergency_manager()
        
        # 1분 후 만료로 활성화
        manager.activate_manual(
            level=EmergencyLevel.LEVEL_1,
            reason="Test",
            activated_by="admin",
            duration_minutes=1,
        )
        
        # 만료 시간을 과거로 조작
        with manager._state_lock:
            past_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
            manager._state.expires_at = past_time
        
        # 상태 조회 시 만료 확인됨
        state = manager.get_state()
        assert state.is_active is False
    
    def test_history_tracking(self):
        """변경 이력이 추적되어야 함."""
        manager = get_emergency_manager()
        
        manager.activate_manual(
            level=EmergencyLevel.LEVEL_1,
            reason="First activation",
            activated_by="admin",
        )
        
        manager.activate_manual(
            level=EmergencyLevel.LEVEL_2,
            reason="Escalation",
            activated_by="admin",
        )
        
        history = manager.get_history()
        assert len(history) >= 2
    
    def test_singleton_pattern(self):
        """싱글톤 패턴이 동작해야 함."""
        manager1 = get_emergency_manager()
        manager2 = get_emergency_manager()
        
        assert manager1 is manager2


# =============================================================================
# Convenience Function Tests
# =============================================================================


class TestConvenienceFunctions:
    """편의 함수 테스트."""
    
    @pytest.fixture(autouse=True)
    def reset_manager(self):
        """각 테스트 전에 매니저 초기화."""
        GracefulDegradationManager._instance = None
        manager = get_emergency_manager()
        manager.reset()
        yield
        manager.reset()
    
    def test_is_emergency_active(self):
        """is_emergency_active() 함수가 동작해야 함."""
        assert is_emergency_active() is False
        
        get_emergency_manager().activate_manual(
            level=EmergencyLevel.LEVEL_1,
            reason="Test",
            activated_by="test",
        )
        
        assert is_emergency_active() is True
    
    def test_get_emergency_level(self):
        """get_emergency_level() 함수가 동작해야 함."""
        assert get_emergency_level() == EmergencyLevel.NORMAL
        
        get_emergency_manager().activate_manual(
            level=EmergencyLevel.LEVEL_2,
            reason="Test",
            activated_by="test",
        )
        
        assert get_emergency_level() == EmergencyLevel.LEVEL_2
    
    def test_get_tier_multiplier_function(self):
        """get_tier_multiplier() 함수가 동작해야 함."""
        assert get_tier_multiplier("critical") == 1.0
        
        get_emergency_manager().activate_manual(
            level=EmergencyLevel.LEVEL_3,
            reason="Test",
            activated_by="test",
        )
        
        assert get_tier_multiplier("critical") == 0.5


# =============================================================================
# Thread Safety Tests
# =============================================================================


class TestThreadSafety:
    """스레드 안전성 테스트."""
    
    @pytest.fixture(autouse=True)
    def reset_manager(self):
        """각 테스트 전에 매니저 초기화."""
        GracefulDegradationManager._instance = None
        manager = get_emergency_manager()
        manager.reset()
        yield
        manager.reset()
    
    def test_concurrent_activation(self):
        """동시 활성화가 안전하게 처리되어야 함."""
        manager = get_emergency_manager()
        errors = []
        
        def activate(level, name):
            try:
                manager.activate_manual(
                    level=level,
                    reason=f"Test {name}",
                    activated_by=name,
                )
            except Exception as e:
                errors.append(e)
        
        threads = [
            threading.Thread(target=activate, args=(EmergencyLevel.LEVEL_1, "thread1")),
            threading.Thread(target=activate, args=(EmergencyLevel.LEVEL_2, "thread2")),
            threading.Thread(target=activate, args=(EmergencyLevel.LEVEL_3, "thread3")),
        ]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # 에러가 없어야 함
        assert len(errors) == 0
        
        # 최종 상태가 유효해야 함
        state = manager.get_state()
        assert state.level in EmergencyLevel
        assert state.is_active is True
    
    def test_concurrent_reads(self):
        """동시 읽기가 안전하게 처리되어야 함."""
        manager = get_emergency_manager()
        manager.activate_manual(
            level=EmergencyLevel.LEVEL_2,
            reason="Test",
            activated_by="test",
        )
        
        results = []
        errors = []
        
        def read_state():
            try:
                for _ in range(100):
                    state = manager.get_state()
                    results.append(state.level)
            except Exception as e:
                errors.append(e)
        
        threads = [threading.Thread(target=read_state) for _ in range(5)]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert len(errors) == 0
        assert all(level == EmergencyLevel.LEVEL_2 for level in results)


# =============================================================================
# Integration Tests (Mock Backend)
# =============================================================================


class TestIntegrationWithMockBackend:
    """백엔드 연동 테스트."""
    
    @pytest.fixture(autouse=True)
    def reset_manager(self):
        """각 테스트 전에 매니저 초기화."""
        GracefulDegradationManager._instance = None
        manager = get_emergency_manager()
        manager.reset()
        yield
        manager.reset()
    
    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_state_persistence(self, mock_get_backend):
        """상태가 백엔드에 저장되어야 함."""
        mock_backend = MagicMock()
        mock_backend.get.return_value = None
        mock_get_backend.return_value = mock_backend
        
        # 새 인스턴스 생성
        GracefulDegradationManager._instance = None
        manager = GracefulDegradationManager()
        
        manager.activate_manual(
            level=EmergencyLevel.LEVEL_1,
            reason="Test",
            activated_by="test",
        )
        
        # 저장이 호출되었어야 함
        mock_backend.set.assert_called()
