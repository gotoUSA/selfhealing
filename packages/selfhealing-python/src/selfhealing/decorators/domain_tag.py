"""
Domain Tag Decorator and Context Manager.

에러 발생 시 도메인을 자동으로 태깅하여 Crisis Multiplier의
도메인 인지 기능을 지원합니다.

Features:
- @domain_tag 데코레이터: 함수 실행 중 도메인 컨텍스트 설정
- DomainContext: with 문 기반 도메인 컨텍스트
- contextvars 기반 스레드/async 안전

Usage:
    from selfhealing.decorators.domain_tag import (
        domain_tag,
        DomainContext,
        get_current_domain,
    )

    # 데코레이터 방식
    @domain_tag("payment")
    def process_payment():
        # 이 함수 내에서 발생하는 에러는 "payment" 도메인으로 태깅됨
        ...

    # 컨텍스트 매니저 방식
    with DomainContext("order"):
        # 이 블록 내에서 발생하는 에러는 "order" 도메인으로 태깅됨
        ...

Reference:
    docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §0.1 (6, 15번)
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from contextvars import ContextVar, Token
from typing import Any, TypeVar

logger = logging.getLogger(__name__)


# =============================================================================
# Context Variable
# =============================================================================

_current_domain: ContextVar[str | None] = ContextVar(
    "selfhealing_current_domain",
    default=None,
)
"""
현재 실행 컨텍스트의 도메인.

contextvars를 사용하여 스레드/async 안전을 보장.
"""


# =============================================================================
# Domain Context Manager
# =============================================================================


class DomainContext:
    """
    도메인 컨텍스트 매니저.

    with 문을 사용하여 특정 코드 블록의 도메인을 설정합니다.
    블록 종료 시 이전 도메인 컨텍스트로 자동 복원됩니다.

    Usage:
        with DomainContext("payment"):
            # 이 블록 내에서는 도메인이 "payment"
            process_payment()
        # 블록 종료 후 이전 도메인으로 복원

    Attributes:
        domain: 설정할 도메인 이름
    """

    def __init__(self, domain: str):
        """
        DomainContext 초기화.

        Args:
            domain: 설정할 도메인 이름
        """
        self.domain = domain.lower()
        self._token: Token | None = None
        self._previous_domain: str | None = None

    def __enter__(self) -> DomainContext:
        """컨텍스트 진입: 도메인 설정."""
        self._previous_domain = _current_domain.get()
        self._token = _current_domain.set(self.domain)

        logger.debug(f"[DomainContext] Entered: domain={self.domain}, " f"previous={self._previous_domain}")

        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """컨텍스트 종료: 이전 도메인으로 복원."""
        if self._token is not None:
            _current_domain.reset(self._token)
            self._token = None

        logger.debug(f"[DomainContext] Exited: domain={self.domain}, " f"restored={_current_domain.get()}")

        # 예외 전파 (False 반환)
        return False


# =============================================================================
# Domain Tag Decorator
# =============================================================================

F = TypeVar("F", bound=Callable[..., Any])


def domain_tag(domain: str) -> Callable[[F], F]:
    """
    도메인 태깅 데코레이터.

    함수 실행 중 도메인 컨텍스트를 설정합니다.
    함수 종료 시 이전 도메인 컨텍스트로 자동 복원됩니다.

    Args:
        domain: 설정할 도메인 이름

    Returns:
        데코레이터 함수

    Usage:
        @domain_tag("payment")
        def process_payment():
            # 이 함수 내에서 발생하는 에러는 "payment" 도메인으로 태깅됨
            ...

        @domain_tag("order")
        async def create_order():
            # async 함수도 지원
            ...

    Reference:
        docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §0.1 (6번)
    """
    normalized_domain = domain.lower()

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            with DomainContext(normalized_domain):
                return func(*args, **kwargs)

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            with DomainContext(normalized_domain):
                return await func(*args, **kwargs)

        # async 함수 판별
        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        else:
            return sync_wrapper  # type: ignore

    return decorator


# =============================================================================
# Utility Functions
# =============================================================================


def get_current_domain() -> str | None:
    """
    현재 도메인 컨텍스트 조회.

    Returns:
        현재 도메인 이름 (없으면 None)

    Usage:
        @domain_tag("payment")
        def process_payment():
            domain = get_current_domain()
            print(f"Current domain: {domain}")  # "payment"
    """
    return _current_domain.get()


def clear_domain_context() -> None:
    """
    도메인 컨텍스트 초기화.

    주로 테스트 정리(cleanup) 용도.
    일반 코드에서는 DomainContext나 @domain_tag가 자동으로 정리합니다.
    """
    _current_domain.set(None)


def set_domain_context(domain: str | None) -> Token:
    """
    도메인 컨텍스트 설정 (저수준 API).

    주로 프레임워크 통합 용도. 일반 코드에서는
    DomainContext나 @domain_tag 사용을 권장합니다.

    Args:
        domain: 설정할 도메인 (None이면 해제)

    Returns:
        컨텍스트 복원용 토큰

    Usage:
        token = set_domain_context("payment")
        try:
            # 도메인이 "payment"인 컨텍스트
            ...
        finally:
            _current_domain.reset(token)
    """
    return _current_domain.set(domain.lower() if domain else None)


# =============================================================================
# Django/Flask Middleware Integration Helper
# =============================================================================


class DomainMiddlewareMixin:
    """
    웹 프레임워크 미들웨어용 믹스인.

    요청 URL이나 헤더에서 도메인을 추출하여 컨텍스트를 설정합니다.

    Usage (Django):
        class DomainMiddleware(DomainMiddlewareMixin):
            def __init__(self, get_response):
                self.get_response = get_response

            def __call__(self, request):
                domain = self.extract_domain_from_request(request)
                with DomainContext(domain or "unknown"):
                    return self.get_response(request)
    """

    DOMAIN_HEADER = "X-Domain"
    """도메인 식별 헤더."""

    URL_DOMAIN_MAPPING: dict[str, str] = {}
    """
    URL 패턴 → 도메인 매핑 (프로젝트별 설정).

    서브클래스에서 오버라이드하거나, Django settings SELF_HEALING_DOMAIN_MAPPING 을 사용하세요.
    예: {"/api/payments/": "payment", "/api/orders/": "order"}
    """

    def extract_domain_from_request(self, request) -> str | None:
        """
        요청에서 도메인 추출.

        우선순위:
        1. X-Domain 헤더
        2. URL 패턴 매칭
        3. None
        """
        # 헤더에서 추출
        if hasattr(request, "headers"):
            domain = request.headers.get(self.DOMAIN_HEADER)
            if domain:
                return domain.lower()
        elif hasattr(request, "META"):
            # Django 스타일
            domain = request.META.get(f'HTTP_{self.DOMAIN_HEADER.replace("-", "_").upper()}')
            if domain:
                return domain.lower()

        # URL 패턴에서 추출
        path = getattr(request, "path", "")
        for pattern, domain in self.URL_DOMAIN_MAPPING.items():
            if path.startswith(pattern):
                return domain

        return None
