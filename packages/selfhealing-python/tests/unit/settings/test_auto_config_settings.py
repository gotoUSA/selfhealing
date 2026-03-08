"""Unit tests for selfhealing.settings.auto_config module (320).

Tests AutoConfigSettings defaults, environment variable overrides,
and get/reset singleton lifecycle.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from selfhealing.settings.auto_config import (
    AutoConfigSettings,
    get_auto_config_settings,
    reset_auto_config_settings,
)

# =========================================================================
# Contract Tests — design doc defaults
# =========================================================================


class TestAutoConfigSettingsContract:
    """320 설계 문서에 정의된 AutoConfigSettings 기본값 계약 검증."""

    def test_middleware_default_is_true(self):
        """middleware 기본값: True."""
        settings = AutoConfigSettings()
        assert settings.middleware is True

    def test_exception_handler_default_is_true(self):
        """exception_handler 기본값: True."""
        settings = AutoConfigSettings()
        assert settings.exception_handler is True

    def test_otel_default_is_true(self):
        """otel 기본값: True."""
        settings = AutoConfigSettings()
        assert settings.otel is True

    def test_celery_signal_warning_default_is_true(self):
        """celery_signal_warning 기본값: True."""
        settings = AutoConfigSettings()
        assert settings.celery_signal_warning is True

    def test_env_prefix_is_selfhealing_auto(self):
        """환경변수 접두사는 SELFHEALING_AUTO_이다."""
        assert AutoConfigSettings.model_config["env_prefix"] == "SELFHEALING_AUTO_"

    def test_has_exactly_four_fields(self):
        """AutoConfigSettings는 4개 필드를 가진다."""
        fields = AutoConfigSettings.model_fields
        assert len(fields) == 4
        assert set(fields.keys()) == {
            "middleware",
            "exception_handler",
            "otel",
            "celery_signal_warning",
        }


# =========================================================================
# Behavior Tests — environment variable overrides
# =========================================================================


class TestAutoConfigSettingsEnvOverrideBehavior:
    """AutoConfigSettings 환경변수 오버라이딩 동작 검증."""

    def test_middleware_disabled_via_env(self):
        """SELFHEALING_AUTO_MIDDLEWARE=false로 미들웨어 비활성화."""
        with patch.dict("os.environ", {"SELFHEALING_AUTO_MIDDLEWARE": "false"}):
            settings = AutoConfigSettings()
            assert settings.middleware is False

    def test_otel_disabled_via_env(self):
        """SELFHEALING_AUTO_OTEL=false로 OTEL 비활성화."""
        with patch.dict("os.environ", {"SELFHEALING_AUTO_OTEL": "false"}):
            settings = AutoConfigSettings()
            assert settings.otel is False

    def test_exception_handler_disabled_via_env(self):
        """SELFHEALING_AUTO_EXCEPTION_HANDLER=false로 예외 핸들러 비활성화."""
        with patch.dict("os.environ", {"SELFHEALING_AUTO_EXCEPTION_HANDLER": "false"}):
            settings = AutoConfigSettings()
            assert settings.exception_handler is False

    def test_celery_signal_warning_disabled_via_env(self):
        """SELFHEALING_AUTO_CELERY_SIGNAL_WARNING=false로 경고 비활성화."""
        with patch.dict(
            "os.environ", {"SELFHEALING_AUTO_CELERY_SIGNAL_WARNING": "false"}
        ):
            settings = AutoConfigSettings()
            assert settings.celery_signal_warning is False


# =========================================================================
# Behavior Tests — singleton lifecycle
# =========================================================================


class TestAutoConfigSettingsSingletonBehavior:
    """get_auto_config_settings / reset_auto_config_settings 싱글톤 동작 검증."""

    @pytest.fixture(autouse=True)
    def _reset_singleton(self):
        """각 테스트 후 싱글톤 캐시를 리셋한다."""
        reset_auto_config_settings()
        yield
        reset_auto_config_settings()

    def test_get_returns_same_instance(self):
        """get_auto_config_settings()는 동일 인스턴스를 반환한다."""
        first = get_auto_config_settings()
        second = get_auto_config_settings()
        assert first is second

    def test_reset_clears_cached_instance(self):
        """reset 후 새 인스턴스가 생성된다."""
        first = get_auto_config_settings()
        reset_auto_config_settings()
        second = get_auto_config_settings()
        assert first is not second
