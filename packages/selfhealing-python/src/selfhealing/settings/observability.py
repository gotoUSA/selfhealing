"""
OpenTelemetry Settings Configuration.

Provides environment-based configuration for OpenTelemetry SDK.
Supports adaptive sampling based on EmergencyLevel.
"""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings

from selfhealing.settings.base import COMMON_SETTINGS_CONFIG


class OpenTelemetrySettings(BaseSettings):
    """
    OpenTelemetry SDK configuration via environment variables.

    All settings follow OTEL_* naming convention for consistency
    with OpenTelemetry SDK environment variable standards.
    """

    model_config = COMMON_SETTINGS_CONFIG

    # Core Settings
    enabled: bool = Field(
        default=False,
        validation_alias="OTEL_ENABLED",
        description="Enable/disable OpenTelemetry SDK",
    )

    service_name: str = Field(
        default="selfhealing",
        validation_alias="OTEL_SERVICE_NAME",
        description="Service name for telemetry identification",
    )

    # Exporter Settings
    exporter_otlp_endpoint: str = Field(
        default="http://localhost:4317",
        validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT",
        description="OTLP Collector endpoint URL",
    )

    exporter_otlp_timeout_ms: int = Field(
        default=5000,
        validation_alias="OTEL_EXPORTER_OTLP_TIMEOUT",
        description="OTLP exporter timeout in milliseconds",
    )

    # Sampling Settings
    traces_sampler: Literal[
        "always_on",
        "always_off",
        "traceidratio",
        "parentbased_always_on",
        "parentbased_always_off",
        "parentbased_traceidratio",
    ] = Field(
        default="parentbased_traceidratio",
        validation_alias="OTEL_TRACES_SAMPLER",
        description="Sampling strategy",
    )

    traces_sampler_arg: float = Field(
        default=0.01,
        validation_alias="OTEL_TRACES_SAMPLER_ARG",
        ge=0.0,
        le=1.0,
        description="Sampling ratio (0.0-1.0). Default 1% (0.01)",
    )

    # Adaptive Sampling Thresholds
    adaptive_sampling_enabled: bool = Field(
        default=True,
        validation_alias="OTEL_ADAPTIVE_SAMPLING_ENABLED",
        description="Enable emergency-level based adaptive sampling",
    )

    sla_critical_threshold_ms: int = Field(
        default=500,
        validation_alias="OTEL_SLA_CRITICAL_MS",
        description="SLA critical threshold in milliseconds. Exceeding triggers 100% sampling",
    )

    # Resource Attributes
    resource_attributes: str = Field(
        default="",
        validation_alias="OTEL_RESOURCE_ATTRIBUTES",
        description="Comma-separated key=value pairs for resource attributes",
    )

    # Django Instrumentation Settings
    django_instrument_enabled: bool = Field(
        default=True,
        validation_alias="OTEL_DJANGO_INSTRUMENT_ENABLED",
        description="Enable automatic Django request/response instrumentation",
    )

    # Requests Instrumentation Settings
    requests_instrument_enabled: bool = Field(
        default=True,
        validation_alias="OTEL_REQUESTS_INSTRUMENT_ENABLED",
        description="Enable automatic HTTP client (requests library) instrumentation",
    )

    # Celery Instrumentation Settings
    celery_instrument_enabled: bool = Field(
        default=True,
        validation_alias="OTEL_CELERY_INSTRUMENT_ENABLED",
        description="Enable automatic Celery task instrumentation",
    )

    # Excluded URLs (for health checks, etc.)
    excluded_urls: str = Field(
        default="/health,/health/,/health/ready,/health/live,/health/l3,/metrics",
        validation_alias="OTEL_EXCLUDED_URLS",
        description="Comma-separated URL paths to exclude from tracing",
    )

    def get_excluded_urls_list(self) -> list[str]:
        """Get excluded URLs as a list."""
        if not self.excluded_urls:
            return []
        return [url.strip() for url in self.excluded_urls.split(",") if url.strip()]

    def get_resource_attributes_dict(self) -> dict[str, str]:
        """Parse resource_attributes string into a dictionary."""
        if not self.resource_attributes:
            return {}

        result = {}
        for pair in self.resource_attributes.split(","):
            if "=" in pair:
                key, value = pair.split("=", 1)
                result[key.strip()] = value.strip()
        return result


# Global settings instance (cached)
_settings_instance: OpenTelemetrySettings | None = None


def get_otel_settings() -> OpenTelemetrySettings:
    """
    Get the OpenTelemetry settings singleton.

    Returns:
        Cached OpenTelemetrySettings instance
    """
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = OpenTelemetrySettings()
    return _settings_instance


def reset_otel_settings() -> None:
    """
    Reset the OpenTelemetry settings singleton.

    Used primarily for testing to ensure fresh settings.
    """
    global _settings_instance
    _settings_instance = None
