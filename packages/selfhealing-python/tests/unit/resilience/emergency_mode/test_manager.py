"""
GracefulDegradationManager Tests.

Tests for GracefulDegradationManager functionality.
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock


class TestGracefulDegradationManager:
    """GracefulDegradationManager 테스트."""
    
    @pytest.fixture(autouse=True)
    def reset_manager(self):
        """각 테스트 전에 매니저 초기화."""
        from selfhealing.services.emergency_mode import (
            GracefulDegradationManager,
            get_emergency_manager,
        )
        
        # 싱글톤 인스턴스 초기화
        GracefulDegradationManager._instance = None
        manager = get_emergency_manager()
        manager.reset()
        yield
        manager.reset()
    
    def test_initial_state_is_normal(self):
        """초기 상태는 NORMAL이어야 함."""
        from selfhealing.services.emergency_mode import EmergencyLevel, get_emergency_manager
        
        manager = get_emergency_manager()
        assert manager.get_current_level() == EmergencyLevel.NORMAL
        assert manager.is_active() is False
    
    def test_activate_manual(self):
        """수동 비상 모드 활성화가 동작해야 함."""
        from selfhealing.services.emergency_mode import EmergencyLevel, get_emergency_manager
        
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
        from selfhealing.services.emergency_mode import EmergencyLevel, get_emergency_manager
        
        manager = get_emergency_manager()
        
        with pytest.raises(ValueError, match="reason is required"):
            manager.activate_manual(
                level=EmergencyLevel.LEVEL_1,
                reason="",  # 빈 사유
                activated_by="test_user",
            )
    
    def test_activate_auto(self):
        """자동 비상 모드 활성화가 동작해야 함."""
        from selfhealing.services.emergency_mode import EmergencyLevel, get_emergency_manager
        
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
        from selfhealing.services.emergency_mode import EmergencyLevel, get_emergency_manager
        
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
        from selfhealing.services.emergency_mode import EmergencyLevel, get_emergency_manager
        
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
        from selfhealing.services.emergency_mode import EmergencyLevel, get_emergency_manager
        
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
        from selfhealing.services.emergency_mode import EmergencyLevel, get_emergency_manager
        
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
        from selfhealing.services.emergency_mode import EmergencyLevel, get_emergency_manager
        
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
        from selfhealing.services.emergency_mode import EmergencyLevel, get_emergency_manager
        
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
        from selfhealing.services.emergency_mode import EmergencyLevel, get_emergency_manager
        
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
        from selfhealing.services.emergency_mode import get_emergency_manager
        
        manager1 = get_emergency_manager()
        manager2 = get_emergency_manager()
        
        assert manager1 is manager2
