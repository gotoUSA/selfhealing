"""
Throttle SLA Notification Settings 단위 테스트.

대상: selfhealing/settings/throttle_sla_notification.py
- ThrottleSLANotificationSettings Pydantic BaseSettings
- 환경변수 매핑, 기본값, 타입 변환
- get_throttle_sla_notification_settings() 싱글톤
- reset_throttle_sla_notification_settings()
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


# 환경변수 prefix 상수
SETTINGS_ENV_PREFIX = "SELFHEALING_THROTTLE_SLA_NOTIFICATION"


class TestThrottleSlaNotificationSettingsDefaults:
    """ThrottleSLANotificationSettings 기본값 테스트."""

    @pytest.fixture(autouse=True)
    def reset_settings(self):
        """테스트 전 싱글톤 캐시 초기화."""
        from selfhealing.settings.throttle_sla_notification import (
            reset_throttle_sla_notification_settings,
        )

        reset_throttle_sla_notification_settings()
        yield
        reset_throttle_sla_notification_settings()

    def test_default_enabled_true(self):
        """기본값 enabled=True."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        settings = ThrottleSLANotificationSettings()
        assert settings.enabled is True

    def test_default_warning_enabled_true(self):
        """기본값 warning_enabled=True."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        settings = ThrottleSLANotificationSettings()
        assert settings.warning_enabled is True

    def test_default_critical_enabled_true(self):
        """기본값 critical_enabled=True."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        settings = ThrottleSLANotificationSettings()
        assert settings.critical_enabled is True

    def test_default_recovery_enabled_true(self):
        """기본값 recovery_enabled=True."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        settings = ThrottleSLANotificationSettings()
        assert settings.recovery_enabled is True

    def test_default_warning_cooldown_seconds(self):
        """기본 warning_cooldown_seconds=1800."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        settings = ThrottleSLANotificationSettings()
        assert settings.warning_cooldown_seconds == 1800

    def test_default_critical_cooldown_seconds(self):
        """기본 critical_cooldown_seconds=900."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        settings = ThrottleSLANotificationSettings()
        assert settings.critical_cooldown_seconds == 900

    def test_default_redis_cooldown_enabled_true(self):
        """기본값 redis_cooldown_enabled=True."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        settings = ThrottleSLANotificationSettings()
        assert settings.redis_cooldown_enabled is True

    def test_default_warning_channels_none(self):
        """기본 warning_channels=None."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        settings = ThrottleSLANotificationSettings()
        assert settings.warning_channels is None

    def test_default_critical_channels_none(self):
        """기본 critical_channels=None."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        settings = ThrottleSLANotificationSettings()
        assert settings.critical_channels is None


class TestThrottleSlaNotificationSettingsEnvOverride:
    """환경변수 오버라이드 테스트."""

    @pytest.fixture(autouse=True)
    def reset_settings(self):
        """테스트 전 싱글톤 캐시 초기화."""
        from selfhealing.settings.throttle_sla_notification import (
            reset_throttle_sla_notification_settings,
        )

        reset_throttle_sla_notification_settings()
        yield
        reset_throttle_sla_notification_settings()

    def _make_env_key(self, field: str) -> str:
        """환경변수 키 생성 (대문자)."""
        return f"{SETTINGS_ENV_PREFIX}_{field.upper()}"

    def test_enabled_false_from_env(self):
        """ENABLED=false 환경변수."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with patch.dict(os.environ, {self._make_env_key("ENABLED"): "false"}):
            settings = ThrottleSLANotificationSettings()
            assert settings.enabled is False

    def test_warning_enabled_false_from_env(self):
        """WARNING_ENABLED=false 환경변수."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with patch.dict(os.environ, {self._make_env_key("WARNING_ENABLED"): "false"}):
            settings = ThrottleSLANotificationSettings()
            assert settings.warning_enabled is False

    def test_critical_enabled_false_from_env(self):
        """CRITICAL_ENABLED=false 환경변수."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with patch.dict(os.environ, {self._make_env_key("CRITICAL_ENABLED"): "false"}):
            settings = ThrottleSLANotificationSettings()
            assert settings.critical_enabled is False

    def test_recovery_enabled_false_from_env(self):
        """RECOVERY_ENABLED=false 환경변수."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with patch.dict(os.environ, {self._make_env_key("RECOVERY_ENABLED"): "false"}):
            settings = ThrottleSLANotificationSettings()
            assert settings.recovery_enabled is False

    def test_warning_cooldown_from_env(self):
        """WARNING_COOLDOWN_SECONDS 환경변수 정수 변환."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with patch.dict(os.environ, {self._make_env_key("WARNING_COOLDOWN_SECONDS"): "3600"}):
            settings = ThrottleSLANotificationSettings()
            assert settings.warning_cooldown_seconds == 3600

    def test_critical_cooldown_from_env(self):
        """CRITICAL_COOLDOWN_SECONDS 환경변수 정수 변환."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with patch.dict(os.environ, {self._make_env_key("CRITICAL_COOLDOWN_SECONDS"): "600"}):
            settings = ThrottleSLANotificationSettings()
            assert settings.critical_cooldown_seconds == 600

    def test_redis_cooldown_enabled_false_from_env(self):
        """REDIS_COOLDOWN_ENABLED=false 환경변수."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with patch.dict(os.environ, {self._make_env_key("REDIS_COOLDOWN_ENABLED"): "false"}):
            settings = ThrottleSLANotificationSettings()
            assert settings.redis_cooldown_enabled is False


class TestThrottleSlaNotificationSettingsSingleton:
    """싱글톤 패턴 테스트."""

    @pytest.fixture(autouse=True)
    def reset_settings(self):
        """테스트 전 싱글톤 캐시 초기화."""
        from selfhealing.settings.throttle_sla_notification import (
            reset_throttle_sla_notification_settings,
        )

        reset_throttle_sla_notification_settings()
        yield
        reset_throttle_sla_notification_settings()

    def test_get_settings_returns_singleton(self):
        """get_throttle_sla_notification_settings()는 동일 인스턴스 반환."""
        from selfhealing.settings.throttle_sla_notification import (
            get_throttle_sla_notification_settings,
        )

        settings1 = get_throttle_sla_notification_settings()
        settings2 = get_throttle_sla_notification_settings()
        assert settings1 is settings2

    def test_reset_creates_new_instance(self):
        """reset 후 새 인스턴스 생성."""
        from selfhealing.settings.throttle_sla_notification import (
            get_throttle_sla_notification_settings,
            reset_throttle_sla_notification_settings,
        )

        settings1 = get_throttle_sla_notification_settings()
        reset_throttle_sla_notification_settings()
        settings2 = get_throttle_sla_notification_settings()
        assert settings1 is not settings2


class TestThrottleSlaNotificationSettingsEnvPrefix:
    """환경변수 prefix 테스트."""

    def test_env_prefix_correct(self):
        """환경변수 prefix가 SELFHEALING_THROTTLE_SLA_NOTIFICATION_."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        model_config = ThrottleSLANotificationSettings.model_config
        assert model_config.get("env_prefix") == f"{SETTINGS_ENV_PREFIX}_"


class TestThrottleSlaNotificationSettingsValidation:
    """Pydantic 유효성 검증 테스트."""

    @pytest.fixture(autouse=True)
    def reset_settings(self):
        """테스트 전 싱글톤 캐시 초기화."""
        from selfhealing.settings.throttle_sla_notification import (
            reset_throttle_sla_notification_settings,
        )

        reset_throttle_sla_notification_settings()
        yield
        reset_throttle_sla_notification_settings()

    def test_warning_cooldown_minimum_60(self):
        """warning_cooldown_seconds 최소값 60."""
        from pydantic import ValidationError

        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with pytest.raises(ValidationError):
            ThrottleSLANotificationSettings(warning_cooldown_seconds=30)

    def test_warning_cooldown_maximum_7200(self):
        """warning_cooldown_seconds 최대값 7200."""
        from pydantic import ValidationError

        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with pytest.raises(ValidationError):
            ThrottleSLANotificationSettings(warning_cooldown_seconds=10000)

    def test_critical_cooldown_minimum_60(self):
        """critical_cooldown_seconds 최소값 60."""
        from pydantic import ValidationError

        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with pytest.raises(ValidationError):
            ThrottleSLANotificationSettings(critical_cooldown_seconds=30)

    def test_critical_cooldown_maximum_3600(self):
        """critical_cooldown_seconds 최대값 3600."""
        from pydantic import ValidationError

        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with pytest.raises(ValidationError):
            ThrottleSLANotificationSettings(critical_cooldown_seconds=5000)

    def test_warning_cooldown_within_range(self):
        """유효 범위 내 warning_cooldown_seconds."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        settings = ThrottleSLANotificationSettings(warning_cooldown_seconds=120)
        assert settings.warning_cooldown_seconds == 120

    def test_critical_cooldown_within_range(self):
        """유효 범위 내 critical_cooldown_seconds."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        settings = ThrottleSLANotificationSettings(critical_cooldown_seconds=120)
        assert settings.critical_cooldown_seconds == 120
