"""
Throttle SLA Notification Settings 단위 테스트.

대상: selfhealing/settings/throttle_sla_notification.py
- ThrottleSLANotificationSettings (Pydantic v2 BaseSettings)
- 싱글톤 get/reset 함수
- 환경변수 오버라이드
- 필드 유효성 검증
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


class TestThrottleSLANotificationSettingsDefaults:
    """기본값 테스트."""

    def setup_method(self):
        from selfhealing.settings.throttle_sla_notification import (
            reset_throttle_sla_notification_settings,
        )

        reset_throttle_sla_notification_settings()

    def teardown_method(self):
        from selfhealing.settings.throttle_sla_notification import (
            reset_throttle_sla_notification_settings,
        )

        reset_throttle_sla_notification_settings()

    def test_enabled_default_true(self):
        """enabled 기본값 True."""
        from selfhealing.settings.throttle_sla_notification import (
            get_throttle_sla_notification_settings,
        )

        assert get_throttle_sla_notification_settings().enabled is True

    def test_warning_enabled_default_true(self):
        """warning_enabled 기본값 True."""
        from selfhealing.settings.throttle_sla_notification import (
            get_throttle_sla_notification_settings,
        )

        assert get_throttle_sla_notification_settings().warning_enabled is True

    def test_critical_enabled_default_true(self):
        """critical_enabled 기본값 True."""
        from selfhealing.settings.throttle_sla_notification import (
            get_throttle_sla_notification_settings,
        )

        assert get_throttle_sla_notification_settings().critical_enabled is True

    def test_recovery_enabled_default_true(self):
        """recovery_enabled 기본값 True."""
        from selfhealing.settings.throttle_sla_notification import (
            get_throttle_sla_notification_settings,
        )

        assert get_throttle_sla_notification_settings().recovery_enabled is True

    def test_warning_cooldown_default_1800(self):
        """warning_cooldown_seconds 기본값 1800."""
        from selfhealing.settings.throttle_sla_notification import (
            get_throttle_sla_notification_settings,
        )

        assert get_throttle_sla_notification_settings().warning_cooldown_seconds == 1800

    def test_critical_cooldown_default_900(self):
        """critical_cooldown_seconds 기본값 900."""
        from selfhealing.settings.throttle_sla_notification import (
            get_throttle_sla_notification_settings,
        )

        assert get_throttle_sla_notification_settings().critical_cooldown_seconds == 900

    def test_redis_cooldown_enabled_default_true(self):
        """redis_cooldown_enabled 기본값 True."""
        from selfhealing.settings.throttle_sla_notification import (
            get_throttle_sla_notification_settings,
        )

        assert get_throttle_sla_notification_settings().redis_cooldown_enabled is True

    def test_warning_channels_default_none(self):
        """warning_channels 기본값 None."""
        from selfhealing.settings.throttle_sla_notification import (
            get_throttle_sla_notification_settings,
        )

        assert get_throttle_sla_notification_settings().warning_channels is None

    def test_critical_channels_default_none(self):
        """critical_channels 기본값 None."""
        from selfhealing.settings.throttle_sla_notification import (
            get_throttle_sla_notification_settings,
        )

        assert get_throttle_sla_notification_settings().critical_channels is None


class TestThrottleSLANotificationSettingsEnvOverride:
    """환경변수 오버라이드 테스트."""

    def test_disable_all(self):
        """전체 비활성화."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with patch.dict(
            os.environ,
            {
                "SELFHEALING_THROTTLE_SLA_NOTIFICATION_ENABLED": "false",
            },
        ):
            settings = ThrottleSLANotificationSettings()
            assert settings.enabled is False

    def test_disable_warning(self):
        """Warning만 비활성화."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with patch.dict(
            os.environ,
            {
                "SELFHEALING_THROTTLE_SLA_NOTIFICATION_WARNING_ENABLED": "false",
            },
        ):
            settings = ThrottleSLANotificationSettings()
            assert settings.warning_enabled is False
            assert settings.critical_enabled is True

    def test_disable_critical(self):
        """Critical만 비활성화."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with patch.dict(
            os.environ,
            {
                "SELFHEALING_THROTTLE_SLA_NOTIFICATION_CRITICAL_ENABLED": "false",
            },
        ):
            settings = ThrottleSLANotificationSettings()
            assert settings.critical_enabled is False
            assert settings.warning_enabled is True

    def test_disable_recovery(self):
        """Recovery만 비활성화."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with patch.dict(
            os.environ,
            {
                "SELFHEALING_THROTTLE_SLA_NOTIFICATION_RECOVERY_ENABLED": "false",
            },
        ):
            settings = ThrottleSLANotificationSettings()
            assert settings.recovery_enabled is False

    def test_override_warning_cooldown(self):
        """Warning 쿨다운 오버라이드."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with patch.dict(
            os.environ,
            {
                "SELFHEALING_THROTTLE_SLA_NOTIFICATION_WARNING_COOLDOWN_SECONDS": "3600",
            },
        ):
            settings = ThrottleSLANotificationSettings()
            assert settings.warning_cooldown_seconds == 3600

    def test_override_critical_cooldown(self):
        """Critical 쿨다운 오버라이드."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with patch.dict(
            os.environ,
            {
                "SELFHEALING_THROTTLE_SLA_NOTIFICATION_CRITICAL_COOLDOWN_SECONDS": "600",
            },
        ):
            settings = ThrottleSLANotificationSettings()
            assert settings.critical_cooldown_seconds == 600

    def test_disable_redis_cooldown(self):
        """Redis 쿨다운 비활성화."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with patch.dict(
            os.environ,
            {
                "SELFHEALING_THROTTLE_SLA_NOTIFICATION_REDIS_COOLDOWN_ENABLED": "false",
            },
        ):
            settings = ThrottleSLANotificationSettings()
            assert settings.redis_cooldown_enabled is False

    def test_multiple_overrides(self):
        """여러 값 동시 오버라이드."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with patch.dict(
            os.environ,
            {
                "SELFHEALING_THROTTLE_SLA_NOTIFICATION_ENABLED": "false",
                "SELFHEALING_THROTTLE_SLA_NOTIFICATION_WARNING_COOLDOWN_SECONDS": "3600",
                "SELFHEALING_THROTTLE_SLA_NOTIFICATION_CRITICAL_COOLDOWN_SECONDS": "600",
            },
        ):
            settings = ThrottleSLANotificationSettings()
            assert settings.enabled is False
            assert settings.warning_cooldown_seconds == 3600
            assert settings.critical_cooldown_seconds == 600


class TestThrottleSLANotificationSettingsValidation:
    """필드 유효성 검증 테스트."""

    def test_warning_cooldown_min_60(self):
        """warning_cooldown_seconds 최소값 60."""
        from pydantic import ValidationError
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with patch.dict(
            os.environ,
            {
                "SELFHEALING_THROTTLE_SLA_NOTIFICATION_WARNING_COOLDOWN_SECONDS": "30",
            },
        ):
            with pytest.raises(ValidationError):
                ThrottleSLANotificationSettings()

    def test_warning_cooldown_max_7200(self):
        """warning_cooldown_seconds 최대값 7200."""
        from pydantic import ValidationError
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with patch.dict(
            os.environ,
            {
                "SELFHEALING_THROTTLE_SLA_NOTIFICATION_WARNING_COOLDOWN_SECONDS": "10000",
            },
        ):
            with pytest.raises(ValidationError):
                ThrottleSLANotificationSettings()

    def test_critical_cooldown_min_60(self):
        """critical_cooldown_seconds 최소값 60."""
        from pydantic import ValidationError
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with patch.dict(
            os.environ,
            {
                "SELFHEALING_THROTTLE_SLA_NOTIFICATION_CRITICAL_COOLDOWN_SECONDS": "10",
            },
        ):
            with pytest.raises(ValidationError):
                ThrottleSLANotificationSettings()

    def test_critical_cooldown_max_3600(self):
        """critical_cooldown_seconds 최대값 3600."""
        from pydantic import ValidationError
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with patch.dict(
            os.environ,
            {
                "SELFHEALING_THROTTLE_SLA_NOTIFICATION_CRITICAL_COOLDOWN_SECONDS": "5000",
            },
        ):
            with pytest.raises(ValidationError):
                ThrottleSLANotificationSettings()

    def test_warning_cooldown_at_boundary_60(self):
        """warning_cooldown_seconds=60 허용."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with patch.dict(
            os.environ,
            {
                "SELFHEALING_THROTTLE_SLA_NOTIFICATION_WARNING_COOLDOWN_SECONDS": "60",
            },
        ):
            settings = ThrottleSLANotificationSettings()
            assert settings.warning_cooldown_seconds == 60

    def test_warning_cooldown_at_boundary_7200(self):
        """warning_cooldown_seconds=7200 허용."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
        )

        with patch.dict(
            os.environ,
            {
                "SELFHEALING_THROTTLE_SLA_NOTIFICATION_WARNING_COOLDOWN_SECONDS": "7200",
            },
        ):
            settings = ThrottleSLANotificationSettings()
            assert settings.warning_cooldown_seconds == 7200


class TestThrottleSLANotificationSettingsSingleton:
    """싱글톤 패턴 테스트."""

    def setup_method(self):
        from selfhealing.settings.throttle_sla_notification import (
            reset_throttle_sla_notification_settings,
        )

        reset_throttle_sla_notification_settings()

    def teardown_method(self):
        from selfhealing.settings.throttle_sla_notification import (
            reset_throttle_sla_notification_settings,
        )

        reset_throttle_sla_notification_settings()

    def test_same_instance(self):
        """동일 인스턴스 반환."""
        from selfhealing.settings.throttle_sla_notification import (
            get_throttle_sla_notification_settings,
        )

        s1 = get_throttle_sla_notification_settings()
        s2 = get_throttle_sla_notification_settings()
        assert s1 is s2

    def test_reset_creates_new(self):
        """reset 후 새 인스턴스."""
        from selfhealing.settings.throttle_sla_notification import (
            get_throttle_sla_notification_settings,
            reset_throttle_sla_notification_settings,
        )

        s1 = get_throttle_sla_notification_settings()
        reset_throttle_sla_notification_settings()
        s2 = get_throttle_sla_notification_settings()
        assert s2 is not s1

    def test_type_check(self):
        """반환 타입 확인."""
        from selfhealing.settings.throttle_sla_notification import (
            ThrottleSLANotificationSettings,
            get_throttle_sla_notification_settings,
        )

        settings = get_throttle_sla_notification_settings()
        assert isinstance(settings, ThrottleSLANotificationSettings)
