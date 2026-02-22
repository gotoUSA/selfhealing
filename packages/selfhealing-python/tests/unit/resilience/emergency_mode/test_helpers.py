"""
Emergency Mode Convenience Functions Tests.

Tests for helper functions.
"""

import pytest


class TestConvenienceFunctions:
    """편의 함수 테스트."""

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

    def test_is_emergency_active(self):
        """is_emergency_active() 함수가 동작해야 함."""
        from selfhealing.services.emergency_mode import (
            EmergencyLevel,
            get_emergency_manager,
            is_emergency_active,
        )

        assert is_emergency_active() is False

        get_emergency_manager().activate_manual(
            level=EmergencyLevel.LEVEL_1,
            reason="Test",
            activated_by="test",
        )

        assert is_emergency_active() is True

    def test_get_emergency_level(self):
        """get_emergency_level() 함수가 동작해야 함."""
        from selfhealing.services.emergency_mode import (
            EmergencyLevel,
            get_emergency_level,
            get_emergency_manager,
        )

        assert get_emergency_level() == EmergencyLevel.NORMAL

        get_emergency_manager().activate_manual(
            level=EmergencyLevel.LEVEL_2,
            reason="Test",
            activated_by="test",
        )

        assert get_emergency_level() == EmergencyLevel.LEVEL_2

    def test_get_tier_multiplier_function(self):
        """get_tier_multiplier() 함수가 동작해야 함."""
        from selfhealing.services.emergency_mode import (
            EmergencyLevel,
            get_emergency_manager,
            get_tier_multiplier,
        )

        assert get_tier_multiplier("critical") == 1.0

        get_emergency_manager().activate_manual(
            level=EmergencyLevel.LEVEL_3,
            reason="Test",
            activated_by="test",
        )

        assert get_tier_multiplier("critical") == 0.5
