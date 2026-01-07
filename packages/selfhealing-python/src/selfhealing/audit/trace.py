"""
Distributed Trace ID Management.

Provides request tracing across the audit logging system.
Integrates with OpenTelemetry and common tracing headers.
"""

import contextvars
import logging
import threading
import uuid
from contextlib import contextmanager
from typing import Any, Generator, Optional

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


# =============================================================================
# Phase 25: Celery Task trace_id 전파 및 복원
# =============================================================================


def get_trace_for_celery() -> dict[str, Any]:
    """
    Celery Task에 전달할 trace 정보를 반환합니다.
    
    HTTP 요청 컨텍스트에서 호출 시 현재 trace_id를 포함하여 반환합니다.
    Celery Task 내에서 restore_trace_from_celery()로 복원할 수 있습니다.
    
    Returns:
        dict: trace_id와 source 정보를 담은 딕셔너리
        
    Example:
        # View에서 Task 호출 시
        from selfhealing.audit.trace import get_trace_for_celery
        
        replay_single_dlq_entry.delay(
            dlq_id=pk,
            trace_info=get_trace_for_celery(),
        )
    """
    current_trace_id = _trace_id_var.get() or getattr(_thread_local, "trace_id", None)
    
    return {
        "trace_id": current_trace_id,
        "source": "celery_propagated",
    }


@contextmanager
def restore_trace_from_celery(
    trace_info: Optional[dict[str, Any]] = None
) -> Generator[str, None, None]:
    """
    Celery Task에서 trace 컨텍스트를 복원하거나 자체 생성합니다.
    
    동작:
    - trace_info가 있고 trace_id가 존재하면: 전파된 trace_id 사용
    - trace_info가 없거나 trace_id가 없으면: INTERNAL_BEAT_xxx 형식으로 자체 생성
    
    이를 통해:
    - 수동 API 호출: 원본 HTTP 요청의 trace_id가 Celery Task까지 추적 가능
    - Beat 자동 호출: 별도의 내부 trace_id로 추적 가능
    
    Args:
        trace_info: get_trace_for_celery()로 생성된 trace 정보 (optional)
        
    Yields:
        str: 현재 사용 중인 trace_id
        
    Example:
        @shared_task
        def my_task(dlq_id: int, trace_info: dict = None):
            with restore_trace_from_celery(trace_info):
                # 이 블록 내에서 get_trace_id()는 적절한 trace_id 반환
                do_work()
    """
    if trace_info and trace_info.get("trace_id"):
        # 전파된 trace_id 사용
        trace_id = trace_info["trace_id"]
    else:
        # Beat에서 호출된 경우: INTERNAL_BEAT_xxx 형식으로 자체 생성
        trace_id = f"INTERNAL_BEAT_{generate_trace_id()}"
    
    with TraceContext(trace_id) as active_trace_id:
        yield active_trace_id
