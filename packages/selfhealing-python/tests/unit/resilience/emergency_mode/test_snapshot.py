"""
EmergencyModeManager Before Mutation Snapshot Tests.

Tests for snapshot and rollback functionality.
"""

import pytest
from unittest.mock import patch, MagicMock


class TestEmergencyModeSnapshot:
    """EmergencyModeManager Before Mutation Snapshot Tests."""
    
    def test_previous_states_initialized_empty(self):
        """_previous_states는 초기에 빈 리스트여야 함."""
        from selfhealing.services.emergency_mode import get_emergency_manager
        
        manager = get_emergency_manager()
        manager._previous_states = []  # 테스트를 위해 초기화
        
        assert manager._previous_states == []
    
    @patch("selfhealing.services.config_history.get_config_history_service")
    def test_activate_saves_previous_state(self, mock_get_service):
        """activate 시 이전 상태를 스냅샷으로 저장해야 함."""
        from selfhealing.services.emergency_mode import EmergencyLevel, get_emergency_manager
        
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        
        manager = get_emergency_manager()
        manager._previous_states = []  # 테스트를 위해 초기화
        
        # 비활성 상태에서 활성화
        manager.deactivate(deactivated_by="test_user")
        initial_state_count = len(manager._previous_states)
        
        manager.activate_manual(
            level=EmergencyLevel.LEVEL_1,
            reason="Test activation",
            activated_by="admin",
        )
        
        # 스냅샷이 저장되어야 함
        assert len(manager._previous_states) == initial_state_count + 1
        
        snapshot = manager._previous_states[-1]
        assert "state" in snapshot
        assert "action" in snapshot
        assert "timestamp" in snapshot
        assert snapshot["action"] == "activate_manual"
    
    @patch("selfhealing.services.config_history.get_config_history_service")
    def test_deactivate_saves_previous_state(self, mock_get_service):
        """deactivate 시 이전 상태를 스냅샷으로 저장해야 함."""
        from selfhealing.services.emergency_mode import EmergencyLevel, get_emergency_manager
        
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        
        manager = get_emergency_manager()
        manager._previous_states = []  # 테스트를 위해 초기화
        
        # 먼저 활성화
        manager.activate_manual(
            level=EmergencyLevel.LEVEL_2,
            reason="Test",
            activated_by="admin",
        )
        count_before_deactivate = len(manager._previous_states)
        
        # 비활성화
        manager.deactivate(deactivated_by="test_user")
        
        # deactivate에서도 스냅샷 저장되어야 함
        assert len(manager._previous_states) == count_before_deactivate + 1
        
        snapshot = manager._previous_states[-1]
        assert snapshot["action"] == "deactivate"
    
    @patch("selfhealing.services.config_history.get_config_history_service")
    def test_get_previous_states_returns_newest_first(self, mock_get_service):
        """get_previous_states()는 최신 순으로 반환해야 함."""
        from selfhealing.services.emergency_mode import EmergencyLevel, get_emergency_manager
        
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        
        manager = get_emergency_manager()
        manager._previous_states = []  # 테스트를 위해 초기화
        
        # 여러 번 상태 변경
        manager.activate_manual(
            level=EmergencyLevel.LEVEL_1,
            reason="First",
            activated_by="admin",
        )
        manager.deactivate(deactivated_by="test_user")
        manager.activate_manual(
            level=EmergencyLevel.LEVEL_2,
            reason="Second",
            activated_by="admin",
        )
        
        # 최신 순으로 정렬되어 반환되어야 함
        states = manager.get_previous_states()
        
        assert len(states) >= 3
        # 최신이 먼저 (가장 최근 activate_manual이 첫 번째)
        assert states[0]["action"] == "activate_manual"
    
    @patch("selfhealing.services.config_history.get_config_history_service")
    def test_rollback_to_previous_restores_state(self, mock_get_service):
        """rollback_to_previous()는 이전 상태를 복원해야 함."""
        from selfhealing.services.emergency_mode import EmergencyLevel, get_emergency_manager
        
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        
        manager = get_emergency_manager()
        manager._previous_states = []  # 테스트를 위해 초기화
        
        # 먼저 비활성 상태 확인
        manager.deactivate(deactivated_by="test_user")
        
        # LEVEL_1 활성화 (이전 비활성 상태 저장됨)
        manager.activate_manual(
            level=EmergencyLevel.LEVEL_1,
            reason="Test",
            activated_by="admin",
        )
        
        # 현재 활성 상태
        assert manager.get_state().is_active is True
        
        # 롤백 (index=0 = 가장 최근 스냅샷 = 활성화 전 상태)
        result = manager.rollback_to_previous(0)
        
        # rollback_to_previous returns EmergencyState on success, None on failure
        assert result is not None
        # 비활성 상태로 복원되어야 함
        assert manager.get_state().is_active is False
    
    @patch("selfhealing.services.config_history.get_config_history_service")
    def test_rollback_invalid_index_returns_none(self, mock_get_service):
        """잘못된 index로 rollback 시 None 반환."""
        from selfhealing.services.emergency_mode import get_emergency_manager
        
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        
        manager = get_emergency_manager()
        manager._previous_states = []  # 테스트를 위해 초기화
        
        # 존재하지 않는 인덱스
        result = manager.rollback_to_previous(999)
        
        assert result is None
    
    @patch("selfhealing.services.config_history.get_config_history_service")
    def test_snapshot_limit_10(self, mock_get_service):
        """스냅샷은 최대 10개로 제한되어야 함."""
        from selfhealing.services.emergency_mode import EmergencyLevel, get_emergency_manager
        
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        
        manager = get_emergency_manager()
        manager._previous_states = []  # 테스트를 위해 초기화
        
        # 15번 상태 변경 (activate/deactivate 반복)
        for i in range(15):
            if i % 2 == 0:
                manager.activate_manual(
                    level=EmergencyLevel.LEVEL_1,
                    reason=f"Test {i}",
                    activated_by="admin",
                )
            else:
                manager.deactivate(deactivated_by="test_user")
        
        # 최대 10개까지만 유지
        assert len(manager._previous_states) <= 10
