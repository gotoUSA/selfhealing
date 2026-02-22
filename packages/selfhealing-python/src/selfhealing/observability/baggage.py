"""
OTel Baggage 통합 전파.

W3C Baggage Propagator 설정 및
ContextVar ↔ OTel Baggage 양방향 동기화 로직.

Baggage가 활성화되면 RequestsInstrumentor의 inject() 실행 시
traceparent + baggage 헤더가 함께 outgoing HTTP 요청에 전파된다.
"""

from __future__ import annotations

import importlib
import structlog
from functools import lru_cache
from typing import Any

logger = structlog.get_logger()

# Baggage 키 접두사 — selfhealing 네임스페이스
BAGGAGE_PREFIX = "selfhealing"

# ContextVar 매핑 — Baggage 키별 getter(읽기)와 contextvar(쓰기) 경로
# 단일 소스로 관리하여 sync/restore 비대칭 방지
# 지연 import로 순환 의존 방지
_CONTEXTVAR_BAGGAGE_MAP: dict[str, dict[str, str]] = {
    "cell_id": {
        "getter": "selfhealing.context.cell_context:get_current_cell_id",
        "contextvar": "selfhealing.context.cell_context:_current_cell_id",
    },
    "domain": {
        "getter": "selfhealing.decorators.domain_tag:get_current_domain",
        "contextvar": "selfhealing.decorators.domain_tag:_current_domain",
    },
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
        logger.info("otel.baggage_propagation_enabled")
    except ImportError:
        logger.debug("otel.propagation_packages_installed")
    except Exception as e:
        logger.warning(
            "failed_setup_baggage_propagation",
            error=e,
        )


@lru_cache(maxsize=None)
def _resolve_import(path: str) -> Any:
    """
    'module.path:attribute_name' 문자열에서 attribute를 동적 import하고 캐싱.

    순환 의존 방지를 위해 최초 호출 시에만 지연 import 실행.
    이후 호출은 lru_cache에서 즉시 반환.
    """
    module_path, attr_name = path.rsplit(":", 1)
    module = importlib.import_module(module_path)
    return getattr(module, attr_name)


def sync_contextvars_to_baggage() -> object | None:
    """
    현재 ContextVar 값을 OTel Baggage에 동기화.

    Django 미들웨어 또는 SelfHealingHttpClient의 pre-request hook에서 호출.
    ContextVar 값이 None이면 해당 Baggage 항목은 설정하지 않는다.

    Returns:
        OTel context token — 반드시 context.detach(token)으로 해제해야 함.
        OTel 미설치 시 None.
    """
    try:
        from opentelemetry import baggage, context

        ctx = context.get_current()

        for key, entry in _CONTEXTVAR_BAGGAGE_MAP.items():
            try:
                getter = _resolve_import(entry["getter"])
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

    _CONTEXTVAR_BAGGAGE_MAP의 contextvar 경로를 사용하여
    sync_contextvars_to_baggage()와 동일한 매핑에서 읽고 쓴다.

    DjangoInstrumentor가 baggage HTTP 헤더를 OTel Context에 적재한 후
    호출되어야 유효한 값을 읽을 수 있다.

    Django BaggageSyncMiddleware 또는 Celery task_prerun에서 호출.
    """
    try:
        from opentelemetry import baggage
    except ImportError:
        return

    for key, entry in _CONTEXTVAR_BAGGAGE_MAP.items():
        value = baggage.get_baggage(f"{BAGGAGE_PREFIX}.{key}")
        if value:
            try:
                contextvar = _resolve_import(entry["contextvar"])
                contextvar.set(value)
            except Exception:
                logger.debug("Failed to restore ContextVar '%s' from baggage", key, exc_info=True)
