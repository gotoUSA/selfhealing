"""
Tests for GovernanceSettings and ChaosSettings.
"""

import pytest
from pydantic import ValidationError


class TestGovernanceSettings:
    """Tests for GovernanceSettings."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        from selfhealing.settings.governance import reset_governance_settings
        reset_governance_settings()
        yield
        reset_governance_settings()

    def test_default_values(self):
        """기본값이 core/config.py:GovernanceConfig와 일치하는지 검증."""
        from selfhealing.settings.governance import GovernanceSettings

        settings = GovernanceSettings()

        assert settings.threshold_operator == 0.15
        assert settings.threshold_admin == 0.30
        assert settings.emergency_expiry_hours == 8
        assert settings.default_mode == "NORMAL"
        assert settings.four_eyes_enabled is False

    def test_env_override(self, monkeypatch):
        """환경변수로 값을 오버라이드할 수 있는지 검증."""
        from selfhealing.settings.governance import GovernanceSettings

        monkeypatch.setenv("SELFHEALING_GOVERNANCE_EMERGENCY_EXPIRY_HOURS", "12")

        settings = GovernanceSettings()

        assert settings.emergency_expiry_hours == 12

    def test_validation_mode(self):
        """default_mode 유효값 검증."""
        from selfhealing.settings.governance import GovernanceSettings

        # Valid modes
        for mode in ["NORMAL", "STRICT"]:
            settings = GovernanceSettings(default_mode=mode)
            assert settings.default_mode == mode

        # Invalid mode
        with pytest.raises(ValidationError):
            GovernanceSettings(default_mode="INVALID")

    def test_validation_threshold_range(self):
        """threshold_operator 범위 (0.01-1.0) 검증."""
        from selfhealing.settings.governance import GovernanceSettings

        with pytest.raises(ValidationError):
            GovernanceSettings(threshold_operator=0.0)

        with pytest.raises(ValidationError):
            GovernanceSettings(threshold_operator=1.1)

    def test_singleton_pattern(self):
        """싱글톤 패턴이 동작하는지 검증."""
        from selfhealing.settings.governance import get_governance_settings

        settings1 = get_governance_settings()
        settings2 = get_governance_settings()

        assert settings1 is settings2


class TestChaosSettings:
    """Tests for ChaosSettings."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        from selfhealing.settings.chaos import reset_chaos_settings
        reset_chaos_settings()
        yield
        reset_chaos_settings()

    def test_default_values(self):
        """기본값이 core/config.py:ChaosConfig와 일치하는지 검증."""
        from selfhealing.settings.chaos import ChaosSettings

        settings = ChaosSettings()

        assert settings.enabled is False  # Safety: disabled by default
        assert settings.max_blast_radius == 0.10
        assert settings.max_failure_rate == 0.20
        assert settings.auto_rollback_enabled is True
        assert settings.dry_run_default is True

    def test_env_override(self, monkeypatch):
        """환경변수로 값을 오버라이드할 수 있는지 검증."""
        from selfhealing.settings.chaos import ChaosSettings

        monkeypatch.setenv("SELFHEALING_CHAOS_MAX_BLAST_RADIUS", "0.20")

        settings = ChaosSettings()

        assert settings.max_blast_radius == 0.20

    def test_validation_blast_radius_range(self):
        """max_blast_radius 범위 (0.0-0.5) 검증."""
        from selfhealing.settings.chaos import ChaosSettings

        with pytest.raises(ValidationError):
            ChaosSettings(max_blast_radius=-0.1)

        with pytest.raises(ValidationError):
            ChaosSettings(max_blast_radius=0.6)

    def test_singleton_pattern(self):
        """싱글톤 패턴이 동작하는지 검증."""
        from selfhealing.settings.chaos import get_chaos_settings

        settings1 = get_chaos_settings()
        settings2 = get_chaos_settings()

        assert settings1 is settings2
