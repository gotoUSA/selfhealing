"""
OpenTelemetry SDK Initialization.

Provides TracerProvider setup, OTLP exporter configuration,
and compatibility layer with existing trace_id system.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Module-level state
_initialized: bool = False
_tracer_provider = None
_tracer = None
_logger_provider = None
_logging_instrumented: bool = False
_requests_instrumented: bool = False
_celery_instrumented: bool = False
_django_instrumented: bool = False


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

        # Baggage Propagator 등록 — traceparent + baggage 헤더 자동 전파
        from selfhealing.observability.baggage import setup_baggage_propagation

        setup_baggage_propagation()

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

    Flushes pending spans and log records, releases resources.
    """
    global _initialized, _tracer_provider, _tracer, _logger_provider

    if _logger_provider is not None:
        try:
            _logger_provider.shutdown()
            logger.debug("OpenTelemetry LoggerProvider shutdown completed")
        except Exception as e:
            logger.warning("Error during LoggerProvider shutdown: %s", e)
        _logger_provider = None

    if _tracer_provider is not None:
        try:
            _tracer_provider.shutdown()
            logger.debug("OpenTelemetry TracerProvider shutdown completed")
        except Exception as e:
            logger.warning("Error during TracerProvider shutdown: %s", e)

    _tracer_provider = None
    _tracer = None
    _initialized = False


def reset_opentelemetry() -> None:
    """
    Reset OpenTelemetry state for testing.

    This forces re-initialization on next use.
    """
    global _initialized, _tracer_provider, _tracer, _logger_provider
    global _requests_instrumented, _celery_instrumented, _logging_instrumented, _django_instrumented
    _initialized = False
    _tracer_provider = None
    _tracer = None
    _logger_provider = None
    _requests_instrumented = False
    _celery_instrumented = False
    _logging_instrumented = False
    _django_instrumented = False


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


def is_django_instrumented() -> bool:
    """Check if Django is instrumented."""
    return _django_instrumented


def instrument_django() -> bool:
    """
    Enable automatic instrumentation for Django.

    WSGI 레벨에서 traceparent + baggage 헤더를 자동 추출하고,
    Django 요청에 대한 span을 자동 생성한다.

    DjangoInstrumentor는 내부적으로 MIDDLEWARE 최상단에
    _DjangoMiddleware를 자동 삽입한다.
    따라서 BaggageSyncMiddleware보다 반드시 먼저 실행된다.

    excluded_urls: /health, /metrics 등 불필요한 span/baggage 파싱 제외.

    Returns:
        bool: True if instrumentation was successful, False otherwise
    """
    global _django_instrumented

    if _django_instrumented:
        return True

    if not is_otel_enabled():
        return False

    try:
        import os

        from opentelemetry.instrumentation.django import DjangoInstrumentor

        from selfhealing.settings.observability import get_otel_settings

        settings = get_otel_settings()

        if not settings.django_instrument_enabled:
            logger.debug("Django instrumentation disabled via OTEL_DJANGO_INSTRUMENT_ENABLED=false")
            return False

        # excluded_urls 설정 적용 — 환경변수 OTEL_PYTHON_DJANGO_EXCLUDED_URLS 사용
        excluded = ",".join(settings.get_excluded_urls_list())
        if excluded:
            os.environ.setdefault("OTEL_PYTHON_DJANGO_EXCLUDED_URLS", excluded)

        DjangoInstrumentor().instrument()
        _django_instrumented = True
        logger.info(
            "OpenTelemetry Django instrumentation enabled " "(excluded_urls=%s)",
            excluded or "none",
        )
        return True

    except ImportError:
        logger.debug("opentelemetry-instrumentation-django not installed")
        return False
    except Exception as e:
        logger.warning("Failed to instrument Django: %s", e)
        return False


def uninstrument_django() -> None:
    """
    Disable automatic instrumentation for Django.

    Used primarily for testing to ensure clean state.
    """
    global _django_instrumented

    if not _django_instrumented:
        return

    try:
        from opentelemetry.instrumentation.django import DjangoInstrumentor

        DjangoInstrumentor().uninstrument()
        _django_instrumented = False
        logger.debug("OpenTelemetry Django instrumentation disabled")
    except Exception:
        pass


def is_logging_instrumented() -> bool:
    """Check if Python logging is instrumented."""
    return _logging_instrumented


def _is_otel_logging_available() -> bool:
    """Check if OpenTelemetry logging SDK packages are installed."""
    try:
        import opentelemetry.sdk._logs  # noqa: F401
        import opentelemetry.exporter.otlp.proto.grpc._log_exporter  # noqa: F401

        return True
    except ImportError:
        return False


def initialize_logger_provider() -> bool:
    """
    Initialize OpenTelemetry LoggerProvider with OTLP Log Exporter.

    Enables sending Python logs to OTEL Collector for storage in Loki.
    Automatically includes trace_id and span_id in log records.

    Returns:
        bool: True if initialization succeeded, False if disabled or failed
    """
    global _logger_provider

    if _logger_provider is not None:
        return True

    if not is_otel_enabled():
        return False

    if not _is_otel_logging_available():
        logger.debug("OpenTelemetry logging SDK not installed. Skipping LoggerProvider initialization.")
        return False

    try:
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import Resource

        # Import settings
        from selfhealing.settings.observability import get_otel_settings

        settings = get_otel_settings()

        # Build resource attributes (same as TracerProvider)
        resource_attrs = {
            "service.name": settings.service_name,
        }
        resource_attrs.update(settings.get_resource_attributes_dict())
        resource = Resource(attributes=resource_attrs)

        # Create LoggerProvider
        _logger_provider = LoggerProvider(resource=resource)

        # Configure OTLP Log Exporter
        otlp_log_exporter = OTLPLogExporter(
            endpoint=settings.exporter_otlp_endpoint,
            timeout=settings.exporter_otlp_timeout_ms / 1000,
        )

        # Add BatchLogRecordProcessor for efficient export
        log_processor = BatchLogRecordProcessor(otlp_log_exporter)
        _logger_provider.add_log_record_processor(log_processor)

        # Set as global LoggerProvider
        set_logger_provider(_logger_provider)

        logger.info(
            "OpenTelemetry LoggerProvider initialized: service=%s endpoint=%s",
            settings.service_name,
            settings.exporter_otlp_endpoint,
        )
        return True

    except Exception as e:
        logger.warning("Failed to initialize OpenTelemetry LoggerProvider: %s", e)
        return False


def get_logger_provider():
    """
    Get the configured LoggerProvider.

    Returns:
        LoggerProvider instance if OTEL logging is initialized, None otherwise
    """
    if _logger_provider is None:
        initialize_logger_provider()
    return _logger_provider


def instrument_logging() -> bool:
    """
    Enable automatic instrumentation for Python logging.

    Adds OpenTelemetry handler to Python logging that:
    - Sends log records to OTEL Collector via LoggerProvider
    - Automatically includes trace_id and span_id from current span context
    - Enables log-trace correlation in Grafana

    Returns:
        bool: True if instrumentation was successful, False otherwise
    """
    global _logging_instrumented

    if _logging_instrumented:
        return True

    if not is_otel_enabled():
        return False

    if not initialize_logger_provider():
        return False

    try:
        from opentelemetry.instrumentation.logging import LoggingInstrumentor

        # Instrument logging to add trace context to all log records
        LoggingInstrumentor().instrument(
            set_logging_format=True,
            log_level=logging.INFO,
        )

        _logging_instrumented = True
        logger.info("OpenTelemetry logging instrumentation enabled with trace context injection")
        return True

    except ImportError:
        logger.debug("opentelemetry-instrumentation-logging not installed")
        return False
    except Exception as e:
        logger.warning("Failed to instrument logging: %s", e)
        return False


def uninstrument_logging() -> None:
    """
    Disable automatic instrumentation for Python logging.

    Used primarily for testing to ensure clean state.
    """
    global _logging_instrumented

    if not _logging_instrumented:
        return

    try:
        from opentelemetry.instrumentation.logging import LoggingInstrumentor

        LoggingInstrumentor().uninstrument()
        _logging_instrumented = False
        logger.debug("OpenTelemetry logging instrumentation disabled")
    except Exception:
        pass


def shutdown_logger_provider() -> None:
    """
    Gracefully shutdown OpenTelemetry LoggerProvider.

    Flushes pending log records and releases resources.
    """
    global _logger_provider

    if _logger_provider is not None:
        try:
            _logger_provider.shutdown()
            logger.debug("OpenTelemetry LoggerProvider shutdown completed")
        except Exception as e:
            logger.warning("Error during LoggerProvider shutdown: %s", e)

    _logger_provider = None
