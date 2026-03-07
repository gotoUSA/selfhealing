"""
Self-Healing Middleware (Stage 16 v5.0.0: HEALING PROOF)

자동 장애 감지 및 DLQ 적재를 위한 미들웨어입니다.

Features:
1. DB 오류 감지: OperationalError, InterfaceError 등
2. HTTP 5xx 오류 감지: 502, 503, 504
3. CircuitBreaker 자동 기록: 실패 발생 시 record_failure() 호출
4. DLQ 자동 적재: 복구 가능한 요청을 DLQ에 자동 저장
5. Self-Audit 연동: 해시 체인 로그에 이벤트 기록

Stage 16 v6.1.0 (HEALING PROOF Fix):
- stress 엔드포인트 503 에러를 인프라 장애로 인식 (신호 통합)
- CB OPEN 시 선제적 DLQ 적재 (자동 라우팅)
- 완전한 자율 치유 사이클 달성

CRITICAL: 이 Middleware는 HealthBridgeMiddleware 다음에 위치해야 합니다!

Usage in settings.py:
    MIDDLEWARE = [
        "selfhealing.api.django.middleware.HealthBridgeMiddleware",  # 최상단
        "selfhealing.api.django.middleware.SelfHealingMiddleware",   # 두 번째
        "django.middleware.security.SecurityMiddleware",
        ...
    ]
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

logger = structlog.get_logger()


class SelfHealingMiddleware:
    """
    Self-Healing Middleware for automatic failure detection and DLQ storage.
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
    DLQ_ELIGIBLE_PATHS: list = []  # _load_path_patterns()에서 초기화

    # 인프라 장애로 인식할 경로 패턴 - 설정에서 로드 (Domain-Free)
    INFRASTRUCTURE_FAILURE_PATHS: list = []  # _load_path_patterns()에서 초기화

    # 도메인 추론 매핑 - 설정에서 로드 (Domain-Free)
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
            logger.warning(
                "self_healing_middleware.cb_service_init_failed",
                error=e,
            )

        try:
            from selfhealing.audit import get_audit_logger

            self._audit_logger = get_audit_logger()
        except Exception as e:
            logger.warning(
                "self_healing_middleware.audit_logger_init_failed",
                error=e,
            )

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
            "self_healing_middleware.loaded_patterns",
            dlq_eligible_paths_count=len(cls.DLQ_ELIGIBLE_PATHS),
            infra_paths_count=len(cls.INFRASTRUCTURE_FAILURE_PATHS),
            domain_mapping_count=len(cls.DOMAIN_MAPPING),
        )

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Process request/response with self-healing logic."""
        from django.http import JsonResponse

        self._lazy_init()

        request_data = self._capture_request_data(request)

        # =====================================================================
        # v6.1.0: CB OPEN 상태에서 선제적 DLQ 적재 (자동 라우팅)
        # =====================================================================
        if self._is_cb_open() and self._is_dlq_eligible(request):
            error_context = {
                "error_type": "CIRCUIT_BREAKER_OPEN",
                "error_message": "Circuit breaker is OPEN - request queued for later retry",
                "path": request.path,
                "method": request.method,
                "preemptive": True,
            }

            dlq_id = self._store_to_dlq(request_data, error_context, request=request)

            logger.info(
                "self_healing_middleware.preemptive_dlq_cb_open",
                dlq_id=dlq_id,
                request_path=request.path,
            )

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
            response = self.get_response(request)

        except Exception as e:
            error_type = type(e).__name__

            if error_type in self.MONITORED_DB_ERRORS or self._is_db_connection_error(e):
                db_error_context = {
                    "error_type": error_type,
                    "error_message": str(e),
                    "path": request.path,
                    "method": request.method,
                }

                self._record_cb_failure(db_error_context, request=request)

                if self._is_dlq_eligible(request):
                    self._store_to_dlq(request_data, db_error_context, request=request)

                return JsonResponse(
                    {
                        "error": "Service temporarily unavailable",
                        "code": "DB_CONNECTION_ERROR",
                        "retry_after": 30,
                        "dlq_stored": self._is_dlq_eligible(request),
                    },
                    status=503,
                )

            raise

        # HTTP 5xx 응답 감지
        if response.status_code in self.MONITORED_STATUS_CODES:
            is_infra_failure_path = self._is_infrastructure_failure_path(request)

            error_context = {
                "error_type": f"HTTP_{response.status_code}",
                "error_message": f"Server returned {response.status_code}",
                "path": request.path,
                "method": request.method,
                "infrastructure_failure": is_infra_failure_path,
            }

            self._record_cb_failure(error_context, request=request)

            if is_infra_failure_path:
                logger.warning(
                    "self_healing_middleware.infra_failure_detected",
                    request_path=request.path,
                    response=response.status_code,
                )

            if self._is_dlq_eligible(request):
                self._store_to_dlq(request_data, error_context, request=request)

        else:
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

    def _capture_request_data(self, request: HttpRequest) -> dict[str, Any]:
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
                "user_id": (getattr(request.user, "id", None) if hasattr(request, "user") else None),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            logger.warning(
                "self_healing_middleware.request_capture_failed",
                error=e,
            )
            return {"path": getattr(request, "path", "unknown"), "error": str(e)}

    def _is_dlq_eligible(self, request: HttpRequest) -> bool:
        """Check if request is eligible for DLQ storage."""
        if request.method not in ("POST", "PUT", "PATCH"):
            return False

        for pattern in self.DLQ_ELIGIBLE_PATHS:
            if pattern.match(request.path):
                return True

        return False

    def _is_infrastructure_failure_path(self, request: HttpRequest) -> bool:
        """Check if request path is an infrastructure failure path."""
        for pattern in self.INFRASTRUCTURE_FAILURE_PATHS:
            if pattern.match(request.path):
                return True
        return False

    def _is_cb_open(self) -> bool:
        """Check if CircuitBreaker is in OPEN state."""
        try:
            if self._cb_service and self._cb_service.is_enabled:
                state = self._cb_service.get_state(self.CB_SERVICE_NAME)
                if state and state.lower() in ("open", "half_open"):
                    logger.debug(
                        "self_healing_middleware.cb_service",
                        state=state.upper(),
                        cb_service_name=self.CB_SERVICE_NAME,
                    )
                    return True

            try:
                from selfhealing.api.django.pool_circuit_breaker import (
                    pool_circuit_breaker,
                )

                pool_state = pool_circuit_breaker.state
                if pool_state in ("OPEN", "HALF_OPEN"):
                    logger.debug(
                        "self_healing_middleware.poolcb",
                        pool_state=pool_state,
                    )
                    return True
            except Exception:
                pass

            return False

        except Exception as e:
            logger.warning(
                "self_healing_middleware.cb_state_check_failed",
                error=e,
            )
            return False

    def _record_cb_failure(
        self,
        error_context: dict[str, Any],
        request: HttpRequest | None = None,
    ) -> None:
        """Record failure to CircuitBreaker."""
        try:
            if self._cb_service and self._cb_service.is_enabled:
                self._cb_service.record_failure(
                    self.CB_SERVICE_NAME,
                    error_context=error_context,
                )
                logger.info(
                    "self_healing_middleware.cb_failure_recorded",
                    cb_service_name=self.CB_SERVICE_NAME,
                    error_context=error_context.get("error_type"),
                )

                self._log_audit_event(
                    "cb_failure_recorded",
                    {"service": self.CB_SERVICE_NAME, "error_context": error_context},
                    request=request,
                )
        except Exception as e:
            logger.exception(
                "self_healing_middleware.cb_failure_recording_failed",
                error=e,
            )

        try:
            from selfhealing.api.django.pool_circuit_breaker import pool_circuit_breaker

            pool_circuit_breaker.record_failure()
            logger.info(
                "self_healing_middleware.poolcb_failure_recorded",
                pool_circuit_breaker=pool_circuit_breaker.state,
                failure_count=pool_circuit_breaker._failure_count,
            )
        except Exception as e:
            logger.warning(
                "self_healing_middleware.poolcb_failed",
                error=e,
            )

    def _record_cb_success(self) -> None:
        """Record success to CircuitBreaker."""
        try:
            if self._cb_service and self._cb_service.is_enabled:
                self._cb_service.record_success(self.CB_SERVICE_NAME)
        except Exception:
            pass

    def _store_to_dlq(
        self,
        request_data: dict[str, Any],
        error_context: dict[str, Any],
        request: HttpRequest | None = None,
    ) -> int | None:
        """Store failed request to DLQ."""
        try:
            from selfhealing.services.dlq import store_to_dlq

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
                    "self_healing_middleware.dlq_stored",
                    dlq_id=result.dlq_id,
                    healing_domain=domain,
                    request_data=request_data.get("path"),
                )

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
                logger.warning(
                    "self_healing_middleware.dlq_storage_failed",
                    result_error=result.error,
                )
                return None

        except Exception as e:
            logger.exception(
                "self_healing_middleware.dlq_storage_error",
                error=e,
            )
            return None

    def _infer_domain(self, path: str) -> str:
        """Infer domain from request path using configurable mapping."""
        for pattern, domain in self.DOMAIN_MAPPING.items():
            if pattern in path:
                return domain
        return "http"

    def _log_audit_event(
        self,
        event_type: str,
        data: dict[str, Any],
        request: HttpRequest | None = None,
    ) -> None:
        """Log event to audit system."""
        # === 버퍼 패턴 우선 ===
        if request is not None:
            try:
                from selfhealing.audit.event_buffer import (
                    AuditEventType,
                    RequestAuditBuffer,
                )

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
                    details={"event_type": event_type, **data},
                    success=True,
                )
                return
            except ImportError:
                pass

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
            logger.warning(
                "self_healing_middleware.audit_log_failed",
                error=e,
            )
