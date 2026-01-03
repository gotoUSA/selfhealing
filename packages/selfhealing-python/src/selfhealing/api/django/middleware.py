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
            from selfhealing.services.circuit_breaker.convenience import (
                get_circuit_breaker_service,
            )

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
            fallback_record = {"_fallback": True, "_reason": "primary_logging_failed", **entry.to_dict()}
            # Write directly to stdout, bypassing logging framework
            print(f"[FALLBACK_AUDIT_LOG] {json.dumps(fallback_record)}", file=sys.stdout, flush=True)
        except Exception:
            # Last resort: minimal output
            print(
                f"[FALLBACK_AUDIT_LOG] user={entry.user} path={entry.path} status={entry.status_code}",
                file=sys.stdout,
                flush=True,
            )

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
                request.user and hasattr(request.user, "is_authenticated") and request.user.is_authenticated
            )

            if not is_authenticated:
                logger.info(
                    f"[Permission] Denied: user not authenticated, "
                    f"path={request.path}, ip={request.META.get('REMOTE_ADDR')}"
                )

            return is_authenticated

        except Exception as e:
            # FAIL-SECURE: Any error = deny access
            logger.warning(f"[Permission] FAIL-SECURE denial due to error: {e}, " f"path={request.path}")
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
                request.user and hasattr(request.user, "is_authenticated") and request.user.is_authenticated
            )

            if not is_authenticated:
                logger.info(f"[Permission] Admin check denied: not authenticated, " f"path={request.path}")
                return False

            # Must be staff
            is_admin = bool(hasattr(request.user, "is_staff") and request.user.is_staff)

            if not is_admin:
                logger.info(f"[Permission] Admin check denied: user={request.user}, " f"path={request.path}")

            return is_admin

        except Exception as e:
            # FAIL-SECURE: Any error = deny access
            logger.warning(f"[Permission] FAIL-SECURE admin denial due to error: {e}, " f"path={request.path}")
            return False


# =============================================================================
# Self-Healing Middleware (Stage 16 v5.0.0: HEALING PROOF)
# =============================================================================


class SelfHealingMiddleware:
    """
    Self-Healing Middleware for automatic failure detection and DLQ storage.

    이 미들웨어는 다음 기능을 제공합니다:

    1. **DB 오류 감지**: OperationalError, InterfaceError 등 DB 연결 오류 감지
    2. **HTTP 5xx 오류 감지**: 502, 503, 504 등 서버 오류 감지
    3. **CircuitBreaker 자동 기록**: 실패 발생 시 record_failure() 호출
    4. **DLQ 자동 적재**: 복구 가능한 요청을 DLQ에 자동 저장
    5. **Self-Audit 연동**: 해시 체인 로그에 이벤트 기록

    CRITICAL: 이 Middleware는 HealthBridgeMiddleware 다음에 위치해야 합니다!

    Usage in settings.py:
        MIDDLEWARE = [
            "selfhealing.api.django.middleware.HealthBridgeMiddleware",  # 최상단
            "selfhealing.api.django.middleware.SelfHealingMiddleware",   # 두 번째
            "django.middleware.security.SecurityMiddleware",
            ...
        ]

    Stage 16 v5.0.0 (HEALING PROOF):
    - DB 커넥션 풀 고갈 시 서킷 자동 오픈
    - 502/503 에러 발생 시 DLQ 자동 적재
    - 복구 후 자동 리플레이 트리거

    Stage 16 v6.1.0 (HEALING PROOF - Phase 3/4/5 Fix):
    - stress 엔드포인트 503 에러를 인프라 장애로 인식 (신호 통합)
    - CB OPEN 시 선제적 DLQ 적재 (자동 라우팅)
    - 완전한 자율 치유 사이클 달성
    """

    # 감시 대상 HTTP 상태 코드
    MONITORED_STATUS_CODES = {502, 503, 504}

    # 감시 대상 DB 예외 클래스 (문자열로 저장 - lazy import 위해)
    MONITORED_DB_ERRORS = (
        "OperationalError",  # DB 연결 오류, 쿼리 실패
        "InterfaceError",  # DB 인터페이스 오류
        "DatabaseError",  # 일반 DB 오류
        "ConnectionDoesNotExist",  # Django 커넥션 미존재
    )

    # DLQ 적재 대상 경로 패턴 - 설정에서 로드 (Domain-Free)
    # settings.SELF_HEALING_DLQ_ELIGIBLE_PATHS에서 읽어옴
    # 기본값: 빈 리스트 (프로젝트에서 설정 필요)
    DLQ_ELIGIBLE_PATHS: list = []  # _load_path_patterns()에서 초기화

    # 인프라 장애로 인식할 경로 패턴 - 설정에서 로드 (Domain-Free)
    # settings.SELF_HEALING_INFRA_FAILURE_PATHS에서 읽어옴
    INFRASTRUCTURE_FAILURE_PATHS: list = []  # _load_path_patterns()에서 초기화

    # 도메인 추론 매핑 - 설정에서 로드 (Domain-Free)
    # settings.SELF_HEALING_DOMAIN_MAPPING에서 읽어옴
    # 형식: {"pattern": "domain"} 예: {"/payments/": "payment"}
    DOMAIN_MAPPING: dict = {}  # _load_path_patterns()에서 초기화

    _paths_loaded: bool = False

    # CircuitBreaker 서비스 이름
    CB_SERVICE_NAME = "database"

    def __init__(self, get_response: Callable):
        """Initialize middleware."""
        self.get_response = get_response
        self._audit_logger = None
        self._cb_service = None
        self._initialized = False

    def _lazy_init(self) -> None:
        """Lazy initialization to avoid circular imports."""
        if self._initialized:
            return

        # 경로 패턴 로드 (한 번만)
        self._load_path_patterns()

        try:
            from selfhealing.services.circuit_breaker.convenience import (
                get_circuit_breaker_service,
            )

            self._cb_service = get_circuit_breaker_service()
        except Exception as e:
            logger.warning(f"[SelfHealingMiddleware] CB service init failed: {e}")

        try:
            from selfhealing.audit import get_audit_logger

            self._audit_logger = get_audit_logger()
        except Exception as e:
            logger.warning(f"[SelfHealingMiddleware] Audit logger init failed: {e}")

        self._initialized = True

    @classmethod
    def _load_path_patterns(cls) -> None:
        """
        Load path patterns from Django settings (Domain-Free).

        settings.py에 다음을 정의하세요:

        SELF_HEALING_DLQ_ELIGIBLE_PATHS = [
            r"^/api/orders/",
            r"^/api/payments/",
            r"^/api/cart/",
            ...
        ]

        SELF_HEALING_INFRA_FAILURE_PATHS = [
            r"^/api/orders/",
            r"^/api/payments/",
            ...
        ]

        SELF_HEALING_DOMAIN_MAPPING = {
            "/payments/": "payment",
            "/checkout/": "payment",
            "/orders/": "order",
            "/points/": "point",
            "/cart/": "cart",
            "/webhooks/": "webhook",
        }
        """
        if cls._paths_loaded:
            return

        from django.conf import settings

        # DLQ 적재 대상 경로
        dlq_patterns = getattr(settings, "SELF_HEALING_DLQ_ELIGIBLE_PATHS", [])
        cls.DLQ_ELIGIBLE_PATHS = [re.compile(p) for p in dlq_patterns]

        # 인프라 장애 경로
        infra_patterns = getattr(settings, "SELF_HEALING_INFRA_FAILURE_PATHS", [])
        cls.INFRASTRUCTURE_FAILURE_PATHS = [re.compile(p) for p in infra_patterns]

        # 도메인 매핑
        cls.DOMAIN_MAPPING = getattr(settings, "SELF_HEALING_DOMAIN_MAPPING", {})

        cls._paths_loaded = True

        logger.info(
            f"[SelfHealingMiddleware] Loaded patterns: "
            f"DLQ={len(cls.DLQ_ELIGIBLE_PATHS)}, "
            f"Infra={len(cls.INFRASTRUCTURE_FAILURE_PATHS)}, "
            f"Domains={len(cls.DOMAIN_MAPPING)}"
        )

    def __call__(self, request: "HttpRequest") -> "HttpResponse":
        """Process request/response with self-healing logic.

        v6.1.0 Enhancement:
        1. CB OPEN 상태에서 선제적 DLQ 적재 (자동 라우팅)
        2. 인프라 장애 경로의 503은 강화된 CB 기록 (신호 통합)
        """
        from django.http import JsonResponse

        self._lazy_init()

        request_data = self._capture_request_data(request)
        db_error_context = None

        # =====================================================================
        # v6.1.0: CB OPEN 상태에서 선제적 DLQ 적재 (자동 라우팅)
        # =====================================================================
        if self._is_cb_open() and self._is_dlq_eligible(request):
            error_context = {
                "error_type": "CIRCUIT_BREAKER_OPEN",
                "error_message": "Circuit breaker is OPEN - request queued for later retry",
                "path": request.path,
                "method": request.method,
                "preemptive": True,  # 선제적 DLQ 적재 표시
            }

            dlq_id = self._store_to_dlq(request_data, error_context, request=request)

            logger.info(
                f"[SelfHealingMiddleware] 🔒 Preemptive DLQ: CB is OPEN, "
                f"request queued (dlq_id={dlq_id}, path={request.path})"
            )

            # Audit 로그 기록 (Phase 3: 버퍼 패턴)
            self._log_audit_event(
                "preemptive_dlq_stored",
                {
                    "dlq_id": dlq_id,
                    "reason": "circuit_breaker_open",
                    "path": request.path,
                },
                request=request,
            )

            return JsonResponse(
                {
                    "error": "Service temporarily unavailable",
                    "code": "CIRCUIT_BREAKER_OPEN",
                    "retry_after": 30,
                    "dlq_stored": True,
                    "dlq_id": dlq_id,
                    "message": "Request has been queued for automatic retry when service recovers",
                },
                status=503,
            )

        # =====================================================================
        # 기존 로직: 요청 처리 및 예외/응답 감시
        # =====================================================================
        try:
            # DB 오류를 감지하기 위해 try-except로 감싸기
            response = self.get_response(request)

        except Exception as e:
            # DB 관련 예외 감지
            error_type = type(e).__name__

            if error_type in self.MONITORED_DB_ERRORS or self._is_db_connection_error(e):
                db_error_context = {
                    "error_type": error_type,
                    "error_message": str(e),
                    "path": request.path,
                    "method": request.method,
                }

                # CircuitBreaker에 실패 기록 (Phase 3: request 전달)
                self._record_cb_failure(db_error_context, request=request)

                # DLQ에 적재 (복구 가능한 요청인 경우, Phase 3: request 전달)
                if self._is_dlq_eligible(request):
                    self._store_to_dlq(request_data, db_error_context, request=request)

                # 503 응답 반환
                return JsonResponse(
                    {
                        "error": "Service temporarily unavailable",
                        "code": "DB_CONNECTION_ERROR",
                        "retry_after": 30,
                        "dlq_stored": self._is_dlq_eligible(request),
                    },
                    status=503,
                )

            # DB 오류가 아닌 경우 다시 raise
            raise

        # HTTP 5xx 응답 감지
        if response.status_code in self.MONITORED_STATUS_CODES:
            # v6.1.0: 인프라 장애 경로 여부 확인
            is_infra_failure_path = self._is_infrastructure_failure_path(request)

            error_context = {
                "error_type": f"HTTP_{response.status_code}",
                "error_message": f"Server returned {response.status_code}",
                "path": request.path,
                "method": request.method,
                "infrastructure_failure": is_infra_failure_path,  # v6.1.0
            }

            # CircuitBreaker에 실패 기록
            # v6.1.0: 인프라 장애 경로에서는 반드시 CB 실패로 기록
            # Phase 3: request 전달
            self._record_cb_failure(error_context, request=request)

            if is_infra_failure_path:
                logger.warning(
                    f"[SelfHealingMiddleware] 🔥 INFRA FAILURE detected: "
                    f"path={request.path}, status={response.status_code}"
                )

            # DLQ에 적재 (복구 가능한 요청인 경우, Phase 3: request 전달)
            if self._is_dlq_eligible(request):
                self._store_to_dlq(request_data, error_context, request=request)

        else:
            # 성공 응답일 경우 CircuitBreaker에 성공 기록
            if response.status_code < 400:
                self._record_cb_success()

        return response

    def _is_db_connection_error(self, error: Exception) -> bool:
        """Check if the error is a DB connection related error."""
        error_str = str(error).lower()
        db_error_keywords = [
            "connection refused",
            "too many clients",
            "connection timed out",
            "could not connect",
            "server closed the connection",
            "connection reset",
            "pool exhausted",
            "no connection available",
        ]
        return any(keyword in error_str for keyword in db_error_keywords)

    def _capture_request_data(self, request: "HttpRequest") -> Dict[str, Any]:
        """Capture request data for DLQ storage."""
        try:
            body = {}
            if request.body:
                try:
                    body = json.loads(request.body.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    body = {"raw": request.body.decode("utf-8", errors="replace")}

            return {
                "method": request.method,
                "path": request.path,
                "query_string": request.META.get("QUERY_STRING", ""),
                "body": body,
                "headers": {
                    "content_type": request.META.get("CONTENT_TYPE", ""),
                    "user_agent": request.META.get("HTTP_USER_AGENT", ""),
                    "x_request_id": request.META.get("HTTP_X_REQUEST_ID", ""),
                    "x_idempotency_key": request.META.get("HTTP_X_IDEMPOTENCY_KEY", ""),
                },
                "user_id": getattr(request.user, "id", None) if hasattr(request, "user") else None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            logger.warning(f"[SelfHealingMiddleware] Request capture failed: {e}")
            return {"path": getattr(request, "path", "unknown"), "error": str(e)}

    def _is_dlq_eligible(self, request: "HttpRequest") -> bool:
        """Check if request is eligible for DLQ storage."""
        # POST, PUT, PATCH 요청만 DLQ 적재 대상
        if request.method not in ("POST", "PUT", "PATCH"):
            return False

        # 경로 패턴 매칭
        for pattern in self.DLQ_ELIGIBLE_PATHS:
            if pattern.match(request.path):
                return True

        return False

    def _is_infrastructure_failure_path(self, request: "HttpRequest") -> bool:
        """
        Check if request path is an infrastructure failure path.

        v6.1.0: 이 경로들에서 503이 발생하면 "진짜 인프라 장애"로 취급하여
        CB 실패를 더 강하게 기록합니다.
        """
        for pattern in self.INFRASTRUCTURE_FAILURE_PATHS:
            if pattern.match(request.path):
                return True
        return False

    def _is_cb_open(self) -> bool:
        """
        Check if CircuitBreaker is in OPEN state.

        v6.1.0: CB가 OPEN 상태이면 새 요청을 바로 DLQ에 저장하여
        시스템 부하를 줄이고 복구 후 자동 리플레이를 보장합니다.

        두 개의 CB를 모두 확인:
        1. CircuitBreakerService (database 서비스)
        2. PoolCircuitBreaker (싱글톤)
        """
        try:
            # 1. CircuitBreakerService 상태 확인
            if self._cb_service and self._cb_service.is_enabled:
                state = self._cb_service.get_state(self.CB_SERVICE_NAME)
                if state and state.lower() in ("open", "half_open"):
                    logger.debug(f"[SelfHealingMiddleware] CB service is {state.upper()} for {self.CB_SERVICE_NAME}")
                    return True

            # 2. PoolCircuitBreaker 상태 확인
            try:
                from selfhealing.api.django.pool_circuit_breaker import pool_circuit_breaker

                pool_state = pool_circuit_breaker.state
                if pool_state in ("OPEN", "HALF_OPEN"):
                    logger.debug(f"[SelfHealingMiddleware] PoolCB is {pool_state}")
                    return True
            except Exception:
                pass

            return False

        except Exception as e:
            logger.warning(f"[SelfHealingMiddleware] CB state check failed: {e}")
            return False

    def _record_cb_failure(
        self,
        error_context: Dict[str, Any],
        request: Optional["HttpRequest"] = None,
    ) -> None:
        """Record failure to CircuitBreaker.

        v6.1.0: PoolCircuitBreaker도 함께 업데이트하여
        테스트에서 /circuit-breaker/pool/status/ API로 상태 확인 가능

        Phase 3: request 파라미터 추가하여 AuditMiddleware 버퍼 패턴 지원
        """
        try:
            if self._cb_service and self._cb_service.is_enabled:
                self._cb_service.record_failure(
                    self.CB_SERVICE_NAME,
                    error_context=error_context,
                )
                logger.info(
                    f"[SelfHealingMiddleware] CB failure recorded: "
                    f"service={self.CB_SERVICE_NAME}, "
                    f"error_type={error_context.get('error_type')}"
                )

                # Audit 로그 기록 (Phase 3: 버퍼 패턴)
                self._log_audit_event(
                    "cb_failure_recorded",
                    {
                        "service": self.CB_SERVICE_NAME,
                        "error_context": error_context,
                    },
                    request=request,
                )
        except Exception as e:
            logger.error(f"[SelfHealingMiddleware] CB failure recording failed: {e}")

        # v6.1.0: PoolCircuitBreaker도 함께 실패 기록 (테스트 가시성)
        try:
            from selfhealing.api.django.pool_circuit_breaker import pool_circuit_breaker

            pool_circuit_breaker.record_failure()
            logger.info(
                f"[SelfHealingMiddleware] PoolCB failure recorded: "
                f"state={pool_circuit_breaker.state}, failures={pool_circuit_breaker._failure_count}"
            )
        except Exception as e:
            logger.warning(f"[SelfHealingMiddleware] PoolCB record_failure failed: {e}")

    def _record_cb_success(self) -> None:
        """Record success to CircuitBreaker."""
        try:
            if self._cb_service and self._cb_service.is_enabled:
                self._cb_service.record_success(self.CB_SERVICE_NAME)
        except Exception as e:
            # Success recording failure should not affect response
            pass

    def _store_to_dlq(
        self,
        request_data: Dict[str, Any],
        error_context: Dict[str, Any],
        request: Optional["HttpRequest"] = None,
    ) -> Optional[int]:
        """Store failed request to DLQ.

        Phase 3: request 파라미터 추가하여 AuditMiddleware 버퍼 패턴 지원
        """
        try:
            from selfhealing.services.dlq_service import store_to_dlq

            # 도메인 추론
            domain = self._infer_domain(request_data.get("path", ""))

            result = store_to_dlq(
                domain=domain,
                failure_type=error_context.get("error_type", "UNKNOWN"),
                entity_type="http_request",
                entity_id=request_data.get("headers", {}).get("x_idempotency_key", ""),
                user_id=request_data.get("user_id"),
                error_code=error_context.get("error_type", ""),
                error_message=error_context.get("error_message", ""),
                request_data=request_data,
                response_data={"status_code": 503},
                metadata={
                    "source": "SelfHealingMiddleware",
                    "auto_stored": True,
                    "path": request_data.get("path"),
                    "method": request_data.get("method"),
                },
                recommended_action="replay",
                next_action_hint="시스템 정상화 후 자동 리플레이 대상",
            )

            if result.success:
                logger.info(
                    f"[SelfHealingMiddleware] DLQ stored: "
                    f"id={result.dlq_id}, domain={domain}, "
                    f"path={request_data.get('path')}"
                )

                # Audit 로그 기록 (Phase 3: 버퍼 패턴)
                self._log_audit_event(
                    "dlq_auto_stored",
                    {
                        "dlq_id": result.dlq_id,
                        "domain": domain,
                        "path": request_data.get("path"),
                        "error_type": error_context.get("error_type"),
                    },
                    request=request,
                )

                return result.dlq_id
            else:
                logger.warning(f"[SelfHealingMiddleware] DLQ storage failed: {result.error}")
                return None

        except Exception as e:
            logger.error(f"[SelfHealingMiddleware] DLQ storage error: {e}")
            return None

    def _infer_domain(self, path: str) -> str:
        """
        Infer domain from request path using configurable mapping.

        Domain mapping is loaded from settings.SELF_HEALING_DOMAIN_MAPPING.
        Falls back to "http" if no match found.
        """
        # 설정 기반 도메인 매핑 사용 (Domain-Free)
        for pattern, domain in self.DOMAIN_MAPPING.items():
            if pattern in path:
                return domain

        return "http"

    def _log_audit_event(
        self,
        event_type: str,
        data: Dict[str, Any],
        request: Optional["HttpRequest"] = None,
    ) -> None:
        """
        Log event to audit system.

        Phase 3 변경:
        - request가 있으면 → RequestAuditBuffer에 적재 (AuditMiddleware에서 일괄 기록)
        - request가 없으면 → 기존 방식 유지 (직접 로깅)

        Args:
            event_type: 이벤트 유형 (dlq_auto_stored, cb_failure_recorded 등)
            data: 이벤트 데이터
            request: Django HttpRequest 객체 (있으면 버퍼에 적재)
        """
        # === Phase 3: 버퍼 패턴 우선 ===
        if request is not None:
            try:
                from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType

                # 이벤트 타입 매핑
                event_type_map = {
                    "preemptive_dlq_stored": AuditEventType.DLQ_STORE,
                    "dlq_auto_stored": AuditEventType.DLQ_STORE,
                    "cb_failure_recorded": AuditEventType.CB_STATE_CHANGE,
                }
                audit_event_type = event_type_map.get(event_type, AuditEventType.ERROR_DETECTED)

                buffer = RequestAuditBuffer.get_or_create(request)
                buffer.add(
                    event_type=audit_event_type,
                    source="SelfHealingMiddleware",
                    details={
                        "event_type": event_type,
                        **data,
                    },
                    success=True,
                )
                return  # 버퍼에 추가됨 - AuditMiddleware에서 기록
            except ImportError:
                pass  # event_buffer 사용 불가 - fallback

        # === Fallback: 기존 방식 ===
        try:
            if self._audit_logger:
                self._audit_logger.log(
                    {
                        "event_type": event_type,
                        "source": "SelfHealingMiddleware",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        **data,
                    }
                )
        except Exception as e:
            logger.warning(f"[SelfHealingMiddleware] Audit log failed: {e}")


# =============================================================================
# Self-Healing Recovery Event Logger
# =============================================================================


class SelfHealingRecoveryLogger:
    """
    Self-Healing 복구 이벤트 로거.

    DB 붕괴 → 서킷 오픈 → DLQ 적재 → 복구 → 리플레이 완료 과정을
    해시 체인으로 기록하여 감사 증적을 제공합니다.

    Usage:
        recovery_logger = SelfHealingRecoveryLogger()

        # 복구 이벤트 체인 시작
        chain_id = recovery_logger.start_recovery_chain(
            trigger="db_connection_exhausted",
            affected_services=["database"],
        )

        # 이벤트 기록
        recovery_logger.log_event(chain_id, "circuit_opened", {...})
        recovery_logger.log_event(chain_id, "dlq_items_stored", {...})
        recovery_logger.log_event(chain_id, "system_recovered", {...})
        recovery_logger.log_event(chain_id, "dlq_replay_completed", {...})

        # 체인 완료
        summary = recovery_logger.complete_chain(chain_id)
    """

    def __init__(self):
        """Initialize recovery logger."""
        self._chains: Dict[str, Dict[str, Any]] = {}
        self._audit_logger = None
        self._lock = None

    def _lazy_init(self) -> None:
        """Lazy initialization."""
        if self._lock is None:
            import threading

            self._lock = threading.Lock()

        if self._audit_logger is None:
            try:
                from selfhealing.audit import get_audit_logger

                self._audit_logger = get_audit_logger()
            except Exception:
                pass

    def start_recovery_chain(
        self,
        trigger: str,
        affected_services: list[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Start a new recovery event chain."""
        import uuid

        self._lazy_init()

        chain_id = f"recovery_{uuid.uuid4().hex[:12]}"

        chain_data = {
            "chain_id": chain_id,
            "trigger": trigger,
            "affected_services": affected_services,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "events": [],
            "metadata": metadata or {},
            "status": "in_progress",
        }

        with self._lock:
            self._chains[chain_id] = chain_data

        # 시작 이벤트 기록
        self.log_event(
            chain_id,
            "recovery_chain_started",
            {
                "trigger": trigger,
                "affected_services": affected_services,
            },
        )

        return chain_id

    def log_event(
        self,
        chain_id: str,
        event_type: str,
        data: Dict[str, Any],
        request: Optional["HttpRequest"] = None,
    ) -> bool:
        """
        Log an event to the recovery chain.

        Phase 3: request 파라미터 추가하여 AuditMiddleware 버퍼 패턴 지원
        (복구 체인은 대부분 비동기 컨텍스트에서 실행되므로 request가 없는 경우가 많음)
        """
        self._lazy_init()

        with self._lock:
            chain = self._chains.get(chain_id)
            if not chain:
                logger.warning(f"[RecoveryLogger] Chain not found: {chain_id}")
                return False

            event = {
                "sequence": len(chain["events"]) + 1,
                "event_type": event_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": data,
            }

            chain["events"].append(event)

        # === Phase 3: 버퍼 패턴 우선 ===
        if request is not None:
            try:
                from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType

                buffer = RequestAuditBuffer.get_or_create(request)
                buffer.add(
                    event_type=AuditEventType.RECOVERY_EVENT,
                    source="SelfHealingRecoveryLogger",
                    details={
                        "recovery_chain_id": chain_id,
                        "event_type": f"recovery_{event_type}",
                        "sequence": event["sequence"],
                        **data,
                    },
                    success=True,
                )
                return True  # 버퍼에 추가됨 - AuditMiddleware에서 기록
            except ImportError:
                pass  # event_buffer 사용 불가 - fallback

        # === Fallback: 기존 방식 ===
        try:
            if self._audit_logger:
                self._audit_logger.log(
                    {
                        "recovery_chain_id": chain_id,
                        "event_type": f"recovery_{event_type}",
                        "sequence": event["sequence"],
                        "source": "SelfHealingRecoveryLogger",
                        **data,
                    }
                )
        except Exception as e:
            logger.warning(f"[RecoveryLogger] Audit log failed: {e}")

        return True

    def complete_chain(
        self,
        chain_id: str,
        success: bool = True,
        summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Complete a recovery chain and return summary."""
        self._lazy_init()

        with self._lock:
            chain = self._chains.get(chain_id)
            if not chain:
                return {"error": f"Chain not found: {chain_id}"}

            chain["status"] = "completed" if success else "failed"
            chain["completed_at"] = datetime.now(timezone.utc).isoformat()
            chain["success"] = success

            # 소요 시간 계산
            started = datetime.fromisoformat(chain["started_at"].replace("Z", "+00:00"))
            completed = datetime.fromisoformat(chain["completed_at"].replace("Z", "+00:00"))
            chain["duration_seconds"] = (completed - started).total_seconds()

            if summary:
                chain["summary"] = summary

        # 완료 이벤트 기록
        self.log_event(
            chain_id,
            "recovery_chain_completed",
            {
                "success": success,
                "duration_seconds": chain["duration_seconds"],
                "total_events": len(chain["events"]),
                "summary": summary or {},
            },
        )

        return chain

    def get_chain(self, chain_id: str) -> Optional[Dict[str, Any]]:
        """Get a recovery chain by ID."""
        self._lazy_init()
        with self._lock:
            return self._chains.get(chain_id)

    def generate_audit_report(self, chain_id: str) -> Dict[str, Any]:
        """Generate audit report for a recovery chain."""
        chain = self.get_chain(chain_id)
        if not chain:
            return {"error": f"Chain not found: {chain_id}"}

        # 이벤트 타임라인 생성
        timeline = []
        for event in chain.get("events", []):
            timeline.append(
                {
                    "sequence": event["sequence"],
                    "event": event["event_type"],
                    "time": event["timestamp"],
                    "data_summary": {k: v for k, v in event.get("data", {}).items() if not k.startswith("_")},
                }
            )

        return {
            "chain_id": chain_id,
            "status": chain.get("status"),
            "trigger": chain.get("trigger"),
            "affected_services": chain.get("affected_services"),
            "started_at": chain.get("started_at"),
            "completed_at": chain.get("completed_at"),
            "duration_seconds": chain.get("duration_seconds"),
            "success": chain.get("success"),
            "total_events": len(timeline),
            "timeline": timeline,
            "summary": chain.get("summary", {}),
            "hash_chain_enabled": self._audit_logger is not None,
        }


# Singleton instance
_recovery_logger: SelfHealingRecoveryLogger | None = None


def get_recovery_logger() -> SelfHealingRecoveryLogger:
    """Get the singleton recovery logger instance."""
    global _recovery_logger
    if _recovery_logger is None:
        _recovery_logger = SelfHealingRecoveryLogger()
    return _recovery_logger


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Health Bridge
    "HealthBridgeMiddleware",
    # Access Logging
    "SensitiveEndpointAccessLogger",
    "SensitiveAccessLoggingMiddleware",
    "AccessLogEntry",
    "SENSITIVE_ENDPOINT_PATTERNS",
    # Self-Healing
    "SelfHealingMiddleware",
    "SelfHealingRecoveryLogger",
    "get_recovery_logger",
    # Fail-Secure Permissions
    "FailSecureIsAuthenticated",
    "FailSecureIsAdminUser",
]
