"""
Unit tests for config_apply Celery tasks.

Thin Task, Fat Service Architecture:
- Celery Task들은 서비스 레이어에 위임
- 모든 비즈니스 로직은 ConfigApplyService에서 처리
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone


class TestApplyPendingConfigChanges:
    """Test apply_pending_config_changes task."""

    def test_blocked_during_emergency(self):
        """비상 모드에서 설정 변경이 차단됨."""
        from selfhealing.tasks.config_apply import apply_pending_config_changes

        with patch("selfhealing.services.execution_services.get_config_apply_service") as mock_svc:
            mock_service = MagicMock()
            mock_service.apply_pending_changes.return_value = {
                "status": "blocked",
                "reason": "Emergency mode active (level=LEVEL_2)"
            }
            mock_svc.return_value = mock_service

            # Mock Celery task retry
            with patch.object(apply_pending_config_changes, "retry"):
                result = apply_pending_config_changes()

            assert result.get("status") == "blocked"
            assert "emergency" in result.get("reason", "").lower()

    def test_no_pending_changes(self):
        """대기 중인 변경이 없으면 성공."""
        from selfhealing.tasks.config_apply import apply_pending_config_changes

        with patch("selfhealing.services.execution_services.get_config_apply_service") as mock_svc:
            mock_service = MagicMock()
            mock_service.apply_pending_changes.return_value = {
                "status": "success",
                "applied": 0
            }
            mock_svc.return_value = mock_service

            with patch.object(apply_pending_config_changes, "retry"):
                result = apply_pending_config_changes()

            assert result["status"] == "success"
            assert result["applied"] == 0

    def test_applies_changes_when_not_emergency(self):
        """비상 모드가 아니면 설정 변경이 적용됨."""
        from selfhealing.tasks.config_apply import apply_pending_config_changes

        with patch("selfhealing.services.execution_services.get_config_apply_service") as mock_svc:
            mock_service = MagicMock()
            mock_service.apply_pending_changes.return_value = {
                "status": "success",
                "applied": 1
            }
            mock_svc.return_value = mock_service

            with patch.object(apply_pending_config_changes, "retry"):
                result = apply_pending_config_changes()

            assert result["status"] == "success"
            assert result["applied"] == 1


class TestApplyGracefulConfigChange:
    """Test apply_graceful_config_change task."""

    def test_graceful_apply_success(self):
        """정상적인 graceful 적용."""
        from selfhealing.tasks.config_apply import apply_graceful_config_change

        with patch("selfhealing.services.execution_services.get_config_apply_service") as mock_svc:
            mock_service = MagicMock()
            mock_service.apply_graceful_change.return_value = {
                "status": "success",
                "pending_id": "pending-1"
            }
            mock_svc.return_value = mock_service

            # Call the underlying function directly, not as a Celery task
            result = apply_graceful_config_change("pending-1")

            assert result["status"] == "success"


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
