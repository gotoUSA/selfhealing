"""
Tests for OpenTelemetry Settings.
"""

import os
import pytest
from unittest.mock import patch


class TestOpenTelemetrySettings:
    """Tests for OpenTelemetrySettings Pydantic class."""

    def test_default_settings_otel_disabled(self):
        """Test that OTEL is disabled by default."""
        from selfhealing.settings.observability import (
            OpenTelemetrySettings,
            reset_otel_settings,
        )

        reset_otel_settings()

        with patch.dict(os.environ, {}, clear=True):
            settings = OpenTelemetrySettings()

        assert settings.enabled is False
        assert settings.service_name == "selfhealing"

    def test_settings_from_environment_variables(self):
        """Test loading settings from environment variables."""
        from selfhealing.settings.observability import (
            OpenTelemetrySettings,
            reset_otel_settings,
        )

        reset_otel_settings()

        env_vars = {
            "OTEL_ENABLED": "true",
            "OTEL_SERVICE_NAME": "test-service",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4317",
            "OTEL_TRACES_SAMPLER": "always_on",
            "OTEL_TRACES_SAMPLER_ARG": "0.5",
            "OTEL_ADAPTIVE_SAMPLING_ENABLED": "false",
            "OTEL_SLA_CRITICAL_MS": "1000",
            "OTEL_EXCLUDED_URLS": "/health,/metrics,/ready",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            settings = OpenTelemetrySettings()

        assert settings.enabled is True
        assert settings.service_name == "test-service"
        assert settings.exporter_otlp_endpoint == "http://collector:4317"
        assert settings.traces_sampler == "always_on"
        assert settings.traces_sampler_arg == 0.5
        assert settings.adaptive_sampling_enabled is False
        assert settings.sla_critical_threshold_ms == 1000

    def test_get_excluded_urls_list(self):
        """Test parsing excluded URLs into a list."""
        from selfhealing.settings.observability import (
            OpenTelemetrySettings,
            reset_otel_settings,
        )

        reset_otel_settings()

        env_vars = {
            "OTEL_EXCLUDED_URLS": "/health, /metrics , /ready",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            settings = OpenTelemetrySettings()

        urls = settings.get_excluded_urls_list()
        assert urls == ["/health", "/metrics", "/ready"]

    def test_get_excluded_urls_list_empty(self):
        """Test empty excluded URLs."""
        from selfhealing.settings.observability import (
            OpenTelemetrySettings,
            reset_otel_settings,
        )

        reset_otel_settings()

        env_vars = {"OTEL_EXCLUDED_URLS": ""}

        with patch.dict(os.environ, env_vars, clear=True):
            settings = OpenTelemetrySettings()

        assert settings.get_excluded_urls_list() == []

    def test_get_resource_attributes_dict(self):
        """Test parsing resource attributes into a dictionary."""
        from selfhealing.settings.observability import (
            OpenTelemetrySettings,
            reset_otel_settings,
        )

        reset_otel_settings()

        env_vars = {
            "OTEL_RESOURCE_ATTRIBUTES": "deployment.environment=prod,service.version=1.0.0",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            settings = OpenTelemetrySettings()

        attrs = settings.get_resource_attributes_dict()
        assert attrs == {
            "deployment.environment": "prod",
            "service.version": "1.0.0",
        }

    def test_get_resource_attributes_dict_empty(self):
        """Test empty resource attributes."""
        from selfhealing.settings.observability import (
            OpenTelemetrySettings,
            reset_otel_settings,
        )

        reset_otel_settings()

        env_vars = {"OTEL_RESOURCE_ATTRIBUTES": ""}

        with patch.dict(os.environ, env_vars, clear=True):
            settings = OpenTelemetrySettings()

        assert settings.get_resource_attributes_dict() == {}

    def test_traces_sampler_arg_validation(self):
        """Test that sampler arg is clamped to 0.0-1.0."""
        from selfhealing.settings.observability import (
            OpenTelemetrySettings,
            reset_otel_settings,
        )
        from pydantic import ValidationError

        reset_otel_settings()

        # Valid value
        env_vars = {"OTEL_TRACES_SAMPLER_ARG": "0.5"}
        with patch.dict(os.environ, env_vars, clear=True):
            settings = OpenTelemetrySettings()
            assert settings.traces_sampler_arg == 0.5

        # Invalid value (>1.0) should raise validation error
        env_vars = {"OTEL_TRACES_SAMPLER_ARG": "1.5"}
        with patch.dict(os.environ, env_vars, clear=True):
            with pytest.raises(ValidationError):
                OpenTelemetrySettings()

    def test_singleton_pattern(self):
        """Test that get_otel_settings returns cached instance."""
        from selfhealing.settings.observability import (
            get_otel_settings,
            reset_otel_settings,
        )

        reset_otel_settings()

        settings1 = get_otel_settings()
        settings2 = get_otel_settings()

        assert settings1 is settings2

    def test_reset_clears_singleton(self):
        """Test that reset_otel_settings clears the cached instance."""
        from selfhealing.settings.observability import (
            get_otel_settings,
            reset_otel_settings,
        )

        settings1 = get_otel_settings()
        reset_otel_settings()
        settings2 = get_otel_settings()

        assert settings1 is not settings2
