"""
IPC 요청 핸들러 - 기존 서비스 래핑.

UDS 서버 및 gRPC 서버에서 공통으로 사용할 수 있는
요청 처리 및 서비스 라우팅 레이어를 제공합니다.

특징:
- 서비스 인스턴스 Lazy Loading
- 핸들러 플러그인 방식 등록
- 공통 에러 처리

Usage:
    from selfhealing.adapters.ipc.request_handler import RequestHandler

    handler = RequestHandler()

    # Circuit Breaker 요청
    result = handler.handle(
        "circuit_breaker.should_allow",
        {"service_name": "payment_gateway"}
    )

    # DLQ 저장 요청
    result = handler.handle(
        "dlq.store",
        {"domain": "order", "failure_type": "timeout", ...}
    )
"""

from __future__ import annotations

import json
import structlog
from typing import Any, Callable

from selfhealing.adapters.ipc.exceptions import (
    IPCInvalidParamsError,
    IPCMethodNotFoundError,
    IPCServiceUnavailableError,
)

logger = structlog.get_logger()


class RequestHandler:
    """
    IPC 요청을 기존 selfhealing 서비스로 라우팅.

    UDS/gRPC 양쪽에서 공통으로 사용하며,
    플러그인 방식으로 핸들러를 등록하여 확장 가능합니다.
    """

    def __init__(self):
        """핸들러 초기화."""
        # 서비스 인스턴스 (Lazy loading)
        self._cb_service: Any = None
        self._dlq_service: Any = None
        self._learning_service: Any = None

        # 핸들러 레지스트리
        self._handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
        self._register_default_handlers()

    # =========================================================================
    # Service Lazy Loading
    # =========================================================================

    @property
    def cb_service(self) -> Any:
        """CircuitBreakerService 인스턴스."""
        if self._cb_service is None:
            try:
                from selfhealing.services import get_circuit_breaker_service

                self._cb_service = get_circuit_breaker_service()
            except ImportError as e:
                logger.warning(
                    "request_handler.circuitbreakerservice_available",
                    error=e,
                )
        return self._cb_service

    @property
    def dlq_service(self) -> Any:
        """DLQService 인스턴스."""
        if self._dlq_service is None:
            try:
                from selfhealing.services import get_dlq_service

                self._dlq_service = get_dlq_service()
            except ImportError as e:
                logger.warning(
                    "request_handler.dlqservice_available",
                    error=e,
                )
        return self._dlq_service

    @property
    def learning_service(self) -> Any:
        """LearningService 인스턴스."""
        if self._learning_service is None:
            try:
                from selfhealing.services.learning import LearningService

                self._learning_service = LearningService()
            except ImportError as e:
                logger.warning(
                    "request_handler.learningservice_available",
                    error=e,
                )
        return self._learning_service

    # =========================================================================
    # Handler Registration
    # =========================================================================

    def _register_default_handlers(self) -> None:
        """기본 핸들러 등록."""
        self._handlers = {
            # Circuit Breaker
            "circuit_breaker.should_allow": self._cb_should_allow,
            "circuit_breaker.should_allow_batch": self._cb_should_allow_batch,
            "circuit_breaker.get_state": self._cb_get_state,
            "circuit_breaker.get_all_states": self._cb_get_all_states,
            "circuit_breaker.force_open": self._cb_force_open,
            "circuit_breaker.force_close": self._cb_force_close,
            # DLQ
            "dlq.store": self._dlq_store,
            "dlq.is_enabled": self._dlq_is_enabled,
            "dlq.get_entry": self._dlq_get_entry,
            "dlq.list": self._dlq_list,
            # Learning
            "learning.get_suggestions": self._learning_get_suggestions,
            "learning.record_success": self._learning_record_success,
            "learning.record_failure": self._learning_record_failure,
            # Health
            "health.check": self._health_check,
        }

    def register_handler(
        self,
        method: str,
        handler: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        """
        커스텀 핸들러 등록.

        Args:
            method: 메서드 이름 (예: "custom.my_method")
            handler: 핸들러 함수
        """
        self._handlers[method] = handler
        logger.debug(
            "cell_registry.bulkheads_registered",
            method=method,
        )

    def get_registered_methods(self) -> list[str]:
        """등록된 메서드 목록 반환."""
        return list(self._handlers.keys())

    # =========================================================================
    # Request Handling
    # =========================================================================

    def handle(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        요청 처리 및 라우팅.

        Args:
            method: JSON-RPC 메서드 이름
            params: 요청 파라미터

        Returns:
            처리 결과 딕셔너리

        Raises:
            IPCMethodNotFoundError: 메서드를 찾을 수 없을 때
            IPCInvalidParamsError: 파라미터가 잘못되었을 때
        """
        handler = self._handlers.get(method)
        if handler is None:
            raise IPCMethodNotFoundError(method)

        try:
            return handler(params)
        except IPCMethodNotFoundError:
            raise
        except IPCInvalidParamsError:
            raise
        except KeyError as e:
            raise IPCInvalidParamsError(f"Missing required parameter: {e}", param_name=str(e))
        except ValueError as e:
            raise IPCInvalidParamsError(str(e))

    # =========================================================================
    # Circuit Breaker Handlers
    # =========================================================================

    def _cb_should_allow(self, params: dict[str, Any]) -> dict[str, Any]:
        """서킷 브레이커 요청 허용 여부 확인."""
        service_name = params.get("service_name")
        if not service_name:
            raise IPCInvalidParamsError("service_name is required", param_name="service_name")

        if self.cb_service is None:
            # Fail-open: 서비스 없으면 허용
            return {"allowed": True, "state": "closed"}

        allowed = self.cb_service.should_allow(service_name)
        state = self.cb_service.get_state(service_name)

        return {
            "allowed": allowed,
            "state": state,
        }

    def _cb_should_allow_batch(self, params: dict[str, Any]) -> dict[str, Any]:
        """다수 서비스에 대한 요청 허용 여부 일괄 확인."""
        service_names = params.get("service_names", [])
        if not service_names:
            return {"results": {}}

        # 최대 100개 제한
        service_names = service_names[:100]
        results = {}

        for name in service_names:
            results[name] = self._cb_should_allow({"service_name": name})

        return {"results": results}

    def _cb_get_state(self, params: dict[str, Any]) -> dict[str, Any]:
        """서킷 브레이커 상태 조회."""
        service_name = params.get("service_name")
        if not service_name:
            raise IPCInvalidParamsError("service_name is required", param_name="service_name")

        if self.cb_service is None:
            return {"service_name": service_name, "state": "closed"}

        state_data = self.cb_service.get_or_create_state(service_name)
        return {
            "service_name": service_name,
            "state": state_data.state,
            "failure_count": getattr(state_data, "failure_count", 0),
            "success_count": getattr(state_data, "success_count", 0),
            "opened_at": (state_data.opened_at.isoformat() if getattr(state_data, "opened_at", None) else None),
            "controlled_by": getattr(state_data, "controlled_by", None),
            "reason": getattr(state_data, "reason", None),
        }

    def _cb_get_all_states(self, params: dict[str, Any]) -> dict[str, Any]:
        """모든 서킷 브레이커 상태 목록 조회."""
        if self.cb_service is None:
            return {"states": []}

        states = self.cb_service.get_all_states()
        return {"states": states}

    def _cb_force_open(self, params: dict[str, Any]) -> dict[str, Any]:
        """서킷 브레이커 강제 열기 (요청 차단)."""
        service_name = params.get("service_name")
        if not service_name:
            raise IPCInvalidParamsError("service_name is required", param_name="service_name")

        reason = params.get("reason", "IPC request")
        controlled_by = params.get("controlled_by", "sidecar")

        if self.cb_service is None:
            raise IPCServiceUnavailableError("circuit_breaker")

        result = self.cb_service.force_open(
            service_name=service_name,
            reason=reason,
            controlled_by=controlled_by,
        )

        return {
            "success": result.success,
            "message": result.message,
            "state": result.new_state,
        }

    def _cb_force_close(self, params: dict[str, Any]) -> dict[str, Any]:
        """서킷 브레이커 강제 닫기 (요청 허용)."""
        service_name = params.get("service_name")
        if not service_name:
            raise IPCInvalidParamsError("service_name is required", param_name="service_name")

        reason = params.get("reason", "IPC request")
        controlled_by = params.get("controlled_by", "sidecar")
        trigger_replay = params.get("trigger_replay", False)

        if self.cb_service is None:
            raise IPCServiceUnavailableError("circuit_breaker")

        result = self.cb_service.force_close(
            service_name=service_name,
            reason=reason,
            controlled_by=controlled_by,
            trigger_replay=trigger_replay,
        )

        return {
            "success": result.success,
            "message": result.message,
            "state": result.new_state,
        }

    # =========================================================================
    # DLQ Handlers
    # =========================================================================

    def _dlq_store(self, params: dict[str, Any]) -> dict[str, Any]:
        """실패 작업을 DLQ에 저장."""
        domain = params.get("domain")
        failure_type = params.get("failure_type")
        error_message = params.get("error_message", "")

        if not domain:
            raise IPCInvalidParamsError("domain is required", param_name="domain")
        if not failure_type:
            raise IPCInvalidParamsError("failure_type is required", param_name="failure_type")

        if self.dlq_service is None:
            raise IPCServiceUnavailableError("dlq")

        # JSON 바이트 처리
        snapshot_data = params.get("snapshot_data", {})
        if isinstance(snapshot_data, bytes):
            snapshot_data = json.loads(snapshot_data.decode("utf-8"))

        request_data = params.get("request_data")
        if isinstance(request_data, bytes):
            request_data = json.loads(request_data.decode("utf-8"))

        result = self.dlq_service.store_with_snapshot(
            domain=domain,
            failure_type=failure_type,
            error_code=params.get("error_code", ""),
            error_message=error_message,
            snapshot_data=snapshot_data,
            request_data=request_data,
            entity_type=params.get("entity_type"),
            entity_id=params.get("entity_id"),
            user_id=params.get("user_id"),
            max_retries=params.get("max_retries"),
        )

        return {
            "success": result.success,
            "dlq_id": result.entry_id,
            "message": result.message,
        }

    def _dlq_is_enabled(self, params: dict[str, Any]) -> dict[str, Any]:
        """DLQ 활성화 여부 확인."""
        if self.dlq_service is None:
            return {"enabled": False}
        return {"enabled": self.dlq_service.is_enabled}

    def _dlq_get_entry(self, params: dict[str, Any]) -> dict[str, Any]:
        """DLQ 엔트리 조회."""
        entry_id = params.get("entry_id")
        if not entry_id:
            raise IPCInvalidParamsError("entry_id is required", param_name="entry_id")

        if self.dlq_service is None:
            raise IPCServiceUnavailableError("dlq")

        entry = self.dlq_service.get_entry(entry_id)
        if entry is None:
            return {"error": f"Entry not found: {entry_id}"}

        return {
            "id": entry.id,
            "domain": entry.domain,
            "failure_type": entry.failure_type,
            "error_message": entry.error_message,
            "status": entry.status,
            "retry_count": entry.retry_count,
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
        }

    def _dlq_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """DLQ 엔트리 목록 조회."""
        if self.dlq_service is None:
            return {"entries": [], "total_count": 0}

        # 실제 서비스 인터페이스에 맞춘 필터 파라미터 구성
        filters: dict[str, Any] = {}
        if params.get("domain"):
            filters["domain"] = params["domain"]
        if params.get("status"):
            filters["status"] = params["status"]

        page = params.get("page", 1)
        page_size = params.get("page_size", 20)

        result = self.dlq_service.list_entries(
            filters=filters,
            page=page,
            page_size=page_size,
        )

        # 결과 변환
        entries = result.get("results", [])
        return {
            "entries": [
                {
                    "id": e.get("id") if isinstance(e, dict) else getattr(e, "id", None),
                    "domain": e.get("domain") if isinstance(e, dict) else getattr(e, "domain", None),
                    "failure_type": (e.get("failure_type") if isinstance(e, dict) else getattr(e, "failure_type", None)),
                    "status": e.get("status") if isinstance(e, dict) else getattr(e, "status", None),
                    "created_at": (
                        e.get("created_at")
                        if isinstance(e, dict)
                        else (
                            getattr(e, "created_at", None).isoformat() if hasattr(e, "created_at") and e.created_at else None
                        )
                    ),
                }
                for e in entries
            ],
            "total_count": result.get("total_count", len(entries)),
        }

    # =========================================================================
    # Learning Handlers
    # =========================================================================

    def _learning_get_suggestions(self, params: dict[str, Any]) -> dict[str, Any]:
        """학습 기반 개선 제안 조회."""
        if self.learning_service is None:
            return {"suggestions": []}

        # 실제 LearningService 인터페이스 파라미터
        stage_name = params.get("stage_name")
        unapplied_only = params.get("unapplied_only", False)

        suggestions = self.learning_service.get_suggestions(
            stage_name=stage_name,
            unapplied_only=unapplied_only,
        )
        return {
            "suggestions": [
                {
                    "id": getattr(s, "id", None),
                    "type": getattr(s, "suggestion_type", getattr(s, "type", None)),
                    "description": getattr(s, "description", ""),
                    "priority": (getattr(s.priority, "value", s.priority) if hasattr(s, "priority") else None),
                    "confidence": getattr(s, "confidence", None),
                }
                for s in suggestions
            ]
        }

    def _learning_record_success(self, params: dict[str, Any]) -> dict[str, Any]:
        """성공 패턴 기록."""
        pattern_type = params.get("pattern_type")
        if not pattern_type:
            raise IPCInvalidParamsError("pattern_type is required", param_name="pattern_type")

        if self.learning_service is None:
            return {"recorded": False}

        context = params.get("context", {})
        if isinstance(context, bytes):
            context = json.loads(context.decode("utf-8"))

        self.learning_service.record_success(pattern_type, context)
        return {"recorded": True}

    def _learning_record_failure(self, params: dict[str, Any]) -> dict[str, Any]:
        """실패 패턴 기록."""
        pattern_type = params.get("pattern_type")
        if not pattern_type:
            raise IPCInvalidParamsError("pattern_type is required", param_name="pattern_type")

        if self.learning_service is None:
            return {"recorded": False}

        context = params.get("context", {})
        if isinstance(context, bytes):
            context = json.loads(context.decode("utf-8"))

        error = params.get("error", "")
        self.learning_service.record_failure(pattern_type, context, error)
        return {"recorded": True}

    # =========================================================================
    # Health Handlers
    # =========================================================================

    def _health_check(self, params: dict[str, Any]) -> dict[str, Any]:
        """사이드카 건강 상태 확인."""
        components = {
            "circuit_breaker": self.cb_service is not None,
            "dlq": self.dlq_service is not None,
            "learning": self.learning_service is not None,
        }

        all_healthy = any(components.values())
        status = "SERVING" if all_healthy else "NOT_SERVING"

        return {
            "status": status,
            "components": components,
        }


# 싱글톤 인스턴스
_request_handler: RequestHandler | None = None


def get_request_handler() -> RequestHandler:
    """RequestHandler 싱글톤 인스턴스 반환."""
    global _request_handler
    if _request_handler is None:
        _request_handler = RequestHandler()
    return _request_handler


def reset_request_handler() -> None:
    """RequestHandler 싱글톤 초기화 (테스트용)."""
    global _request_handler
    _request_handler = None
