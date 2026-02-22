"""
Tests for Emergency Mode Models

Covers:
- RecoveryGateConfig dataclass
- EmergencyState dataclass
"""



class TestRecoveryGateConfig:
    """Tests for RecoveryGateConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        from selfhealing.services.emergency_mode.models import RecoveryGateConfig

        config = RecoveryGateConfig()

        assert config.stabilization_period_seconds == 300
        assert config.require_metrics_stable is True
        assert config.cpu_threshold_percent == 80.0
        assert config.error_rate_threshold == 0.05
        assert config.gradual_recovery is True
        assert config.level_step_delay_seconds == 60
        assert config.health_check_interval_seconds == 30
        assert config.auto_rollback_on_failure is True

    def test_custom_values(self):
        """Test custom configuration values."""
        from selfhealing.services.emergency_mode.models import RecoveryGateConfig

        config = RecoveryGateConfig(
            stabilization_period_seconds=600,
            cpu_threshold_percent=70.0,
            error_rate_threshold=0.01,
        )

        assert config.stabilization_period_seconds == 600
        assert config.cpu_threshold_percent == 70.0
        assert config.error_rate_threshold == 0.01

    def test_to_dict(self):
        """Test to_dict conversion."""
        from selfhealing.services.emergency_mode.models import RecoveryGateConfig

        config = RecoveryGateConfig()
        d = config.to_dict()

        assert isinstance(d, dict)
        assert "stabilization_period_seconds" in d
        assert "cpu_threshold_percent" in d

    def test_from_dict(self):
        """Test from_dict creation."""
        from selfhealing.services.emergency_mode.models import RecoveryGateConfig

        data = {
            "stabilization_period_seconds": 120,
            "cpu_threshold_percent": 90.0,
        }

        config = RecoveryGateConfig.from_dict(data)

        assert config.stabilization_period_seconds == 120
        assert config.cpu_threshold_percent == 90.0

    def test_from_dict_ignores_unknown_keys(self):
        """Test from_dict ignores unknown keys."""
        from selfhealing.services.emergency_mode.models import RecoveryGateConfig

        data = {
            "stabilization_period_seconds": 120,
            "unknown_key": "value",
        }

        config = RecoveryGateConfig.from_dict(data)
        assert config.stabilization_period_seconds == 120


class TestEmergencyState:
    """Tests for EmergencyState dataclass."""

    def test_default_values(self):
        """Test default state values."""
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        from selfhealing.services.emergency_mode.models import EmergencyState

        state = EmergencyState()

        assert state.level == EmergencyLevel.NORMAL
        assert state.is_active is False
        assert state.activated_at is None
        assert state.activated_by is None
        assert state.is_auto_triggered is False
        assert state.is_recovering is False

    def test_active_state(self):
        """Test active emergency state."""
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        from selfhealing.services.emergency_mode.models import EmergencyState

        state = EmergencyState(
            level=EmergencyLevel.LEVEL_2,
            is_active=True,
            activated_at="2025-12-29T10:00:00Z",
            activated_by="admin",
            activation_reason="High error rate",
        )

        assert state.level == EmergencyLevel.LEVEL_2
        assert state.is_active is True
        assert state.activated_by == "admin"

    def test_to_dict(self):
        """Test to_dict conversion."""
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        from selfhealing.services.emergency_mode.models import EmergencyState

        state = EmergencyState(level=EmergencyLevel.LEVEL_1)
        d = state.to_dict()

        assert isinstance(d, dict)
        assert d["level"] == 1  # Enum value
        assert "is_active" in d

    def test_to_dict_with_target_level(self):
        """Test to_dict with target level during recovery."""
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        from selfhealing.services.emergency_mode.models import EmergencyState

        state = EmergencyState(
            level=EmergencyLevel.LEVEL_2,
            is_recovering=True,
            target_level=EmergencyLevel.NORMAL,
        )
        d = state.to_dict()

        assert d["target_level"] == 0  # NORMAL value

    def test_from_dict(self):
        """Test from_dict creation."""
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        from selfhealing.services.emergency_mode.models import EmergencyState

        data = {
            "level": 2,
            "is_active": True,
            "activated_by": "system",
        }

        state = EmergencyState.from_dict(data)

        assert state.level == EmergencyLevel.LEVEL_2
        assert state.is_active is True
        assert state.activated_by == "system"

    def test_from_dict_with_target_level(self):
        """Test from_dict with target level."""
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        from selfhealing.services.emergency_mode.models import EmergencyState

        data = {
            "level": 2,
            "is_recovering": True,
            "target_level": 0,
        }

        state = EmergencyState.from_dict(data)

        assert state.target_level == EmergencyLevel.NORMAL


class TestEmergencyStateWithExpiration:
    """Tests for EmergencyState with expiration."""

    def test_state_with_expiration(self):
        """Test state with expiration time."""
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        from selfhealing.services.emergency_mode.models import EmergencyState

        state = EmergencyState(
            level=EmergencyLevel.LEVEL_1,
            is_active=True,
            expires_at="2025-12-29T12:00:00Z",
        )

        assert state.expires_at == "2025-12-29T12:00:00Z"

    def test_deactivated_state(self):
        """Test deactivated state."""
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        from selfhealing.services.emergency_mode.models import EmergencyState

        state = EmergencyState(
            level=EmergencyLevel.NORMAL,
            is_active=False,
            deactivated_at="2025-12-29T11:00:00Z",
            deactivated_by="admin",
        )

        assert state.is_active is False
        assert state.deactivated_by == "admin"
