"""
Django Auto-Configuration for Self-Healing.

Provides configure_selfhealing() — an explicit wrapper that consumers call
at the bottom of their settings.py to inject selfhealing middleware groups,
set up DRF EXCEPTION_HANDLER, and optionally initialize OTEL.

Usage:
    # settings.py (last line)
    from selfhealing.adapters.django import configure_selfhealing
    configure_selfhealing(namespace=globals())
"""

from __future__ import annotations

import logging
import os

from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger("selfhealing")

# =========================================================================
# Middleware Group Defaults
# =========================================================================

DEFAULT_EARLY_GROUP = [
    "selfhealing.audit.trace.trace_id_middleware",
    "selfhealing.api.django.middleware.HealthBridgeMiddleware",
    "selfhealing.api.django.tiering.TieringMiddleware",
    "selfhealing.api.django.middleware.IPBanMiddleware",
    "selfhealing.api.django.middleware.SelfHealingMiddleware",
    "selfhealing.api.django.middleware.actor_context.ActorContextMiddleware",
]

DEFAULT_POST_AUTH_GROUP = [
    "selfhealing.api.django.cell.middleware.CellTaggingMiddleware",
    "selfhealing.api.django.cell.middleware.BaggageSyncMiddleware",
    "selfhealing.api.django.rate_limit.HybridRateLimitMiddleware",
    "selfhealing.api.django.pool_circuit_breaker.PoolCircuitBreakerMiddleware",
]

DEFAULT_TAIL_GROUP = [
    "selfhealing.api.django.audit_middleware.AuditMiddleware",
]

MIDDLEWARE_TOGGLES: dict[str, str] = {
    "selfhealing.api.django.tiering.TieringMiddleware": "SELFHEALING_TIERING_MIDDLEWARE_ENABLED",
    "selfhealing.api.django.middleware.actor_context.ActorContextMiddleware": "SELFHEALING_ACTOR_MIDDLEWARE_ENABLED",
    "selfhealing.api.django.cell.middleware.CellTaggingMiddleware": "SELFHEALING_CELL_TAGGING_ENABLED",
    "selfhealing.api.django.pool_circuit_breaker.PoolCircuitBreakerMiddleware": "SELFHEALING_POOL_CB_MIDDLEWARE_ENABLED",
    "selfhealing.api.django.audit_middleware.AuditMiddleware": "SELFHEALING_AUDIT_MIDDLEWARE_ENABLED",
}


# =========================================================================
# Public API
# =========================================================================


def configure_selfhealing(
    namespace: dict,
    *,
    early_group: list[str] | None = None,
    post_auth_group: list[str] | None = None,
    tail_group: list[str] | None = None,
    domains: list[str] | None = None,
    disable_auto_otel: bool = False,
) -> None:
    """Consumer settings.py에서 호출하여 selfhealing 설정을 명시적으로 래핑.

    Args:
        namespace: Consumer settings 모듈의 globals() — MIDDLEWARE, REST_FRAMEWORK 등을 수정
        early_group: Django core 이전에 삽입할 미들웨어 리스트 (기본: DEFAULT_EARLY_GROUP)
        post_auth_group: AuthenticationMiddleware 이후 삽입할 미들웨어 리스트 (기본: DEFAULT_POST_AUTH_GROUP)
        tail_group: 마지막에 삽입할 미들웨어 리스트 (기본: DEFAULT_TAIL_GROUP)
        domains: SELFHEALING_CORE_DOMAINS 설정 (비즈니스 도메인 목록)
        disable_auto_otel: True이면 OTEL 관련 설정 건너뜀 (Gunicorn 훅으로 지연 시)

    Note:
        이 함수는 반드시 settings.py의 **맨 마지막**에서 호출해야 한다.
        MIDDLEWARE, REST_FRAMEWORK 등이 모두 정의된 후에 호출되어야 올바르게 동작한다.
    """
    from selfhealing.settings.auto_config import get_auto_config_settings

    auto_settings = get_auto_config_settings()

    _validate_prerequisites(namespace)

    if auto_settings.middleware:
        _inject_middleware_groups(
            namespace,
            early=early_group if early_group is not None else list(DEFAULT_EARLY_GROUP),
            post_auth=post_auth_group
            if post_auth_group is not None
            else list(DEFAULT_POST_AUTH_GROUP),
            tail=tail_group if tail_group is not None else list(DEFAULT_TAIL_GROUP),
        )

    if auto_settings.exception_handler:
        _setup_exception_handler(namespace)

    if domains is not None:
        namespace["SELFHEALING_CORE_DOMAINS"] = domains

    otel_enabled = auto_settings.otel and not disable_auto_otel
    if otel_enabled and not _is_gunicorn_master():
        _initialize_otel(namespace)

    logger.debug("self_healing.configure_selfhealing_applied")


# =========================================================================
# Middleware Injection
# =========================================================================


def _inject_middleware_groups(
    namespace: dict,
    early: list[str],
    post_auth: list[str],
    tail: list[str],
) -> None:
    """MIDDLEWARE 리스트에 selfhealing 미들웨어 그룹을 삽입."""
    middleware = list(namespace.get("MIDDLEWARE", []))

    early = _filter_by_toggles(early, namespace)
    post_auth = _filter_by_toggles(post_auth, namespace)
    tail = _filter_by_toggles(tail, namespace)

    # 이미 존재하는 항목은 건너뜀 (Consumer가 수동으로 넣은 경우)
    early = [m for m in early if m not in middleware]
    post_auth = [m for m in post_auth if m not in middleware]
    tail = [m for m in tail if m not in middleware]

    # early → PrometheusBeforeMiddleware 직후 (없으면 맨 앞)
    early_idx = _find_insert_point(
        middleware, "PrometheusBeforeMiddleware", after=True, fallback=0
    )
    for i, m in enumerate(early):
        middleware.insert(early_idx + i, m)

    # post_auth → AuthenticationMiddleware 직후, XFrameOptionsMiddleware 이후까지 건너뜀
    auth_idx = _find_insert_point(
        middleware, "AuthenticationMiddleware", after=True, fallback=len(middleware)
    )
    xframe_idx = _find_insert_point(
        middleware, "XFrameOptionsMiddleware", after=True, fallback=auth_idx
    )
    insert_idx = max(auth_idx, xframe_idx)
    for i, m in enumerate(post_auth):
        middleware.insert(insert_idx + i, m)

    # tail → PrometheusAfterMiddleware 직전 (없으면 맨 뒤)
    tail_idx = _find_insert_point(
        middleware, "PrometheusAfterMiddleware", after=False, fallback=len(middleware)
    )
    for i, m in enumerate(tail):
        middleware.insert(tail_idx + i, m)

    namespace["MIDDLEWARE"] = middleware


def _filter_by_toggles(group: list[str], namespace: dict) -> list[str]:
    """토글 설정이 False인 미들웨어를 그룹에서 제거."""
    result = []
    for m in group:
        toggle = MIDDLEWARE_TOGGLES.get(m)
        if toggle and not namespace.get(toggle, True):
            continue
        result.append(m)
    return result


def _find_insert_point(
    middleware: list[str], target_substr: str, *, after: bool, fallback: int
) -> int:
    """미들웨어 리스트에서 target을 찾아 삽입 위치 반환."""
    for i, m in enumerate(middleware):
        if target_substr in m:
            return (i + 1) if after else i
    return fallback


# =========================================================================
# Exception Handler
# =========================================================================


def _setup_exception_handler(namespace: dict) -> None:
    """DRF EXCEPTION_HANDLER를 자동 설정."""
    rest_settings = namespace.get("REST_FRAMEWORK", {})
    if "EXCEPTION_HANDLER" not in rest_settings:
        rest_settings["EXCEPTION_HANDLER"] = (
            "selfhealing.api.django.exceptions.handler.selfhealing_exception_handler"
        )
    namespace["REST_FRAMEWORK"] = rest_settings


# =========================================================================
# Validation
# =========================================================================


def _validate_prerequisites(namespace: dict) -> None:
    """래퍼 호출 전에 필수 설정이 존재하는지 검증."""
    if "MIDDLEWARE" not in namespace:
        raise ImproperlyConfigured(
            "configure_selfhealing()은 MIDDLEWARE가 정의된 이후에 호출해야 합니다. "
            "settings.py의 맨 마지막에 배치하세요."
        )
    if "INSTALLED_APPS" not in namespace:
        raise ImproperlyConfigured(
            "configure_selfhealing()은 INSTALLED_APPS가 정의된 이후에 호출해야 합니다."
        )


# =========================================================================
# OTEL Initialization (dev-server only)
# =========================================================================


def _is_gunicorn_master() -> bool:
    """Gunicorn 마스터 프로세스인지 판별."""
    is_gunicorn_worker = os.environ.get("GUNICORN_WORKER") == "1"
    return (
        "gunicorn" in os.environ.get("SERVER_SOFTWARE", "") and not is_gunicorn_worker
    )


def _initialize_otel(namespace: dict) -> None:
    """개발 서버 환경에서 OTEL 초기화."""
    try:
        from selfhealing.observability import initialize_opentelemetry

        result = initialize_opentelemetry()
        namespace["_otel_initialized"] = result
    except ImportError:
        namespace["_otel_initialized"] = False
    except Exception:
        logger.warning("self_healing.otel_initialization_failed", exc_info=True)
        namespace["_otel_initialized"] = False
