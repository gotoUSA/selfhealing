"""
Distributed Trace ID Management.

Provides request tracing across the audit logging system.
Integrates with OpenTelemetry and common tracing headers.
"""

import contextvars
import logging
import threading
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

# Context variable for async-safe trace ID storage
_trace_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("trace_id", default=None)

# Thread-local fallback for non-async code
_thread_local = threading.local()


def generate_trace_id() -> str:
    """
    Generate a new trace ID.

    Format: "req-{uuid4_short}" (e.g., "req-a1b2c3d4")

    Returns:
        New unique trace ID
    """
    return f"req-{uuid.uuid4().hex[:8]}"


def get_trace_id() -> str:
    """
    Get the current trace ID.

    Checks in order:
    1. Context variable (async-safe)
    2. Thread-local storage
    3. Generates new if none exists

    Returns:
        Current trace ID
    """
    # Try context variable first (async-safe)
    trace_id = _trace_id_var.get()
    if trace_id:
        return trace_id

    # Try thread-local
    trace_id = getattr(_thread_local, "trace_id", None)
    if trace_id:
        return trace_id

    # Generate new one
    new_id = generate_trace_id()
    set_trace_id(new_id)
    return new_id


def set_trace_id(trace_id: str) -> None:
    """
    Set the current trace ID.

    Sets in both context variable and thread-local for compatibility.

    Args:
        trace_id: The trace ID to set
    """
    _trace_id_var.set(trace_id)
    _thread_local.trace_id = trace_id


def clear_trace_id() -> None:
    """Clear the current trace ID."""
    _trace_id_var.set(None)
    _thread_local.trace_id = None


def extract_trace_id_from_request(request) -> Optional[str]:
    """
    Extract trace ID from a Django request.

    Checks common tracing headers in order:
    1. X-Request-ID
    2. X-Trace-ID
    3. X-Correlation-ID
    4. traceparent (W3C Trace Context)
    5. X-Amzn-Trace-Id (AWS X-Ray)

    Args:
        request: Django HttpRequest object

    Returns:
        Trace ID if found, None otherwise
    """
    headers_to_check = [
        "HTTP_X_REQUEST_ID",
        "HTTP_X_TRACE_ID",
        "HTTP_X_CORRELATION_ID",
        "HTTP_TRACEPARENT",
        "HTTP_X_AMZN_TRACE_ID",
    ]

    meta = getattr(request, "META", {})

    for header in headers_to_check:
        value = meta.get(header)
        if value:
            # For traceparent, extract the trace-id portion
            if header == "HTTP_TRACEPARENT":
                # Format: version-trace_id-parent_id-flags
                parts = value.split("-")
                if len(parts) >= 2:
                    return f"req-{parts[1][:8]}"
            # For AWS X-Ray, extract the trace ID
            elif header == "HTTP_X_AMZN_TRACE_ID":
                # Format: Root=1-xxx-yyy;Parent=zzz;Sampled=1
                if "Root=" in value:
                    root = value.split("Root=")[1].split(";")[0]
                    return f"req-{root[-8:]}"
            else:
                return value

    return None


class TraceContext:
    """
    Context manager for trace ID scoping.

    Usage:
        with TraceContext("req-12345"):
            # All audit logs in this block will have this trace ID
            do_something()

        # Or auto-generate:
        with TraceContext() as trace_id:
            print(f"Using trace: {trace_id}")
    """

    def __init__(self, trace_id: Optional[str] = None):
        """
        Initialize trace context.

        Args:
            trace_id: Optional trace ID to use (auto-generated if not provided)
        """
        self.trace_id = trace_id or generate_trace_id()
        self._previous_trace_id: Optional[str] = None

    def __enter__(self) -> str:
        """Enter context and set trace ID."""
        self._previous_trace_id = _trace_id_var.get()
        set_trace_id(self.trace_id)
        return self.trace_id

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context and restore previous trace ID."""
        if self._previous_trace_id:
            set_trace_id(self._previous_trace_id)
        else:
            clear_trace_id()


def trace_id_middleware(get_response):
    """
    Django middleware for automatic trace ID handling.

    Extracts or generates trace ID for each request and adds it to response.

    Usage in settings.py:
        MIDDLEWARE = [
            'selfhealing.audit.trace.trace_id_middleware',
            # ... other middleware
        ]
    """

    def middleware(request):
        # Extract or generate trace ID
        trace_id = extract_trace_id_from_request(request) or generate_trace_id()
        set_trace_id(trace_id)

        # Store on request for easy access
        request.trace_id = trace_id

        # Process request
        response = get_response(request)

        # Add trace ID to response headers
        response["X-Request-ID"] = trace_id

        # Clear trace ID after request
        clear_trace_id()

        return response

    return middleware
