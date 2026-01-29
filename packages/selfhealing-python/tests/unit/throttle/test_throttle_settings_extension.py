"""
ThrottleSettings 확장 필드 테스트.

테스트 대상:
1. Emergency Level 배율 필드 (emergency_level_*_multiplier)
2. CB 연동 설정 필드 (cb_open_limit_percent, cb_half_open_limit_percent)
3. Recovery Dampening 설정 필드
4. Safe-Open 폴백 설정 필드
5. 헬퍼 메서드 (get_emergency_level_multipliers, get_recovery_steps)
"""

import os
import pytest


class TestThrottleSettingsEmergencyFields:
    """Emergency Level 배율 필드 테스트."""

    def test_default_emergency_level_multipliers(self):
        """기본 Emergency Level 배율 확인."""
        from selfhealing.settings.throttle import ThrottleSettings, reset_throttle_settings

        reset_throttle_settings()

        settings = ThrottleSettings()

        assert settings.emergency_level_0_multiplier == 1.0
        assert settings.emergency_level_1_multiplier == 0.8
        assert settings.emergency_level_2_multiplier == 0.5
        assert settings.emergency_level_3_multiplier == 0.0

    def test_get_emergency_level_multipliers_dict(self):
        """get_emergency_level_multipliers() 딕셔너리 반환 테스트."""
        from selfhealing.settings.throttle import ThrottleSettings, reset_throttle_settings

        reset_throttle_settings()

        settings = ThrottleSettings()
        multipliers = settings.get_emergency_level_multipliers()

        assert isinstance(multipliers, dict)
        assert multipliers[0] == 1.0
        assert multipliers[1] == 0.8
        assert multipliers[2] == 0.5
        assert multipliers[3] == 0.0

    def test_emergency_multipliers_from_env(self, monkeypatch):
        """환경변수로 Emergency 배율 설정 테스트."""
        from selfhealing.settings.throttle import ThrottleSettings, reset_throttle_settings

        reset_throttle_settings()

        monkeypatch.setenv("SELFHEALING_THROTTLE_EMERGENCY_LEVEL_1_MULTIPLIER", "0.7")
        monkeypatch.setenv("SELFHEALING_THROTTLE_EMERGENCY_LEVEL_2_MULTIPLIER", "0.4")

        settings = ThrottleSettings()

        assert settings.emergency_level_1_multiplier == 0.7
        assert settings.emergency_level_2_multiplier == 0.4


class TestThrottleSettingsCBFields:
    """CB 연동 설정 필드 테스트."""

    def test_default_cb_limit_percents(self):
        """기본 CB limit 비율 확인."""
        from selfhealing.settings.throttle import ThrottleSettings, reset_throttle_settings

        reset_throttle_settings()

        settings = ThrottleSettings()

        assert settings.cb_open_limit_percent == 0.0
        assert settings.cb_half_open_limit_percent == 0.5

    def test_cb_limit_from_env(self, monkeypatch):
        """환경변수로 CB limit 비율 설정 테스트."""
        from selfhealing.settings.throttle import ThrottleSettings, reset_throttle_settings

        reset_throttle_settings()

        monkeypatch.setenv("SELFHEALING_THROTTLE_CB_OPEN_LIMIT_PERCENT", "0.1")
        monkeypatch.setenv("SELFHEALING_THROTTLE_CB_HALF_OPEN_LIMIT_PERCENT", "0.6")

        settings = ThrottleSettings()

        assert settings.cb_open_limit_percent == 0.1
        assert settings.cb_half_open_limit_percent == 0.6


class TestThrottleSettingsRecoveryDampeningFields:
    """Recovery Dampening 설정 필드 테스트."""

    def test_default_recovery_dampening_settings(self):
        """기본 Recovery Dampening 설정 확인."""
        from selfhealing.settings.throttle import ThrottleSettings, reset_throttle_settings

        reset_throttle_settings()

        settings = ThrottleSettings()

        assert settings.recovery_dampening_enabled is True
        assert settings.recovery_step_1_percent == 0.8
        assert settings.recovery_step_2_percent == 0.9
        assert settings.recovery_step_3_percent == 1.0
        assert settings.recovery_step_interval_seconds == 30.0

    def test_get_recovery_steps_tuple(self):
        """get_recovery_steps() 튜플 반환 테스트."""
        from selfhealing.settings.throttle import ThrottleSettings, reset_throttle_settings

        reset_throttle_settings()

        settings = ThrottleSettings()
        steps = settings.get_recovery_steps()

        assert isinstance(steps, tuple)
        assert steps == (0.8, 0.9, 1.0)

    def test_recovery_steps_from_env(self, monkeypatch):
        """환경변수로 Recovery 단계 설정 테스트."""
        from selfhealing.settings.throttle import ThrottleSettings, reset_throttle_settings

        reset_throttle_settings()

        monkeypatch.setenv("SELFHEALING_THROTTLE_RECOVERY_STEP_1_PERCENT", "0.6")
        monkeypatch.setenv("SELFHEALING_THROTTLE_RECOVERY_STEP_INTERVAL_SECONDS", "60.0")

        settings = ThrottleSettings()

        assert settings.recovery_step_1_percent == 0.6
        assert settings.recovery_step_interval_seconds == 60.0


class TestThrottleSettingsSafeOpenFields:
    """Safe-Open 폴백 설정 필드 테스트."""

    def test_default_safe_open_settings(self):
        """기본 Safe-Open 설정 확인."""
        from selfhealing.settings.throttle import ThrottleSettings, reset_throttle_settings

        reset_throttle_settings()

        settings = ThrottleSettings()

        assert settings.safe_open_fallback_enabled is True
        assert settings.static_safe_limit_percent == 0.5
        assert "throttle:last_safe_limit:" in settings.redis_last_safe_limit_key_pattern

    def test_safe_open_from_env(self, monkeypatch):
        """환경변수로 Safe-Open 설정 테스트."""
        from selfhealing.settings.throttle import ThrottleSettings, reset_throttle_settings

        reset_throttle_settings()

        monkeypatch.setenv("SELFHEALING_THROTTLE_SAFE_OPEN_FALLBACK_ENABLED", "false")
        monkeypatch.setenv("SELFHEALING_THROTTLE_STATIC_SAFE_LIMIT_PERCENT", "0.3")

        settings = ThrottleSettings()

        assert settings.safe_open_fallback_enabled is False
        assert settings.static_safe_limit_percent == 0.3


class TestThrottleSettingsEventIntegrationFields:
    """EventBus 연동 설정 필드 테스트."""

    def test_default_event_integration_settings(self):
        """기본 EventBus 연동 설정 확인."""
        from selfhealing.settings.throttle import ThrottleSettings, reset_throttle_settings

        reset_throttle_settings()

        settings = ThrottleSettings()

        assert settings.enable_event_integration is True
        assert settings.sync_on_startup is True
        assert settings.sync_callback_enabled is True

    def test_gradient_freeze_setting(self):
        """Gradient Freeze 설정 확인."""
        from selfhealing.settings.throttle import ThrottleSettings, reset_throttle_settings

        reset_throttle_settings()

        settings = ThrottleSettings()

        assert settings.gradient_freeze_on_level_3 is True

    def test_full_stop_setting(self):
        """Full Stop 설정 확인."""
        from selfhealing.settings.throttle import ThrottleSettings, reset_throttle_settings

        reset_throttle_settings()

        settings = ThrottleSettings()

        assert settings.full_stop_conditions_enabled is True
