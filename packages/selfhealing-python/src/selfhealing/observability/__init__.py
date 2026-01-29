"""
OpenTelemetry SDK Initialization.

Provides TracerProvider setup, OTLP exporter configuration,
and compatibility layer with existing trace_id system.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

# Module-level state
_initialized: bool = False
_tracer_provider = None
_tracer = None
_requests_instrumented: bool = False
_celery_instrumented: bool = False


def _is_otel_available() -> bool:
    """Check if OpenTelemetry SDK packages are installed."""
    try:
        import opentelemetry.sdk.trace  # noqa: F401

        return True
    except ImportError:
        return False


def initialize_opentelemetry() -> bool:
    """
    Initialize OpenTelemetry SDK with TracerProvider and OTLP Exporter.

    Uses settings from OpenTelemetrySettings.
    Safe to call multiple times (idempotent).

    Returns:
        bool: True if initialization succeeded, False if disabled or failed
    """
    global _initialized, _tracer_provider, _tracer

    if _initialized:
        return _tracer_provider is not None

    # Check if OTEL is available
    if not _is_otel_available():
        logger.debug("OpenTelemetry SDK not installed. Skipping initialization.")
        _initialized = True
        return False

    # Import settings
    from selfhealing.settings.observability import get_otel_settings

    settings = get_otel_settings()

    if not settings.enabled:
        logger.debug("OpenTelemetry disabled via OTEL_ENABLED=false")
        _initialized = True
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import (
            ParentBasedTraceIdRatio,
            TraceIdRatioBased,
        )

        # Build resource attributes
        resource_attrs = {
            "service.name": settings.service_name,
        }
        resource_attrs.update(settings.get_resource_attributes_dict())
        resource = Resource(attributes=resource_attrs)

        # Configure sampler based on settings
        if settings.adaptive_sampling_enabled:
            # Use adaptive sampler (imported from sampler module)
            from selfhealing.observability.sampler import (
                EmergencyLevelAdaptiveSampler,
            )

            sampler = EmergencyLevelAdaptiveSampler(
                base_ratio=settings.traces_sampler_arg,
                sla_critical_ms=settings.sla_critical_threshold_ms,
            )
        else:
            # Use standard sampler based on config
            if settings.traces_sampler.startswith("parentbased_"):
                sampler = ParentBasedTraceIdRatio(settings.traces_sampler_arg)
            else:
                sampler = TraceIdRatioBased(settings.traces_sampler_arg)

        # Create TracerProvider
        _tracer_provider = TracerProvider(
            resource=resource,
            sampler=sampler,
        )

        # Configure OTLP exporter
        otlp_exporter = OTLPSpanExporter(
            endpoint=settings.exporter_otlp_endpoint,
            timeout=settings.exporter_otlp_timeout_ms / 1000,  # Convert to seconds
        )

        # Add BatchSpanProcessor for efficient export
        span_processor = BatchSpanProcessor(otlp_exporter)
        _tracer_provider.add_span_processor(span_processor)

        # Set as global TracerProvider
        trace.set_tracer_provider(_tracer_provider)

        # Create default tracer
        _tracer = trace.get_tracer(settings.service_name)

        _initialized = True
        logger.info(
            "OpenTelemetry initialized: service=%s endpoint=%s sampling_ratio=%.2f%%",
            settings.service_name,
            settings.exporter_otlp_endpoint,
            settings.traces_sampler_arg * 100,
        )
        return True

    except Exception as e:
        logger.warning("Failed to initialize OpenTelemetry: %s", e)
        _initialized = True
        return False


def get_tracer():
    """
    Get the configured OpenTelemetry tracer.

    Returns:
        Tracer instance if OTEL is initialized, None otherwise
    """
    if not _initialized:
        initialize_opentelemetry()
    return _tracer


def get_tracer_provider():
    """
    Get the configured TracerProvider.

    Returns:
        TracerProvider instance if OTEL is initialized, None otherwise
    """
    if not _initialized:
        initialize_opentelemetry()
    return _tracer_provider


def is_otel_enabled() -> bool:
    """
    Check if OpenTelemetry is enabled and initialized.

    Returns:
        bool: True if OTEL is active, False otherwise
    """
    if not _initialized:
        initialize_opentelemetry()
    return _tracer_provider is not None


def get_current_span():
    """
    Get the current active span from OpenTelemetry context.

    Returns:
        Current Span if OTEL is enabled, None otherwise
    """
    if not is_otel_enabled():
        return None

    try:
        from opentelemetry import trace

        return trace.get_current_span()
    except Exception:
        return None


def get_current_trace_id_from_otel() -> str | None:
    """
    Extract trace_id from the current OpenTelemetry span context.

    Returns:
        32-character hex trace_id if available, None otherwise
    """
    span = get_current_span()
    if span is None:
        return None

    try:
        span_context = span.get_span_context()
        if span_context and span_context.is_valid:
            # Format as 32-character hex string (W3C standard)
            return format(span_context.trace_id, "032x")
    except Exception:
        pass

    return None


def get_current_span_id_from_otel() -> str | None:
    """
    Extract span_id from the current OpenTelemetry span context.

    Returns:
        16-character hex span_id if available, None otherwise
    """
    span = get_current_span()
    if span is None:
        return None

    try:
        span_context = span.get_span_context()
        if span_context and span_context.is_valid:
            # Format as 16-character hex string
            return format(span_context.span_id, "016x")
    except Exception:
        pass

    return None


def shutdown_opentelemetry() -> None:
    """
    Gracefully shutdown OpenTelemetry SDK.

    Flushes pending spans and releases resources.
    """
    global _initialized, _tracer_provider, _tracer

    if _tracer_provider is not None:
        try:
            _tracer_provider.shutdown()
            logger.debug("OpenTelemetry shutdown completed")
        except Exception as e:
            logger.warning("Error during OpenTelemetry shutdown: %s", e)

    _tracer_provider = None
    _tracer = None
    _initialized = False


def reset_opentelemetry() -> None:
    """
    Reset OpenTelemetry state for testing.

    This forces re-initialization on next use.
    """
    global _initialized, _tracer_provider, _tracer, _requests_instrumented, _celery_instrumented
    _initialized = False
    _tracer_provider = None
    _tracer = None
    _requests_instrumented = False
    _celery_instrumented = False


def instrument_requests() -> bool:
    """
    Enable automatic instrumentation for requests library.

    Adds automatic span creation and traceparent header injection
    for all outgoing HTTP requests made via the requests library.

    Returns:
        bool: True if instrumentation was successful, False otherwise
    """
    global _requests_instrumented

    if _requests_instrumented:
        return True

    if not is_otel_enabled():
        return False

    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        RequestsInstrumentor().instrument()
        _requests_instrumented = True
        logger.info("OpenTelemetry requests instrumentation enabled")
        return True

    except ImportError:
        logger.debug("opentelemetry-instrumentation-requests not installed")
        return False
    except Exception as e:
        logger.warning("Failed to instrument requests: %s", e)
        return False


def uninstrument_requests() -> None:
    """
    Disable automatic instrumentation for requests library.

    Used primarily for testing to ensure clean state.
    """
    global _requests_instrumented

    if not _requests_instrumented:
        return

    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        RequestsInstrumentor().uninstrument()
        _requests_instrumented = False
        logger.debug("OpenTelemetry requests instrumentation disabled")
    except Exception:
        pass


def instrument_celery() -> bool:
    """
    Enable automatic instrumentation for Celery tasks.

    Adds automatic span creation for Celery task execution
    and propagates trace context between task producers and consumers.

    Returns:
        bool: True if instrumentation was successful, False otherwise
    """
    global _celery_instrumented

    if _celery_instrumented:
        return True

    if not is_otel_enabled():
        return False

    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor

        CeleryInstrumentor().instrument()
        _celery_instrumented = True
        logger.info("OpenTelemetry Celery instrumentation enabled")
        return True

    except ImportError:
        logger.debug("opentelemetry-instrumentation-celery not installed")
        return False
    except Exception as e:
        logger.warning("Failed to instrument Celery: %s", e)
        return False


def uninstrument_celery() -> None:
    """
    Disable automatic instrumentation for Celery.

    Used primarily for testing to ensure clean state.
    """
    global _celery_instrumented

    if not _celery_instrumented:
        return

    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor

        CeleryInstrumentor().uninstrument()
        _celery_instrumented = False
        logger.debug("OpenTelemetry Celery instrumentation disabled")
    except Exception:
        pass


def is_requests_instrumented() -> bool:
    """Check if requests library is instrumented."""
    return _requests_instrumented


def is_celery_instrumented() -> bool:
    """Check if Celery is instrumented."""
    return _celery_instrumented
