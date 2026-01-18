"""
Emergency Mode Integration Tests (Mock Backend).

Tests for backend integration.
"""

import pytest
from unittest.mock import patch, MagicMock


class TestIntegrationWithMockBackend:
    """백엔드 연동 테스트."""
    
    @pytest.fixture(autouse=True)
    def reset_manager(self):
        """각 테스트 전에 매니저 초기화."""
        from selfhealing.services.emergency_mode import (
            GracefulDegradationManager,
            get_emergency_manager,
        )
        
        GracefulDegradationManager._instance = None
        manager = get_emergency_manager()
        manager.reset()
        yield
        manager.reset()
    
    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_state_persistence(self, mock_get_backend):
        """상태가 백엔드에 저장되어야 함."""
        from selfhealing.services.emergency_mode import (
            EmergencyLevel,
            GracefulDegradationManager,
        )
        
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
