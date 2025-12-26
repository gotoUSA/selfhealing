"""
Self-Healing API Middleware.

Provides middleware components for security, logging, and performance.

Features:
- HealthBridgeMiddleware: DB-independent health endpoints (Worker Saturation 방지)
- SensitiveEndpointAccessLogger: Logs access to sensitive endpoints
- Log data masking for privacy protection

Reference: docs/self_healing/07_CONTROL_API.md (Access Logging section)
Stage 50: Observability - Middleware Early Return for /health/l3
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Dict, Any, Optional

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

logger = logging.getLogger(__name__)


# =============================================================================
# Health Bridge Middleware (Stage 50: Worker Saturation 방지)
# =============================================================================


class HealthBridgeMiddleware:
    """
    DB-independent Health Endpoint Middleware.
    
    문제 상황:
    - DB 죽음 → 모든 Django Worker가 DB 연결 대기
    - /health/l3도 Worker를 사용하므로 타임아웃
    - CB 상태를 외부에서 관찰 불가
    
    해결책:
    - Middleware에서 DB 엔진 로드 전에 즉시 반환
    - CB 스냅샷을 메모리에 저장 (매 요청마다 갱신)
    - Prometheus 메트릭은 기존 인프라 활용
    
    CRITICAL: 이 Middleware는 MIDDLEWARE 리스트 최상단에 위치해야 함!
    
    Usage in settings.py:
        MIDDLEWARE = [
            "selfhealing.api.django.middleware.HealthBridgeMiddleware",  # 최상단!
            "django.middleware.security.SecurityMiddleware",
            ...
        ]
    """
    
    # 클래스 변수: CB 스냅샷 저장 (모든 인스턴스가 공유)
    _cb_snapshot: Dict[str, Any] = {
        "states": {},
        "last_updated": None,
        "update_count": 0,
    }
    _snapshot_lock = None  # threading.Lock()는 lazy init
    
    # Health Bridge 대상 경로
    BRIDGE_PATHS = [
        "/api/self-healing/health/l3/",
        "/api/self-healing/health/bridge/",
    ]
    
    def __init__(self, get_response: Callable):
        """Initialize middleware."""
        self.get_response = get_response
        
        # Lazy init lock (import threading here to avoid circular import)
        import threading
        if HealthBridgeMiddleware._snapshot_lock is None:
            HealthBridgeMiddleware._snapshot_lock = threading.Lock()
    
    def __call__(self, request: "HttpRequest") -> "HttpResponse":
        """Process request/response."""
        from django.http import JsonResponse
        
        # === Phase 1: Early Return for Bridge Paths ===
        if request.path in self.BRIDGE_PATHS:
            return self._serve_bridge_response(request)
        
        # === Phase 2: Normal Request Processing ===
        response = self.get_response(request)
        
        # === Phase 3: Update CB Snapshot (best-effort) ===
        # Non-blocking: 실패해도 요청은 정상 처리
        self._try_update_snapshot()
        
        return response
    
    def _serve_bridge_response(self, request: "HttpRequest") -> "HttpResponse":
        """
        Serve health bridge response without touching DB.
        
        Returns CB snapshot from memory - instant response even during DB blackout.
        """
        from django.http import JsonResponse
        
        snapshot = self._get_snapshot()
        
        response_data = {
            "status": "bridge_active",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "circuit_breakers": snapshot.get("states", {}),
            "snapshot": {
                "last_updated": snapshot.get("last_updated"),
                "update_count": snapshot.get("update_count", 0),
                "age_seconds": self._calculate_snapshot_age(snapshot),
            },
            "note": "DB-independent health endpoint (Stage 50)",
        }
        
        return JsonResponse(response_data)
    
    def _try_update_snapshot(self) -> None:
        """
        Try to update CB snapshot from in-memory state.
        
        Non-blocking: If CB service is unavailable, skip silently.
        """
        try:
            # Import here to avoid circular imports and keep DB-independence
            from selfhealing.services.circuit_breaker import get_circuit_breaker_service
            
            cb_service = get_circuit_breaker_service()
            if cb_service is None:
                return
            
            # get_all_states returns list of dicts from repository
            all_states = cb_service.get_all_states()
            
            states = {}
            for s in all_states:
                states[s["service_name"]] = {
                    "state": s["state"],
                    "failure_count": s.get("failure_count", 0),
                    "success_count": s.get("success_count", 0),
                    "last_failure_at": s.get("last_failure_at"),
                    "manually_controlled": s.get("manually_controlled", False),
                }
            
            with self._snapshot_lock:
                HealthBridgeMiddleware._cb_snapshot = {
                    "states": states,
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                    "update_count": self._cb_snapshot.get("update_count", 0) + 1,
                }
                
        except Exception as e:
            # Log at warning level temporarily for debugging
            logger.warning(f"CB snapshot update failed (non-critical): {e}")
    
    def _get_snapshot(self) -> Dict[str, Any]:
        """Thread-safe snapshot read."""
        with self._snapshot_lock:
            return dict(self._cb_snapshot)
    
    def _calculate_snapshot_age(self, snapshot: Dict[str, Any]) -> Optional[float]:
        """Calculate age of snapshot in seconds."""
        last_updated = snapshot.get("last_updated")
        if not last_updated:
            return None
        
        try:
            last_dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            return round((now - last_dt).total_seconds(), 2)
        except Exception:
            return None


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
        """
        Mask internal IP addresses for privacy.

        FAIL-SECURE: On any error, return "[MASKED]" instead of raw IP.
        """
        if not ip:
            return "[EMPTY]"

        try:
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

        except Exception:
            # FAIL-SECURE: On any error, mask completely
            return "[MASKED]"

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
        """
        Write the access log entry with fallback mechanism.
        
        FAIL-OPEN with FALLBACK Design:
        - Primary: Structured logger + dedicated access log file
        - Fallback: Standard output (print) if primary fails
        - This ensures we always have SOME record, even if degraded
        """
        primary_success = False
        
        # Primary: Log to Python logger (for aggregation)
        try:
            logger.info(str(entry))
            primary_success = True
        except Exception as e:
            # Logger failed, will use fallback
            pass

        # Primary: Also write to dedicated access log file
        try:
            self._append_to_file(entry)
            primary_success = True
        except Exception as e:
            logger.warning(f"[AccessLog] Failed to write to file: {e}")
        
        # FALLBACK: If all primary logging failed, use stdout as last resort
        if not primary_success:
            self._fallback_log(entry)

    def _fallback_log(self, entry: AccessLogEntry) -> None:
        """
        Fallback logging to stdout when primary logging fails.
        
        This ensures we have at least some audit trail even when:
        - Main logger is misconfigured
        - Log file is inaccessible
        - Redis/DB logging is down
        
        stdout is captured by container orchestrators (Docker, K8s)
        so it provides a secondary audit trail.
        """
        import sys
        import json
        
        try:
            fallback_record = {
                "_fallback": True,
                "_reason": "primary_logging_failed",
                **entry.to_dict()
            }
            # Write directly to stdout, bypassing logging framework
            print(f"[FALLBACK_AUDIT_LOG] {json.dumps(fallback_record)}", file=sys.stdout, flush=True)
        except Exception:
            # Last resort: minimal output
            print(f"[FALLBACK_AUDIT_LOG] user={entry.user} path={entry.path} status={entry.status_code}", 
                  file=sys.stdout, flush=True)

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

    FAIL-OPEN Design: Logging failure does not block requests.
    The primary function (serving the request) must not be affected by
    secondary functions (logging).
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

        # FAIL-OPEN: Log if sensitive endpoint, but don't block on failure
        try:
            self.access_logger.log_if_sensitive(request, response, response_time_ms)
        except Exception as e:
            # Log error but don't affect response
            logger.error(f"[AccessLog] Middleware error (fail-open): {e}")

        return response


# =============================================================================
# Fail-Secure Permission Classes
# =============================================================================


class FailSecureIsAuthenticated:
    """
    Fail-Secure version of IsAuthenticated.

    FAIL-SECURE Design:
    - If authentication check fails for ANY reason, deny access
    - Default to denial on ambiguity
    - Log all failures for security monitoring
    """

    def has_permission(self, request, view) -> bool:
        """Check if user is authenticated with fail-secure logic."""
        try:
            # Standard authentication check
            is_authenticated = bool(
                request.user and
                hasattr(request.user, 'is_authenticated') and
                request.user.is_authenticated
            )

            if not is_authenticated:
                logger.info(
                    f"[Permission] Denied: user not authenticated, "
                    f"path={request.path}, ip={request.META.get('REMOTE_ADDR')}"
                )

            return is_authenticated

        except Exception as e:
            # FAIL-SECURE: Any error = deny access
            logger.warning(
                f"[Permission] FAIL-SECURE denial due to error: {e}, "
                f"path={request.path}"
            )
            return False


class FailSecureIsAdminUser:
    """
    Fail-Secure version of IsAdminUser.

    FAIL-SECURE Design:
    - If admin check fails for ANY reason, deny access
    - Both is_authenticated AND is_staff must be True
    - Log all denials for security monitoring
    """

    def has_permission(self, request, view) -> bool:
        """Check if user is admin with fail-secure logic."""
        try:
            # Must be authenticated first
            is_authenticated = bool(
                request.user and
                hasattr(request.user, 'is_authenticated') and
                request.user.is_authenticated
            )

            if not is_authenticated:
                logger.info(
                    f"[Permission] Admin check denied: not authenticated, "
                    f"path={request.path}"
                )
                return False

            # Must be staff
            is_admin = bool(
                hasattr(request.user, 'is_staff') and
                request.user.is_staff
            )

            if not is_admin:
                logger.info(
                    f"[Permission] Admin check denied: user={request.user}, "
                    f"path={request.path}"
                )

            return is_admin

        except Exception as e:
            # FAIL-SECURE: Any error = deny access
            logger.warning(
                f"[Permission] FAIL-SECURE admin denial due to error: {e}, "
                f"path={request.path}"
            )
            return False


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Access Logging
    "SensitiveEndpointAccessLogger",
    "SensitiveAccessLoggingMiddleware",
    "AccessLogEntry",
    "SENSITIVE_ENDPOINT_PATTERNS",
    # Fail-Secure Permissions
    "FailSecureIsAuthenticated",
    "FailSecureIsAdminUser",
]
