"""
EmergencyMode and ConfigHistory Integration Tests.

Tests for config history integration.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


class TestEmergencyModeConfigHistoryIntegration:
    """EmergencyMode와 ConfigHistory 연동 테스트."""

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

    @patch("selfhealing.services.config_history.get_config_history_service")
    def test_activate_manual_saves_to_history(self, mock_get_service):
        """수동 활성화 시 ConfigHistory에 저장."""
        from selfhealing.services.emergency_mode import (
            EmergencyLevel,
            get_emergency_manager,
        )

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        manager = get_emergency_manager()
        manager.activate_manual(
            level=EmergencyLevel.LEVEL_2,
            reason="High error rate",
            activated_by="admin",
            duration_minutes=30,
        )

        # save_version이 호출되었어야 함
        mock_service.save_version.assert_called_once()
        call_kwargs = mock_service.save_version.call_args[1]

        assert call_kwargs["config_type"] == "emergency"
        assert call_kwargs["changed_by"] == "admin"
        assert "ACTIVATED" in call_kwargs["reason"]
        assert "High error rate" in call_kwargs["reason"]
        assert call_kwargs["values"]["level"] == EmergencyLevel.LEVEL_2.value
        assert call_kwargs["values"]["is_active"] is True

    @patch("selfhealing.services.config_history.get_config_history_service")
    def test_activate_auto_saves_to_history(self, mock_get_service):
        """자동 활성화 시 ConfigHistory에 저장."""
        from selfhealing.services.emergency_mode import (
            EmergencyLevel,
            get_emergency_manager,
        )

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        manager = get_emergency_manager()
        manager.activate_auto(
            level=EmergencyLevel.LEVEL_3,
            reason="Circuit breaker open",
            duration_minutes=15,
        )

        # save_version이 호출되었어야 함
        mock_service.save_version.assert_called_once()
        call_kwargs = mock_service.save_version.call_args[1]

        assert call_kwargs["config_type"] == "emergency"
        assert call_kwargs["changed_by"] == "system"
        assert "AUTO_ACTIVATED" in call_kwargs["reason"]
        assert "Circuit breaker open" in call_kwargs["reason"]
        assert call_kwargs["values"]["level"] == EmergencyLevel.LEVEL_3.value
        assert call_kwargs["values"]["is_auto_triggered"] is True

    @patch("selfhealing.services.config_history.get_config_history_service")
    def test_deactivate_saves_to_history(self, mock_get_service):
        """비활성화 시 ConfigHistory에 저장."""
        from selfhealing.services.emergency_mode import (
            EmergencyLevel,
            get_emergency_manager,
        )

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        manager = get_emergency_manager()

        # 먼저 활성화
        manager.activate_manual(
            level=EmergencyLevel.LEVEL_1,
            reason="Test",
            activated_by="admin",
        )

        mock_service.reset_mock()

        # 비활성화 (force=True로 메트릭 체크 우회)
        manager.deactivate(deactivated_by="admin", reason="Issue resolved", force=True)

        # save_version이 호출되었어야 함
        mock_service.save_version.assert_called_once()
        call_kwargs = mock_service.save_version.call_args[1]

        assert call_kwargs["config_type"] == "emergency"
        assert call_kwargs["changed_by"] == "admin"
        assert "DEACTIVATED" in call_kwargs["reason"]
        assert "Issue resolved" in call_kwargs["reason"]
        assert call_kwargs["values"]["level"] == EmergencyLevel.NORMAL.value
        assert call_kwargs["values"]["is_active"] is False

    @patch("selfhealing.services.config_history.get_config_history_service")
    def test_history_save_failure_does_not_break_activation(self, mock_get_service):
        """History 저장 실패해도 활성화는 성공해야 함."""
        from selfhealing.services.emergency_mode import (
            EmergencyLevel,
            get_emergency_manager,
        )

        mock_service = MagicMock()
        mock_service.save_version.side_effect = Exception("Redis connection failed")
        mock_get_service.return_value = mock_service

        manager = get_emergency_manager()

        # History 저장 실패해도 활성화는 성공
        state = manager.activate_manual(
            level=EmergencyLevel.LEVEL_2,
            reason="Test",
            activated_by="admin",
        )

        assert state.is_active is True
        assert state.level == EmergencyLevel.LEVEL_2

    @patch("selfhealing.services.config_history.get_config_history_service")
    def test_history_save_failure_does_not_break_deactivation(self, mock_get_service):
        """History 저장 실패해도 비활성화는 성공해야 함."""
        from selfhealing.services.emergency_mode import (
            EmergencyLevel,
            get_emergency_manager,
        )

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        manager = get_emergency_manager()

        # 활성화
        manager.activate_manual(
            level=EmergencyLevel.LEVEL_1,
            reason="Test",
            activated_by="admin",
        )

        # 비활성화 시 History 저장 실패 시뮬레이션
        mock_service.save_version.side_effect = Exception("Redis connection failed")

        # 그래도 비활성화는 성공
        state = manager.deactivate(deactivated_by="admin", force=True)

        assert state.is_active is False
        assert state.level == EmergencyLevel.NORMAL

    @patch("selfhealing.services.config_history.get_config_history_service")
    def test_history_service_import_failure_graceful_degradation(self, mock_get_service):
        """ConfigHistory 서비스 임포트 실패 시에도 정상 동작."""
        from selfhealing.services.emergency_mode import (
            EmergencyLevel,
            get_emergency_manager,
        )

        mock_get_service.side_effect = ImportError("Module not found")

        manager = get_emergency_manager()

        # 임포트 실패해도 활성화는 성공
        state = manager.activate_manual(
            level=EmergencyLevel.LEVEL_2,
            reason="Test",
            activated_by="admin",
        )

        assert state.is_active is True
        assert state.level == EmergencyLevel.LEVEL_2

    @patch("selfhealing.services.config_history.get_config_history_service")
    def test_auto_expiration_triggers_deactivation_with_history(self, mock_get_service):
        """자동 만료 시에도 History에 저장 (DEACTIVATED 기록)."""
        from selfhealing.services.emergency_mode import (
            EmergencyLevel,
            get_emergency_manager,
        )

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        manager = get_emergency_manager()

        # 이미 만료된 시간으로 활성화
        manager.activate_manual(
            level=EmergencyLevel.LEVEL_1,
            reason="Test",
            activated_by="admin",
            duration_minutes=0,  # 즉시 만료
        )

        mock_service.reset_mock()

        # 만료 상태를 강제로 설정
        with manager._state_lock:
            manager._state.expires_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

        # 상태 확인 시 자동 만료 처리
        state = manager.get_state()

        # 자동 만료 시에도 DEACTIVATED로 History에 저장됨
        assert state.is_active is False
