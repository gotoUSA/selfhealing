"""
Unit tests for config_apply Celery tasks.

Phase C: Emergency Mode 체크 추가 테스트
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone


class TestIsEmergencyBlocking:
    """Test _is_emergency_blocking function."""

    @patch("selfhealing.services.emergency_mode.get_emergency_manager")
    def test_returns_false_when_normal(self, mock_get_manager):
        """NORMAL 레벨에서는 차단되지 않음."""
        from selfhealing.tasks.config_apply import _is_emergency_blocking
        from selfhealing.services.emergency_mode import EmergencyLevel

        mock_manager = MagicMock()
        mock_manager.get_current_level.return_value = EmergencyLevel.NORMAL
        mock_get_manager.return_value = mock_manager

        is_blocked, reason = _is_emergency_blocking()

        assert is_blocked is False
        assert reason == ""

    @patch("selfhealing.services.emergency_mode.get_emergency_manager")
    def test_returns_false_when_level_1(self, mock_get_manager):
        """LEVEL_1에서는 차단되지 않음."""
        from selfhealing.tasks.config_apply import _is_emergency_blocking
        from selfhealing.services.emergency_mode import EmergencyLevel

        mock_manager = MagicMock()
        mock_manager.get_current_level.return_value = EmergencyLevel.LEVEL_1
        mock_get_manager.return_value = mock_manager

        is_blocked, reason = _is_emergency_blocking()

        assert is_blocked is False
        assert reason == ""

    @patch("selfhealing.services.emergency_mode.get_emergency_manager")
    def test_returns_true_when_level_2(self, mock_get_manager):
        """LEVEL_2에서는 차단됨."""
        from selfhealing.tasks.config_apply import _is_emergency_blocking
        from selfhealing.services.emergency_mode import EmergencyLevel

        mock_manager = MagicMock()
        mock_manager.get_current_level.return_value = EmergencyLevel.LEVEL_2
        mock_get_manager.return_value = mock_manager

        is_blocked, reason = _is_emergency_blocking()

        assert is_blocked is True
        assert "LEVEL_2" in reason
        assert "blocked" in reason.lower()

    @patch("selfhealing.services.emergency_mode.get_emergency_manager")
    def test_returns_true_when_level_3(self, mock_get_manager):
        """LEVEL_3에서는 차단됨."""
        from selfhealing.tasks.config_apply import _is_emergency_blocking
        from selfhealing.services.emergency_mode import EmergencyLevel

        mock_manager = MagicMock()
        mock_manager.get_current_level.return_value = EmergencyLevel.LEVEL_3
        mock_get_manager.return_value = mock_manager

        is_blocked, reason = _is_emergency_blocking()

        assert is_blocked is True
        assert "LEVEL_3" in reason

    def test_returns_false_when_manager_unavailable(self):
        """EmergencyManager를 가져올 수 없으면 안전하게 허용."""
        from selfhealing.tasks.config_apply import _is_emergency_blocking

        with patch("selfhealing.services.emergency_mode.get_emergency_manager", side_effect=ImportError("Module not found")):
            is_blocked, reason = _is_emergency_blocking()

            assert is_blocked is False
            assert reason == ""


class TestApplyPendingConfigChanges:
    """Test apply_pending_config_changes task."""

    @patch("selfhealing.tasks.config_apply._is_emergency_blocking")
    @patch("selfhealing.tasks.config_apply.get_pending_config_service")
    @patch("selfhealing.tasks.config_apply.get_runtime_config_manager")
    def test_blocked_during_emergency(self, mock_config_manager, mock_pending_service, mock_emergency):
        """비상 모드에서 설정 변경이 차단됨."""
        from selfhealing.tasks.config_apply import apply_pending_config_changes

        mock_emergency.return_value = (True, "Emergency mode active (level=LEVEL_2)")

        result = apply_pending_config_changes()

        assert result["status"] == "blocked"
        assert "emergency" in result["reason"].lower()

    @patch("selfhealing.tasks.config_apply._is_emergency_blocking")
    @patch("selfhealing.tasks.config_apply.get_pending_config_service")
    @patch("selfhealing.tasks.config_apply.get_runtime_config_manager")
    def test_no_pending_changes(self, mock_config_manager, mock_pending_service, mock_emergency):
        """대기 중인 변경이 없으면 성공."""
        from selfhealing.tasks.config_apply import apply_pending_config_changes

        mock_emergency.return_value = (False, "")
        mock_pending_service.return_value.get_due_changes.return_value = []

        result = apply_pending_config_changes()

        assert result["status"] == "success"
        assert result["applied"] == 0

    @patch("selfhealing.tasks.config_apply._is_emergency_blocking")
    @patch("selfhealing.tasks.config_apply.get_pending_config_service")
    @patch("selfhealing.tasks.config_apply.get_runtime_config_manager")
    def test_applies_changes_when_not_emergency(self, mock_config_manager, mock_pending_service, mock_emergency):
        """비상 모드가 아니면 설정 변경이 적용됨."""
        from selfhealing.tasks.config_apply import apply_pending_config_changes

        mock_emergency.return_value = (False, "")

        # Mock pending change
        mock_change = MagicMock()
        mock_change.id = "change-1"
        mock_change.config_type = "circuit_breaker"
        mock_pending_service.return_value.get_due_changes.return_value = [mock_change]
        mock_config_manager.return_value.apply_pending_change.return_value = {"status": "applied"}

        result = apply_pending_config_changes()

        assert result["status"] == "success"
        assert result["applied"] == 1


class TestApplyGracefulConfigChange:
    """Test apply_graceful_config_change task."""

    @patch("selfhealing.tasks.config_apply._is_emergency_blocking")
    @patch("selfhealing.tasks.config_apply.get_pending_config_service")
    @patch("selfhealing.tasks.config_apply.get_runtime_config_manager")
    def test_blocked_during_emergency_returns_blocked_after_max_retries(
        self, mock_config_manager, mock_pending_service, mock_emergency
    ):
        """비상 모드에서 최대 재시도 후 blocked 상태 반환."""
        from selfhealing.tasks.config_apply import apply_graceful_config_change

        mock_emergency.return_value = (True, "Emergency mode active (level=LEVEL_3)")

        # Mock celery task context
        task = apply_graceful_config_change
        task.bind = True

        # Create mock request with max retries reached
        with patch.object(task, "request") as mock_request:
            mock_request.retries = 10  # max_retries reached

            with patch.object(task, "retry") as mock_retry:
                with patch.object(task, "max_retries", 10):
                    # The task should not retry when max retries reached
                    result = apply_graceful_config_change("pending-1")

                    assert result["status"] == "blocked"
                    assert result["pending_id"] == "pending-1"


class TestConfigApplyIntegration:
    """Integration tests for config apply."""

    def test_emergency_import_path_exists(self):
        """EmergencyLevel과 get_emergency_manager가 import 가능해야 함."""
        from selfhealing.services.emergency_mode import (
            EmergencyLevel,
            get_emergency_manager,
        )

        assert EmergencyLevel is not None
        assert get_emergency_manager is not None

    def test_emergency_levels_have_correct_values(self):
        """Emergency Level 값이 올바른 순서여야 함."""
        from selfhealing.services.emergency_mode import EmergencyLevel

        assert EmergencyLevel.NORMAL.value < EmergencyLevel.LEVEL_1.value
        assert EmergencyLevel.LEVEL_1.value < EmergencyLevel.LEVEL_2.value
        assert EmergencyLevel.LEVEL_2.value < EmergencyLevel.LEVEL_3.value
