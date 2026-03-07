"""
Distributed Tracing for Circuit Breaker

CB 상태 변화 시 해당 상태 변화를 유발한 '마지막 요청'의 trace_id를 감사 로그에 기록합니다.
운영자가 "서킷이 왜 열렸지?"라고 물었을 때, 로그의 trace_id 하나로
전체 서비스 호출 흐름을 1초 만에 시각화할 수 있습니다.

운영 가치:
- "서킷이 왜 열렸지?" → trace_id 클릭 → 1초
- Root Cause 분석: 30분~1시간 → 1분 이내
- 서비스 간 연쇄 장애 추적: 불가능 → 전체 호출 그래프 시각화
- 감사 대응: "조사 중입니다" → trace_id로 즉시 증거 제시
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger()


# =============================================================================
# Tracing Configuration
# =============================================================================


@dataclass
class TracingConfig:
    """
    CB Audit Tracing 설정.

    Attributes:
        enabled: Tracing 활성화 여부
        captured_headers: 캡처할 trace 헤더 목록
        record_triggering_request: 상태 변화 시 마지막 요청 정보 기록 여부
        create_spans: OpenTelemetry Span 생성 여부
        trace_url_template: Trace 조회 URL 템플릿 (Jaeger/Zipkin용)
    """

    enabled: bool = True

    # 기록할 trace 헤더들 (기존 TraceContext 활용)
    captured_headers: list[str] = field(
        default_factory=lambda: [
            "X-Trace-ID",
            "X-Request-ID",
            "X-Correlation-ID",
            "traceparent",  # W3C Trace Context
            "X-Amzn-Trace-Id",  # AWS X-Ray
        ]
    )

    # 상태 변화 시 마지막 요청 정보 기록
    record_triggering_request: bool = True

    # Span 생성 여부 (OpenTelemetry 연동)
    create_spans: bool = True

    # Trace URL 템플릿 (Jaeger/Zipkin용)
    # {trace_id}가 실제 trace_id로 치환됨
    trace_url_template: str = ""

    @classmethod
    def from_env(cls) -> TracingConfig:
        """환경변수에서 설정 로드."""
        return cls(
            enabled=os.environ.get("CB_TRACING_ENABLED", "true").lower() == "true",
            record_triggering_request=os.environ.get("CB_TRACING_RECORD_REQUEST", "true").lower() == "true",
            create_spans=os.environ.get("CB_TRACING_CREATE_SPANS", "true").lower() == "true",
            trace_url_template=os.environ.get("CB_TRACE_URL_TEMPLATE", ""),
        )


def _is_otel_enabled() -> bool:
    """Check if OpenTelemetry is enabled."""
    try:
        from selfhealing.observability import is_otel_enabled

        return is_otel_enabled()
    except ImportError:
        return False


def _get_otel_trace_context() -> tuple[str | None, str | None]:
    """
    Get current OTEL trace_id and span_id.

    Returns:
        Tuple of (trace_id, span_id) or (None, None) if OTEL is not active.
    """
    try:
        from selfhealing.observability import (
            get_current_span_id_from_otel,
            get_current_trace_id_from_otel,
        )

        return get_current_trace_id_from_otel(), get_current_span_id_from_otel()
    except ImportError:
        return None, None


# =============================================================================
# Triggering Request Info
# =============================================================================


@dataclass
class TriggeringRequestInfo:
    """
    CB 상태 변화를 유발한 마지막 요청 정보.

    CB가 CLOSED → OPEN으로 전환될 때, 해당 전환을 유발한
    마지막 요청의 trace 정보를 캡처합니다.

    OTEL이 활성화된 경우 trace_id_full, span_id가 자동으로 채워집니다.

    Attributes:
        trace_id: 요청의 Trace ID (표시용, req-xxx 또는 8자 축약)
        trace_id_full: 전체 W3C trace_id (32자 hex, OTEL 활성화 시)
        span_id: 요청의 Span ID (16자 hex, OTEL 활성화 시)
        request_id: X-Request-ID 헤더 값 (선택)
        correlation_id: X-Correlation-ID 헤더 값 (선택)
        timestamp: 요청 시간
        endpoint: 요청 엔드포인트 (선택)
        method: HTTP 메서드 (선택)
        error_message: 에러 메시지 (선택)
        trace_url: 전체 추적 가능한 Jaeger/Zipkin URL (선택)

    Example:
        >>> info = TriggeringRequestInfo(
        ...     trace_id="abc123def456",
        ...     endpoint="/api/v1/payments",
        ...     method="POST",
        ...     error_message="Connection timeout to payment gateway",
        ... )
    """

    trace_id: str
    trace_id_full: str | None = None  # 전체 W3C trace_id (32자 hex)
    span_id: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None

    # 요청 메타데이터
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    endpoint: str | None = None
    method: str | None = None
    error_message: str | None = None

    # 전체 추적 가능한 링크
    trace_url: str | None = None  # Jaeger/Zipkin URL

    def __post_init__(self) -> None:
        """OTEL 활성화 시 trace_id_full, span_id 자동 채움."""
        if _is_otel_enabled() and (self.trace_id_full is None or self.span_id is None):
            otel_trace_id, otel_span_id = _get_otel_trace_context()
            if self.trace_id_full is None:
                self.trace_id_full = otel_trace_id
            if self.span_id is None:
                self.span_id = otel_span_id

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "trace_id": self.trace_id,
            "trace_id_full": self.trace_id_full,
            "span_id": self.span_id,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp,
            "endpoint": self.endpoint,
            "method": self.method,
            "error_message": self.error_message,
            "trace_url": self.trace_url,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TriggeringRequestInfo:
        """딕셔너리에서 생성."""
        return cls(
            trace_id=data.get("trace_id", ""),
            trace_id_full=data.get("trace_id_full"),
            span_id=data.get("span_id"),
            request_id=data.get("request_id"),
            correlation_id=data.get("correlation_id"),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            endpoint=data.get("endpoint"),
            method=data.get("method"),
            error_message=data.get("error_message"),
            trace_url=data.get("trace_url"),
        )

    @classmethod
    def from_current_otel_context(
        cls,
        endpoint: str | None = None,
        method: str | None = None,
        error_message: str | None = None,
    ) -> TriggeringRequestInfo:
        """
        현재 OTEL 컨텍스트에서 TriggeringRequestInfo 생성.

        OTEL이 활성화된 경우 현재 span의 trace_id, span_id를 자동으로 추출합니다.
        """
        trace_id = ""
        trace_id_full = None
        span_id = None

        if _is_otel_enabled():
            trace_id_full, span_id = _get_otel_trace_context()
            if trace_id_full:
                trace_id = f"req-{trace_id_full[:8]}"

        if not trace_id:
            try:
                from selfhealing.audit.trace import get_trace_id

                trace_id = get_trace_id()
            except ImportError:
                import uuid

                trace_id = f"req-{uuid.uuid4().hex[:8]}"

        return cls(
            trace_id=trace_id,
            trace_id_full=trace_id_full,
            span_id=span_id,
            endpoint=endpoint,
            method=method,
            error_message=error_message,
        )


# =============================================================================
# Trace Context Provider
# =============================================================================


class TraceContextProvider:
    """
    현재 요청의 Trace Context를 제공합니다.

    기존 selfhealing.audit.trace 모듈과 연동하여
    현재 요청의 trace_id, span_id 등을 추출합니다.

    Usage:
        provider = TraceContextProvider()

        # 현재 요청의 trace 정보 조회
        ctx = provider.get_current_context()
        print(ctx.trace_id)

        # Django request에서 trace 정보 추출
        ctx = provider.extract_from_request(request)
    """

    def __init__(self, config: TracingConfig | None = None):
        """
        Initialize TraceContextProvider.

        Args:
            config: Tracing 설정 (기본값 사용 시 None)
        """
        self.config = config or TracingConfig()

    def get_current_context(self) -> TriggeringRequestInfo:
        """
        현재 요청의 Trace Context 조회.

        기존 selfhealing.audit.trace 모듈의 get_trace_id() 활용.

        Returns:
            TriggeringRequestInfo: 현재 요청의 trace 정보
        """
        trace_id = self._get_trace_id()

        return TriggeringRequestInfo(
            trace_id=trace_id,
            span_id=self._get_span_id(),
            request_id=self._get_request_id(),
            trace_url=self._build_trace_url(trace_id),
        )

    def extract_from_request(
        self,
        request: Any,
        endpoint: str | None = None,
        method: str | None = None,
        error_message: str | None = None,
    ) -> TriggeringRequestInfo:
        """
        Django request에서 Trace Context 추출.

        Args:
            request: Django HttpRequest 객체
            endpoint: 요청 엔드포인트 (명시적 지정 가능)
            method: HTTP 메서드 (명시적 지정 가능)
            error_message: 에러 메시지 (선택)

        Returns:
            TriggeringRequestInfo: 요청의 trace 정보
        """
        # Django request에서 trace_id 추출
        trace_id = self._extract_trace_id_from_request(request)

        # 엔드포인트/메서드 추출
        if endpoint is None and hasattr(request, "path"):
            endpoint = request.path
        if method is None and hasattr(request, "method"):
            method = request.method

        return TriggeringRequestInfo(
            trace_id=trace_id,
            span_id=self._extract_span_id_from_request(request),
            request_id=self._extract_header(request, "HTTP_X_REQUEST_ID"),
            correlation_id=self._extract_header(request, "HTTP_X_CORRELATION_ID"),
            endpoint=endpoint,
            method=method,
            error_message=error_message,
            trace_url=self._build_trace_url(trace_id),
        )

    def _get_trace_id(self) -> str:
        """현재 trace_id 조회 (기존 모듈 활용)."""
        try:
            from selfhealing.audit.trace import get_trace_id

            return get_trace_id()
        except ImportError:
            import uuid

            return f"req-{uuid.uuid4().hex[:8]}"

    def _get_span_id(self) -> str | None:
        """현재 span_id 조회.

        Note:
            OpenTelemetry SDK 의존성 없이 trace_id만 전파합니다.
            span_id가 필요한 경우 W3C traceparent 헤더에서 추출합니다.
        """
        # span_id는 request context에서만 추출 가능
        # OTel SDK 없이는 직접 생성하지 않음 (minimal dependency policy)
        return None

    def _get_request_id(self) -> str | None:
        """현재 request_id 조회."""
        # Context variable에서 조회 시도
        try:
            from selfhealing.audit.trace import _trace_id_var

            return _trace_id_var.get()
        except (ImportError, AttributeError):
            pass
        return None

    def _extract_trace_id_from_request(self, request: Any) -> str:
        """Django request에서 trace_id 추출."""
        try:
            from selfhealing.audit.trace import (
                extract_trace_id_from_request,
                get_trace_id,
            )

            extracted = extract_trace_id_from_request(request)
            if extracted:
                return extracted
            return get_trace_id()
        except ImportError:
            import uuid

            return f"req-{uuid.uuid4().hex[:8]}"

    def _extract_span_id_from_request(self, request: Any) -> str | None:
        """Django request에서 span_id 추출 (W3C traceparent)."""
        traceparent = self._extract_header(request, "HTTP_TRACEPARENT")
        if traceparent:
            # Format: version-trace_id-span_id-flags (00-xxx-yyy-01)
            parts = traceparent.split("-")
            if len(parts) >= 3:
                return parts[2]
        return None

    def _extract_header(self, request: Any, header_name: str) -> str | None:
        """Django request에서 헤더 추출."""
        if request is None:
            return None
        meta = getattr(request, "META", {})
        return meta.get(header_name)

    def _build_trace_url(self, trace_id: str) -> str | None:
        """Trace 조회 URL 생성."""
        if not self.config.trace_url_template:
            return None
        return self.config.trace_url_template.replace("{trace_id}", trace_id)


# =============================================================================
# Circuit Breaker Tracing Manager
# =============================================================================


class CircuitBreakerTracingManager:
    """
    Circuit Breaker 상태 변화에 대한 Tracing 관리자.

    CB 상태 변화 시 triggering request 정보를 저장하고,
    Audit 로그에 trace_id를 포함하여 기록합니다.

    Usage:
        manager = CircuitBreakerTracingManager()

        # 실패 기록 시 trace 정보 저장
        manager.record_failure_with_trace(
            service_id="payment-api",
            error=error,
            request=request,
        )

        # CB OPEN 시 triggering request 정보 조회
        info = manager.get_triggering_request("payment-api")

        # Audit 로그에 trace 정보 포함하여 기록
        manager.log_state_change_with_trace(
            service_id="payment-api",
            previous_state="CLOSED",
            new_state="OPEN",
            trigger="AUTO_THRESHOLD",
        )

    Reference:
        docs/self_healing/middleware_system/21_CB_ADVANCED_PROTECTION.md
        Section 15 - Distributed Tracing 연동
    """

    _instance: CircuitBreakerTracingManager | None = None

    def __new__(cls):
        """싱글톤 패턴."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, config: TracingConfig | None = None):
        if getattr(self, "_initialized", False):
            return

        self.config = config or TracingConfig.from_env()
        self._trace_provider = TraceContextProvider(self.config)
        self._triggering_requests: dict[str, TriggeringRequestInfo] = {}
        self._initialized = True

    @classmethod
    def reset_instance(cls) -> None:
        """인스턴스 리셋 (테스트용)."""
        cls._instance = None

    def record_failure_with_trace(
        self,
        service_id: str,
        error: Exception | None = None,
        request: Any = None,
        endpoint: str | None = None,
        method: str | None = None,
    ) -> TriggeringRequestInfo:
        """
        실패 기록 시 trace 정보 저장.

        CB가 OPEN 전환 시 "마지막으로 실패한 요청"의 trace 정보를
        저장하여, OPEN 전환 Audit 로그에 포함합니다.

        Args:
            service_id: 서비스 ID
            error: 발생한 예외 (선택)
            request: Django HttpRequest 객체 (선택)
            endpoint: 요청 엔드포인트 (선택)
            method: HTTP 메서드 (선택)

        Returns:
            TriggeringRequestInfo: 저장된 trace 정보
        """
        if not self.config.enabled:
            return TriggeringRequestInfo(trace_id="disabled")

        error_message = str(error) if error else None

        if request is not None:
            info = self._trace_provider.extract_from_request(
                request=request,
                endpoint=endpoint,
                method=method,
                error_message=error_message,
            )
        else:
            ctx = self._trace_provider.get_current_context()
            info = TriggeringRequestInfo(
                trace_id=ctx.trace_id,
                span_id=ctx.span_id,
                request_id=ctx.request_id,
                endpoint=endpoint,
                method=method,
                error_message=error_message,
                trace_url=ctx.trace_url,
            )

        # 마지막 실패 요청 정보 저장
        self._triggering_requests[service_id] = info

        logger.debug(
            "cb_tracing.recorded_failure_trace",
            service_id=service_id,
            trace_id=info.trace_id,
        )

        return info

    def get_triggering_request(
        self,
        service_id: str,
    ) -> TriggeringRequestInfo | None:
        """
        서비스의 마지막 triggering request 정보 조회.

        Args:
            service_id: 서비스 ID

        Returns:
            TriggeringRequestInfo: 마지막 triggering request 정보 (없으면 None)
        """
        return self._triggering_requests.get(service_id)

    def clear_triggering_request(self, service_id: str) -> None:
        """
        서비스의 triggering request 정보 삭제.

        CB가 CLOSED 상태로 복구되면 호출.

        Args:
            service_id: 서비스 ID
        """
        self._triggering_requests.pop(service_id, None)

    def log_state_change_with_trace(
        self,
        service_id: str,
        previous_state: str,
        new_state: str,
        trigger: str,
        reason: str | None = None,
        request: Any = None,
    ) -> int | None:
        """
        CB 상태 변화를 trace 정보와 함께 Audit 로그에 기록.

        Args:
            service_id: 서비스 ID
            previous_state: 이전 상태
            new_state: 새 상태
            trigger: 트리거 유형 ("AUTO_THRESHOLD", "MANUAL", "CANARY_RECOVERY" 등)
            reason: 상태 변경 사유 (선택)
            request: Django HttpRequest 객체 (선택)

        Returns:
            WAL 시퀀스 번호 (성공 시), None (실패 시)
        """
        # Triggering request 정보 조회
        triggering_info = self.get_triggering_request(service_id)

        # 현재 trace context
        current_trace = self._trace_provider.get_current_context()

        # Audit 로그 기록
        try:
            from selfhealing.services.audit import (
                log_cb_state_change_with_trace_audit,
            )

            wal_seq = log_cb_state_change_with_trace_audit(
                cb_name=service_id,
                old_state=previous_state,
                new_state=new_state,
                trigger=trigger,
                reason=reason,
                trace_id=current_trace.trace_id,
                triggering_request_info=(triggering_info.to_dict() if triggering_info else None),
                request=request,
            )

            logger.info(
                "cb_tracing.state_change_logged",
                service_id=service_id,
                previous_state=previous_state,
                new_state=new_state,
                trigger=trigger,
                current_trace=current_trace.trace_id,
            )

            return wal_seq

        except ImportError:
            # Fallback: 기존 audit 함수 사용
            try:
                from selfhealing.services.audit import log_cb_state_change_audit

                reason_with_trace = reason or ""
                if triggering_info:
                    reason_with_trace += f" [trace_id={triggering_info.trace_id}]"

                return log_cb_state_change_audit(
                    cb_name=service_id,
                    old_state=previous_state,
                    new_state=new_state,
                    reason=reason_with_trace,
                    request=request,
                )
            except Exception as e:
                logger.warning(
                    "cb_tracing.audit_log_failed",
                    error=e,
                )
                return None

    def create_otel_span(
        self,
        service_id: str,
        previous_state: str,
        new_state: str,
        trigger: str,
    ) -> Any:
        """
        CB 상태 변화에 대한 OpenTelemetry Span 생성.

        OTEL SDK가 활성화된 경우 실제 Span을 생성하고,
        비활성화된 경우 None을 반환합니다.

        Args:
            service_id: 서비스 ID
            previous_state: 이전 상태
            new_state: 새 상태
            trigger: 트리거 유형

        Returns:
            Span 객체 (OTEL 활성화 시) 또는 None
        """
        if not self.config.create_spans:
            return None

        if not _is_otel_enabled():
            logger.debug("cb_tracing.otel_enabled_use_links")
            return None

        try:
            from selfhealing.observability import get_tracer

            tracer = get_tracer()
            if tracer is None:
                return None

            span = tracer.start_span(
                name=f"circuit_breaker.state_change.{service_id}",
                attributes={
                    "circuit_breaker.service_id": service_id,
                    "circuit_breaker.previous_state": previous_state,
                    "circuit_breaker.new_state": new_state,
                    "circuit_breaker.trigger": trigger,
                },
            )

            logger.debug(
                "cb_tracing.created_otel_span",
                service_id=service_id,
                previous_state=previous_state,
                new_state=new_state,
            )

            return span

        except ImportError:
            return None
        except Exception as e:
            logger.warning(
                "cb_tracing.failed_create_otel_span",
                error=e,
            )
            return None


# =============================================================================
# Module-Level Convenience Functions
# =============================================================================

_tracing_manager: CircuitBreakerTracingManager | None = None


def get_tracing_manager() -> CircuitBreakerTracingManager:
    """싱글톤 Tracing Manager 인스턴스 반환."""
    global _tracing_manager
    if _tracing_manager is None:
        _tracing_manager = CircuitBreakerTracingManager()
    return _tracing_manager


def record_failure_with_trace(
    service_id: str,
    error: Exception | None = None,
    request: Any = None,
    endpoint: str | None = None,
    method: str | None = None,
) -> TriggeringRequestInfo:
    """실패 기록 시 trace 정보 저장 (편의 함수)."""
    return get_tracing_manager().record_failure_with_trace(
        service_id=service_id,
        error=error,
        request=request,
        endpoint=endpoint,
        method=method,
    )


def get_triggering_request(service_id: str) -> TriggeringRequestInfo | None:
    """서비스의 마지막 triggering request 정보 조회 (편의 함수)."""
    return get_tracing_manager().get_triggering_request(service_id)


def log_state_change_with_trace(
    service_id: str,
    previous_state: str,
    new_state: str,
    trigger: str,
    reason: str | None = None,
    request: Any = None,
) -> int | None:
    """CB 상태 변화를 trace 정보와 함께 Audit 로그에 기록 (편의 함수)."""
    return get_tracing_manager().log_state_change_with_trace(
        service_id=service_id,
        previous_state=previous_state,
        new_state=new_state,
        trigger=trigger,
        reason=reason,
        request=request,
    )
