"""
Self-Healing API Middleware.

Provides middleware components for security, logging, and performance.

Features:
- SensitiveEndpointAccessLogger: Logs access to sensitive endpoints
- Log data masking for privacy protection

Reference: docs/self_healing/07_CONTROL_API.md (Access Logging section)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

# Sensitive endpoints that require access logging
# Only HIGH sensitivity endpoints are logged (audit, config, chaos schedules)
SENSITIVE_ENDPOINT_PATTERNS = [
    re.compile(r"^/api/self-healing/audit/"),
    re.compile(r"^/api/self-healing/config/"),
    re.compile(r"^/api/self-healing/chaos/schedules/"),
    re.compile(r"^/api/self-healing/chaos/config/"),
]


# =============================================================================
# Access Log Entry
# =============================================================================


class AccessLogEntry:
    """Represents an access log entry for sensitive endpoints."""

    def __init__(
        self,
        *,
        timestamp: datetime,
        user: str,
        method: str,
        path: str,
        query_params: str,
        source_ip: str,
        user_agent: str,
        status_code: Optional[int] = None,
        response_time_ms: Optional[float] = None,
    ):
        self.timestamp = timestamp
        self.user = user
        self.method = method
        self.path = path
        self.query_params = query_params
        self.source_ip = source_ip
        self.user_agent = user_agent
        self.status_code = status_code
        self.response_time_ms = response_time_ms

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "user": self.user,
            "method": self.method,
            "path": self.path,
            "query_params": self.query_params,
            "source_ip": self._mask_internal_ip(self.source_ip),
            "user_agent": self.user_agent,
            "status_code": self.status_code,
            "response_time_ms": self.response_time_ms,
        }

    def _mask_internal_ip(self, ip: str) -> str:
        """Mask internal IP addresses for privacy."""
        if not ip:
            return ip

        internal_patterns = [
            re.compile(r"^10\.\d{1,3}\.\d{1,3}\.\d{1,3}$"),
            re.compile(r"^172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}$"),
            re.compile(r"^192\.168\.\d{1,3}\.\d{1,3}$"),
        ]

        for pattern in internal_patterns:
            if pattern.match(ip):
                # Show only network portion for internal IPs
                parts = ip.split(".")
                return f"{parts[0]}.{parts[1]}.xxx.xxx"

        return ip

    def __str__(self) -> str:
        """Format as log message."""
        return (
            f"[AccessLog] user={self.user} method={self.method} "
            f"path={self.path} ip={self._mask_internal_ip(self.source_ip)} "
            f"status={self.status_code} time_ms={self.response_time_ms}"
        )


# =============================================================================
# Access Logger Service
# =============================================================================


class SensitiveEndpointAccessLogger:
    """
    Service for logging access to sensitive endpoints.

    This provides audit trail for compliance requirements.
    Only logs access to HIGH sensitivity endpoints:
    - /audit/ - System control audit logs
    - /config/* - System configuration
    - /chaos/schedules/* - Chaos experiment schedules
    - /chaos/config/* - Chaos configuration

    Usage in Django middleware:
        class AccessLoggingMiddleware:
            def __init__(self, get_response):
                self.get_response = get_response
                self.access_logger = SensitiveEndpointAccessLogger()

            def __call__(self, request):
                response = self.get_response(request)
                self.access_logger.log_if_sensitive(request, response)
                return response
    """

    def __init__(self, patterns: list[re.Pattern] | None = None):
        """
        Initialize the access logger.

        Args:
            patterns: Custom list of patterns to consider sensitive.
                     Uses default SENSITIVE_ENDPOINT_PATTERNS if None.
        """
        self.patterns = patterns or SENSITIVE_ENDPOINT_PATTERNS
        self._access_log_file = "logs/sensitive_access.log"

    def is_sensitive_endpoint(self, path: str) -> bool:
        """Check if the given path is a sensitive endpoint."""
        for pattern in self.patterns:
            if pattern.match(path):
                return True
        return False

    def log_if_sensitive(
        self,
        request: "HttpRequest",
        response: "HttpResponse",
        response_time_ms: float = 0.0,
    ) -> Optional[AccessLogEntry]:
        """
        Log access if the request is to a sensitive endpoint.

        Args:
            request: Django HttpRequest object
            response: Django HttpResponse object
            response_time_ms: Response time in milliseconds

        Returns:
            AccessLogEntry if logged, None otherwise
        """
        path = request.path

        if not self.is_sensitive_endpoint(path):
            return None

        # Extract user info
        user = "anonymous"
        if hasattr(request, "user") and request.user.is_authenticated:
            user = getattr(request.user, "username", str(request.user.id))

        # Get client IP (consider X-Forwarded-For for proxied requests)
        source_ip = self._get_client_ip(request)

        # Create log entry
        entry = AccessLogEntry(
            timestamp=datetime.now(timezone.utc),
            user=user,
            method=request.method,
            path=path,
            query_params=request.META.get("QUERY_STRING", ""),
            source_ip=source_ip,
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            status_code=response.status_code,
            response_time_ms=response_time_ms,
        )

        # Log to both structured logger and access log
        self._write_log(entry)

        return entry

    def _get_client_ip(self, request: "HttpRequest") -> str:
        """Extract client IP from request, considering proxies."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            # Take the first IP (original client)
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "")

    def _write_log(self, entry: AccessLogEntry) -> None:
        """Write the access log entry."""
        # Log to Python logger (for aggregation)
        logger.info(str(entry))

        # Also write to dedicated access log file
        try:
            self._append_to_file(entry)
        except Exception as e:
            logger.warning(f"[AccessLog] Failed to write to file: {e}")

    def _append_to_file(self, entry: AccessLogEntry) -> None:
        """Append entry to the access log file."""
        import json
        import os

        # Ensure directory exists
        log_dir = os.path.dirname(self._access_log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        with open(self._access_log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict()) + "\n")


# =============================================================================
# Django Middleware
# =============================================================================


class SensitiveAccessLoggingMiddleware:
    """
    Django middleware for logging access to sensitive endpoints.

    Add to MIDDLEWARE in settings.py:
        MIDDLEWARE = [
            ...
            'selfhealing.api.django.middleware.SensitiveAccessLoggingMiddleware',
            ...
        ]

    This middleware logs all GET requests to sensitive endpoints
    (audit, config, chaos schedules) for compliance audit trails.
    """

    def __init__(self, get_response: Callable):
        self.get_response = get_response
        self.access_logger = SensitiveEndpointAccessLogger()

    def __call__(self, request: "HttpRequest") -> "HttpResponse":
        import time

        start_time = time.time()

        response = self.get_response(request)

        # Calculate response time
        response_time_ms = (time.time() - start_time) * 1000

        # Log if sensitive endpoint
        self.access_logger.log_if_sensitive(request, response, response_time_ms)

        return response


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "SensitiveEndpointAccessLogger",
    "SensitiveAccessLoggingMiddleware",
    "AccessLogEntry",
    "SENSITIVE_ENDPOINT_PATTERNS",
]
