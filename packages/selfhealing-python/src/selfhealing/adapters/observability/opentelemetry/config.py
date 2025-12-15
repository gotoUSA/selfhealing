"""
OpenTelemetry Adapter Configuration

Provides a minimal, user-friendly configuration interface.
Users should not need to understand OpenTelemetry internals.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class OpenTelemetryConfig:
    """
    Configuration for OpenTelemetry adapter.

    This configuration is intentionally minimal to hide OTel complexity.
    Users only need to understand these high-level options.

    Attributes:
        enabled: Master switch for OpenTelemetry export. Default: False
        service_name: Service identifier for telemetry data.
        environment: Deployment environment (production, staging, etc.)
        decision_span_enabled: Enable coarse-grained decision cycle spans.
        event_export_enabled: Enable structured event export.
        endpoint: OTLP exporter endpoint (optional, uses SDK defaults if not set)

    Example:
        config = OpenTelemetryConfig(
            enabled=True,
            service_name="my-payment-service",
            environment="production",
        )
    """

    # ==========================================================================
    # Core Settings
    # ==========================================================================

    enabled: bool = False
    """Master switch. Adapter is NO-OP when False."""

    service_name: str = "selfhealing-service"
    """Service name for OTel resource identification."""

    environment: str = "development"
    """Deployment environment tag."""

    # ==========================================================================
    # Feature Toggles
    # ==========================================================================

    decision_span_enabled: bool = True
    """
    Enable coarse-grained decision cycle spans.
    A decision span represents a self-healing decision window (seconds to minutes),
    NOT individual requests.
    """

    event_export_enabled: bool = True
    """Enable structured event export for state transitions and decisions."""

    # ==========================================================================
    # Export Configuration
    # ==========================================================================

    endpoint: Optional[str] = None
    """
    OTLP exporter endpoint. If None, uses OpenTelemetry SDK defaults
    (environment variables like OTEL_EXPORTER_OTLP_ENDPOINT).
    """

    export_timeout_seconds: int = 30
    """Timeout for exporting telemetry data."""

    # ==========================================================================
    # Event Filtering
    # ==========================================================================

    export_circuit_breaker_events: bool = True
    """Export circuit breaker state transitions."""

    export_retry_events: bool = True
    """Export retry attempts and exhaustion."""

    export_dlq_events: bool = True
    """Export DLQ enqueue/replay events."""

    export_rate_limit_events: bool = True
    """Export rate limit trigger events."""

    export_slo_events: bool = True
    """Export SLO threshold approaching/breached events."""

    export_policy_events: bool = True
    """Export policy evaluation outcomes."""

    # ==========================================================================
    # Resource Attributes
    # ==========================================================================

    additional_resource_attributes: dict = field(default_factory=dict)
    """Additional OTel resource attributes to include."""

    @classmethod
    def from_env(cls) -> "OpenTelemetryConfig":
        """
        Load configuration from environment variables.

        Environment Variables:
            SELFHEALING_OTEL_ENABLED: "true" to enable
            SELFHEALING_OTEL_SERVICE_NAME: Service name
            SELFHEALING_OTEL_ENVIRONMENT: Environment name
            SELFHEALING_OTEL_ENDPOINT: OTLP endpoint
            SELFHEALING_OTEL_DECISION_SPAN_ENABLED: "true"/"false"
            SELFHEALING_OTEL_EVENT_EXPORT_ENABLED: "true"/"false"

        Returns:
            OpenTelemetryConfig instance
        """

        def _get_bool(key: str, default: bool) -> bool:
            value = os.environ.get(key, "").lower()
            if value in ("true", "1", "yes"):
                return True
            elif value in ("false", "0", "no"):
                return False
            return default

        return cls(
            enabled=_get_bool("SELFHEALING_OTEL_ENABLED", False),
            service_name=os.environ.get("SELFHEALING_OTEL_SERVICE_NAME", "selfhealing-service"),
            environment=os.environ.get("SELFHEALING_OTEL_ENVIRONMENT", "development"),
            endpoint=os.environ.get("SELFHEALING_OTEL_ENDPOINT"),
            decision_span_enabled=_get_bool("SELFHEALING_OTEL_DECISION_SPAN_ENABLED", True),
            event_export_enabled=_get_bool("SELFHEALING_OTEL_EVENT_EXPORT_ENABLED", True),
            export_circuit_breaker_events=_get_bool("SELFHEALING_OTEL_EXPORT_CB_EVENTS", True),
            export_retry_events=_get_bool("SELFHEALING_OTEL_EXPORT_RETRY_EVENTS", True),
            export_dlq_events=_get_bool("SELFHEALING_OTEL_EXPORT_DLQ_EVENTS", True),
            export_rate_limit_events=_get_bool("SELFHEALING_OTEL_EXPORT_RATELIMIT_EVENTS", True),
            export_slo_events=_get_bool("SELFHEALING_OTEL_EXPORT_SLO_EVENTS", True),
            export_policy_events=_get_bool("SELFHEALING_OTEL_EXPORT_POLICY_EVENTS", True),
        )

    @classmethod
    def from_django_settings(cls) -> "OpenTelemetryConfig":
        """
        Load configuration from Django settings.

        Expected Django settings structure:
            SELFHEALING_OPENTELEMETRY = {
                "enabled": True,
                "service_name": "my-service",
                "environment": "production",
                ...
            }

        Returns:
            OpenTelemetryConfig instance

        Notes:
            Catches both ImportError (Django not installed) and
            ImproperlyConfigured (Django installed but not configured).
            This ensures graceful fallback to environment variables in
            non-Django contexts (tests, CLI tools, standalone scripts).
        """
        try:
            from django.conf import settings
            from django.core.exceptions import ImproperlyConfigured

            config_dict = getattr(settings, "SELFHEALING_OPENTELEMETRY", {})
            if not config_dict:
                return cls()

            return cls(
                enabled=config_dict.get("enabled", False),
                service_name=config_dict.get("service_name", "selfhealing-service"),
                environment=config_dict.get("environment", "development"),
                endpoint=config_dict.get("endpoint"),
                decision_span_enabled=config_dict.get("decision_span_enabled", True),
                event_export_enabled=config_dict.get("event_export_enabled", True),
                export_circuit_breaker_events=config_dict.get("export_circuit_breaker_events", True),
                export_retry_events=config_dict.get("export_retry_events", True),
                export_dlq_events=config_dict.get("export_dlq_events", True),
                export_rate_limit_events=config_dict.get("export_rate_limit_events", True),
                export_slo_events=config_dict.get("export_slo_events", True),
                export_policy_events=config_dict.get("export_policy_events", True),
                additional_resource_attributes=config_dict.get("additional_resource_attributes", {}),
            )
        except (ImportError, ImproperlyConfigured):
            # ImportError: Django not installed
            # ImproperlyConfigured: Django installed but settings not configured
            return cls.from_env()

    def should_export_event(self, event_type: str) -> bool:
        """
        Check if a specific event type should be exported.

        Args:
            event_type: Event type category

        Returns:
            True if the event should be exported
        """
        if not self.enabled or not self.event_export_enabled:
            return False

        event_type_lower = event_type.lower()

        if "circuit" in event_type_lower:
            return self.export_circuit_breaker_events
        elif "retry" in event_type_lower:
            return self.export_retry_events
        elif "dlq" in event_type_lower:
            return self.export_dlq_events
        elif "rate" in event_type_lower or "limit" in event_type_lower:
            return self.export_rate_limit_events
        elif "slo" in event_type_lower or "threshold" in event_type_lower:
            return self.export_slo_events
        elif "policy" in event_type_lower or "decision" in event_type_lower:
            return self.export_policy_events

        # Default: export unknown event types
        return True
