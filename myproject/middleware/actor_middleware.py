"""
Actor Context Middleware

모든 HTTP 요청에서 "누가" 이 작업을 수행하는지 자동 추적.

이 미들웨어를 사용하면:
1. 모든 AuditEntry에 자동으로 actor_id, actor_type이 채워짐
2. Admin 페이지에서 설정 변경 시 누가 변경했는지 기록
3. API 호출 시 어느 사용자가 호출했는지 추적
4. IP 주소, 세션 ID 등 보안 감사 정보도 자동 수집

Usage in settings.py:
    MIDDLEWARE = [
        ...
        'myproject.middleware.actor_middleware.ActorContextMiddleware',
        ...
    ]

비활성화:
    SELFHEALING_ACTOR_MIDDLEWARE_ENABLED = False (settings.py)
    또는
    SELFHEALING_ACTOR_MIDDLEWARE_ENABLED=false (환경변수)

설정 후 어디서든:
    from selfhealing.context import ActorContext

    actor = ActorContext.get_current()
    print(f"Current user: {actor.actor_id}")  # admin@example.com
    print(f"IP: {actor.ip_address}")  # 192.168.1.1
"""

import logging
import os
from typing import Callable

from django.http import HttpRequest, HttpResponse

logger = logging.getLogger(__name__)


class ActorContextMiddleware:
    """
    Django Middleware for automatic actor context tracking.

    Extracts user information from request and makes it available
    throughout the request lifecycle for audit logging.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response
        self._enabled = self._check_enabled()
        
        status = "enabled" if self._enabled else "DISABLED"
        logger.info(f"[ActorContextMiddleware] Initialized - {status}")

    def _check_enabled(self) -> bool:
        """미들웨어 활성화 여부 확인"""
        try:
            from django.conf import settings
            return getattr(settings, 'SELFHEALING_ACTOR_MIDDLEWARE_ENABLED', True)
        except Exception:
            # settings 접근 불가 시 환경변수 확인
            return os.getenv("SELFHEALING_ACTOR_MIDDLEWARE_ENABLED", "true").lower() in ("true", "1", "yes")

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # 미들웨어 비활성화 시 바이패스
        if not self._enabled:
            return self.get_response(request)
        
        # Import here to avoid circular imports
        try:
            from selfhealing.context.actor_context import ActorContext
        except ImportError:
            logger.warning(
                "[ActorContextMiddleware] selfhealing package not installed. "
                "Actor context tracking disabled."
            )
            return self.get_response(request)

        # Use context manager to set actor for this request
        with ActorContext.set_actor_from_django_request(request):
            response = self.get_response(request)

        return response


class ActorContextMiddlewareSimple:
    """
    Simplified version without selfhealing dependency.

    Uses thread-local storage instead of ActorContext.
    Good for projects that don't use the selfhealing package.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        import threading

        # Thread-local storage
        if not hasattr(self, "_local"):
            self._local = threading.local()

        # Extract actor info
        if hasattr(request, "user") and request.user.is_authenticated:
            self._local.actor_id = getattr(request.user, "email", None) or str(request.user.pk)
            self._local.actor_type = "user"
        else:
            self._local.actor_id = "anonymous"
            self._local.actor_type = "anonymous"

        self._local.ip_address = self._get_client_ip(request)
        self._local.request_path = request.path

        try:
            response = self.get_response(request)
        finally:
            # Clean up
            self._local.actor_id = None
            self._local.actor_type = None
            self._local.ip_address = None
            self._local.request_path = None

        return response

    def _get_client_ip(self, request: HttpRequest) -> str | None:
        """Extract client IP from request."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")
