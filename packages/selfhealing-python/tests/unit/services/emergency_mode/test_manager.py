"""
Tests for GracefulDegradationManager

Covers:
- Manager initialization (singleton)
- State access
- Manual activation/deactivation
- Tier multipliers
"""

from datetime import datetime, timezone

import pytest


class TestGracefulDegradationManagerSingleton:
    """Tests for singleton behavior."""

    def test_singleton_instance(self):
        """Test that manager is singleton."""
        from selfhealing.services.emergency_mode.manager import (
            GracefulDegradationManager,
        )

        manager1 = GracefulDegradationManager()
        manager2 = GracefulDegradationManager()

        assert manager1 is manager2

    def test_init_creates_recovery_gate(self):
        """Test that init creates recovery gate."""
        from selfhealing.services.emergency_mode.manager import (
            GracefulDegradationManager,
        )

        manager = GracefulDegradationManager()

        assert manager._recovery_gate is not None


class TestGetState:
    """Tests for get_state method."""

    def test_get_state_returns_emergency_state(self):
        """Test get_state returns EmergencyState."""
        from selfhealing.services.emergency_mode.manager import (
            GracefulDegradationManager,
        )
        from selfhealing.services.emergency_mode.models import EmergencyState

        manager = GracefulDegradationManager()

        state = manager.get_state()

        assert isinstance(state, EmergencyState)

    def test_get_state_returns_copy(self):
        """Test get_state returns a copy not the original."""
        from selfhealing.services.emergency_mode.manager import (
            GracefulDegradationManager,
        )

        manager = GracefulDegradationManager()

        state1 = manager.get_state()
        state2 = manager.get_state()

        # Should be different objects
        assert state1 is not state2


class TestGetCurrentLevel:
    """Tests for get_current_level method."""

    def test_get_current_level_returns_level(self):
        """Test get_current_level returns EmergencyLevel."""
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        from selfhealing.services.emergency_mode.manager import (
            GracefulDegradationManager,
        )

        manager = GracefulDegradationManager()

        level = manager.get_current_level()

        assert isinstance(level, EmergencyLevel)


class TestIsActive:
    """Tests for is_active method."""

    def test_is_active_returns_bool(self):
        """Test is_active returns boolean."""
        from selfhealing.services.emergency_mode.manager import (
            GracefulDegradationManager,
        )

        manager = GracefulDegradationManager()

        result = manager.is_active()

        assert isinstance(result, bool)


class TestGetTierMultiplier:
    """Tests for get_tier_multiplier method."""

    def test_get_tier_multiplier_critical(self):
        """Test tier multiplier for critical tier."""
        from selfhealing.services.emergency_mode.manager import (
            GracefulDegradationManager,
        )

        manager = GracefulDegradationManager()

        multiplier = manager.get_tier_multiplier("critical")

        assert isinstance(multiplier, float)
        assert 0.0 <= multiplier <= 1.0

    def test_get_tier_multiplier_standard(self):
        """Test tier multiplier for standard tier."""
        from selfhealing.services.emergency_mode.manager import (
            GracefulDegradationManager,
        )

        manager = GracefulDegradationManager()

        multiplier = manager.get_tier_multiplier("standard")

        assert isinstance(multiplier, float)

    def test_get_tier_multiplier_non_essential(self):
        """Test tier multiplier for non_essential tier."""
        from selfhealing.services.emergency_mode.manager import (
            GracefulDegradationManager,
        )

        manager = GracefulDegradationManager()

        multiplier = manager.get_tier_multiplier("non_essential")

        assert isinstance(multiplier, float)

    def test_unknown_tier_returns_default(self):
        """Test unknown tier returns default multiplier."""
        from selfhealing.services.emergency_mode.manager import (
            GracefulDegradationManager,
        )

        manager = GracefulDegradationManager()

        multiplier = manager.get_tier_multiplier("unknown_tier")

        assert multiplier == 1.0


class TestActivateManual:
    """Tests for activate_manual method."""

    def test_activate_manual_success(self):
        """Test manual activation works."""
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        from selfhealing.services.emergency_mode.manager import (
            GracefulDegradationManager,
        )

        manager = GracefulDegradationManager()

        state = manager.activate_manual(
            level=EmergencyLevel.LEVEL_1,
            reason="Test activation",
            activated_by="test_user",
        )

        assert state.is_active is True
        assert state.level == EmergencyLevel.LEVEL_1

        # Cleanup
        manager.deactivate("test", "cleanup")

    def test_activate_manual_requires_reason(self):
        """Test activation requires reason."""
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        from selfhealing.services.emergency_mode.manager import (
            GracefulDegradationManager,
        )

        manager = GracefulDegradationManager()

        with pytest.raises(ValueError, match="reason"):
            manager.activate_manual(
                level=EmergencyLevel.LEVEL_1,
                reason="",
                activated_by="test_user",
            )

    def test_activate_manual_with_duration(self):
        """Test activation with duration sets expiry."""
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        from selfhealing.services.emergency_mode.manager import (
            GracefulDegradationManager,
        )

        manager = GracefulDegradationManager()

        state = manager.activate_manual(
            level=EmergencyLevel.LEVEL_2,
            reason="Test with duration",
            activated_by="test_user",
            duration_minutes=30,
        )

        assert state.expires_at is not None

        # Cleanup
        manager.deactivate("test", "cleanup")

    def test_activate_normal_deactivates(self):
        """Test activating to NORMAL level deactivates."""
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        from selfhealing.services.emergency_mode.manager import (
            GracefulDegradationManager,
        )

        manager = GracefulDegradationManager()

        # First activate
        manager.activate_manual(
            level=EmergencyLevel.LEVEL_1,
            reason="Setup",
            activated_by="test",
        )

        # Then "activate" to NORMAL
        state = manager.activate_manual(
            level=EmergencyLevel.NORMAL,
            reason="Deactivate via NORMAL",
            activated_by="test",
        )

        assert state.is_active is False


class TestDeactivate:
    """Tests for deactivate method."""

    def test_deactivate_success(self):
        """Test deactivation works."""
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        from selfhealing.services.emergency_mode.manager import (
            GracefulDegradationManager,
        )

        manager = GracefulDegradationManager()

        # First activate
        manager.activate_manual(
            level=EmergencyLevel.LEVEL_1,
            reason="Setup",
            activated_by="test",
        )

        # Then deactivate
        state = manager.deactivate(
            deactivated_by="test_user",
            reason="Test deactivation",
        )

        assert state.is_active is False
        assert state.level == EmergencyLevel.NORMAL


class TestThreadSafety:
    """Thread safety tests."""

    def test_concurrent_get_state(self):
        """Test concurrent get_state is thread-safe."""
        import threading

        from selfhealing.services.emergency_mode.manager import (
            GracefulDegradationManager,
        )

        manager = GracefulDegradationManager()
        results = []
        errors = []

        def get_state():
            try:
                for _ in range(100):
                    state = manager.get_state()
                    results.append(state)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=get_state) for _ in range(3)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 300


class TestCacheManagement:
    """Tests for cache management."""

    def test_invalidate_cache(self):
        """Test cache invalidation."""
        from selfhealing.services.emergency_mode.manager import (
            GracefulDegradationManager,
        )

        manager = GracefulDegradationManager()
        manager._last_load_time = datetime.now(timezone.utc)

        manager._invalidate_cache()

        assert manager._last_load_time is None

    def test_is_cache_valid_returns_bool(self):
        """Test _is_cache_valid returns boolean."""
        from selfhealing.services.emergency_mode.manager import (
            GracefulDegradationManager,
        )

        manager = GracefulDegradationManager()

        result = manager._is_cache_valid()

        assert isinstance(result, bool)
