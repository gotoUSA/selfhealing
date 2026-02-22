"""
Tests for LoggingSettings and MetricsSettings.
"""

import pytest
from pydantic import ValidationError


class TestLoggingSettings:
    """Tests for LoggingSettings."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        from selfhealing.settings.logging_config import reset_logging_settings
        reset_logging_settings()
        yield
        reset_logging_settings()

    def test_default_values(self):
        """기본값이 core/config.py:LoggingConfig와 일치하는지 검증."""
        from selfhealing.settings.logging_config import LoggingSettings

        settings = LoggingSettings()

        assert settings.dlq_log_level == "INFO"
        assert settings.circuit_breaker_log_level == "INFO"
        assert settings.forensic_log_level == "DEBUG"
        assert settings.emergency_log_level == "WARNING"
        assert settings.include_timestamps is True
        assert settings.structured_json is True

    def test_env_override(self, monkeypatch):
        """환경변수로 값을 오버라이드할 수 있는지 검증."""
        from selfhealing.settings.logging_config import LoggingSettings

        monkeypatch.setenv("SELFHEALING_LOGGING_DLQ_LOG_LEVEL", "DEBUG")

        settings = LoggingSettings()

        assert settings.dlq_log_level == "DEBUG"

    def test_validation_log_level(self):
        """로그 레벨 유효값 검증."""
        from selfhealing.settings.logging_config import LoggingSettings

        # Valid levels
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            settings = LoggingSettings(dlq_log_level=level)
            assert settings.dlq_log_level == level

        # Invalid level
        with pytest.raises(ValidationError):
            LoggingSettings(dlq_log_level="INVALID")

    def test_singleton_pattern(self):
        """싱글톤 패턴이 동작하는지 검증."""
        from selfhealing.settings.logging_config import get_logging_settings

        settings1 = get_logging_settings()
        settings2 = get_logging_settings()

        assert settings1 is settings2


class TestMetricsSettings:
    """Tests for MetricsSettings."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        from selfhealing.settings.metrics import reset_metrics_settings
        reset_metrics_settings()
        yield
        reset_metrics_settings()

    def test_default_values(self):
        """기본값이 core/config.py:MetricsConfig와 일치하는지 검증."""
        from selfhealing.settings.metrics import MetricsSettings

        settings = MetricsSettings()

        assert settings.enabled is True
        assert settings.prefix == "selfhealing"
        assert settings.collection_interval == 60
        assert settings.export_prometheus is True
        assert settings.jitter_enabled is True
        assert settings.jitter_max_delay_seconds == 60.0

    def test_env_override(self, monkeypatch):
        """환경변수로 값을 오버라이드할 수 있는지 검증."""
        from selfhealing.settings.metrics import MetricsSettings

        monkeypatch.setenv("SELFHEALING_METRICS_COLLECTION_INTERVAL", "30")

        settings = MetricsSettings()

        assert settings.collection_interval == 30

    def test_validation_collection_interval_range(self):
        """collection_interval 범위 (1-3600) 검증."""
        from selfhealing.settings.metrics import MetricsSettings

        with pytest.raises(ValidationError):
            MetricsSettings(collection_interval=0)

        with pytest.raises(ValidationError):
            MetricsSettings(collection_interval=3601)

    def test_singleton_pattern(self):
        """싱글톤 패턴이 동작하는지 검증."""
        from selfhealing.settings.metrics import get_metrics_settings

        settings1 = get_metrics_settings()
        settings2 = get_metrics_settings()

        assert settings1 is settings2
