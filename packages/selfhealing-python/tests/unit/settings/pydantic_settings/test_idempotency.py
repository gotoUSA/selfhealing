"""
Tests for IdempotencySettings.
"""

import pytest
from pydantic import ValidationError


class TestIdempotencySettings:
    """Tests for IdempotencySettings."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        from selfhealing.settings.idempotency import reset_idempotency_settings
        reset_idempotency_settings()
        yield
        reset_idempotency_settings()

    def test_default_values(self):
        """기본값이 core/config.py:IdempotencyConfig와 일치하는지 검증."""
        from selfhealing.settings.idempotency import IdempotencySettings

        settings = IdempotencySettings()

        assert settings.default_cache_ttl == 60
        assert settings.extended_cache_ttl == 300
        assert settings.short_cache_ttl == 60
        assert settings.clock_skew_tolerance_seconds == 5.0

    def test_env_override(self, monkeypatch):
        """환경변수로 값을 오버라이드할 수 있는지 검증."""
        from selfhealing.settings.idempotency import IdempotencySettings

        monkeypatch.setenv("SELFHEALING_IDEMPOTENCY_DEFAULT_CACHE_TTL", "120")

        settings = IdempotencySettings()

        assert settings.default_cache_ttl == 120

    def test_validation_cache_ttl_range(self):
        """default_cache_ttl 범위 (1-3600) 검증."""
        from selfhealing.settings.idempotency import IdempotencySettings

        with pytest.raises(ValidationError):
            IdempotencySettings(default_cache_ttl=0)

        with pytest.raises(ValidationError):
            IdempotencySettings(default_cache_ttl=3601)

    def test_singleton_pattern(self):
        """싱글톤 패턴이 동작하는지 검증."""
        from selfhealing.settings.idempotency import get_idempotency_settings

        settings1 = get_idempotency_settings()
        settings2 = get_idempotency_settings()

        assert settings1 is settings2
