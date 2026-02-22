"""
OTel Baggage 통합 전파.

W3C Baggage Propagator 설정 및
ContextVar ↔ OTel Baggage 양방향 동기화 로직.

Baggage가 활성화되면 RequestsInstrumentor의 inject() 실행 시
traceparent + baggage 헤더가 함께 outgoing HTTP 요청에 전파된다.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Baggage 키 접두사 — selfhealing 네임스페이스
BAGGAGE_PREFIX = "selfhealing"

# ContextVar getter 경로 매핑 (Baggage 키 → 모듈:함수)
# 지연 import로 순환 의존 방지
_CONTEXTVAR_BAGGAGE_MAP: dict[str, str] = {
    "cell_id": "selfhealing.context.cell_context:get_current_cell_id",
    "domain": "selfhealing.decorators.domain_tag:get_current_domain",
}


def setup_baggage_propagation() -> None:
    """
    W3C TraceContext + Baggage CompositePropagator 등록.

    이 함수 호출 후 RequestsInstrumentor가 inject()를 실행할 때
    traceparent + baggage 헤더가 함께 전파된다.

    호출 시점: initialize_opentelemetry() 성공 후
    """
    try:
        from opentelemetry import propagate
        from opentelemetry.baggage.propagation import W3CBaggagePropagator
        from opentelemetry.propagators.composite import CompositePropagator
        from opentelemetry.trace.propagation.tracecontext import (
            TraceContextTextMapPropagator,
        )

        propagate.set_global_textmap(
            CompositePropagator(
                [
                    TraceContextTextMapPropagator(),
                    W3CBaggagePropagator(),
                ]
            )
        )
        logger.info("OTel Baggage propagation enabled (W3C TraceContext + Baggage)")
    except ImportError:
        logger.debug("OpenTelemetry propagation packages not installed — baggage disabled")
    except Exception as e:
        logger.warning("Failed to setup baggage propagation: %s", e)


def _resolve_getter(getter_path: str) -> Any:
    """
    'module.path:function_name' 문자열에서 callable을 동적 import.

    순환 의존 방지를 위해 매 호출 시 지연 import.
    """
    module_path, func_name = getter_path.rsplit(":", 1)
    module = __import__(module_path, fromlist=[func_name])
    return getattr(module, func_name)


def sync_contextvars_to_baggage() -> object:
    """
    현재 ContextVar 값을 OTel Baggage에 동기화.

    Django 미들웨어 또는 SelfHealingHttpClient의 pre-request hook에서 호출.
    ContextVar 값이 None이면 해당 Baggage 항목은 설정하지 않는다.

    Returns:
        OTel context token — 반드시 context.detach(token)으로 해제해야 함
    """
    try:
        from opentelemetry import baggage, context

        ctx = context.get_current()

        for key, getter_path in _CONTEXTVAR_BAGGAGE_MAP.items():
            try:
                getter = _resolve_getter(getter_path)
                value = getter()
                if value is not None:
                    ctx = baggage.set_baggage(f"{BAGGAGE_PREFIX}.{key}", str(value), context=ctx)
            except Exception:
                # 개별 ContextVar 실패가 전체 동기화를 중단하지 않음
                logger.debug("Failed to sync ContextVar '%s' to baggage", key, exc_info=True)

        return context.attach(ctx)
    except ImportError:
        # OTel 미설치 — no-op token 반환
        return None


def detach_baggage_token(token: object) -> None:
    """
    sync_contextvars_to_baggage()가 반환한 token을 안전하게 해제.

    OTel 미설치 환경(token=None)에서도 에러 없이 동작.
    """
    if token is None:
        return
    try:
        from opentelemetry import context

        context.detach(token)
    except Exception:
        logger.debug("Failed to detach baggage token", exc_info=True)


def restore_contextvars_from_baggage() -> None:
    """
    수신된 OTel Baggage에서 ContextVar 값 복원.

    DjangoInstrumentor가 baggage HTTP 헤더를 OTel Context에 적재한 후
    호출되어야 유효한 값을 읽을 수 있다.

    Django BaggageSyncMiddleware 또는 Celery task_prerun에서 호출.
    """
    try:
        from opentelemetry import baggage
    except ImportError:
        return

    # cell_id 복원
    cell_id = baggage.get_baggage(f"{BAGGAGE_PREFIX}.cell_id")
    if cell_id:
        from selfhealing.context.cell_context import _current_cell_id

        _current_cell_id.set(cell_id)

    # domain 복원
    domain = baggage.get_baggage(f"{BAGGAGE_PREFIX}.domain")
    if domain:
        from selfhealing.decorators.domain_tag import _current_domain

        _current_domain.set(domain)
