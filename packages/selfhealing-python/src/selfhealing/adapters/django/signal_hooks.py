"""
Django Session Signal Hooks for Self-Healing System.

Django user_logged_in / user_logged_out 시그널을 통해
UserSessionRegistry에 session_key 역방향 매핑을 자동으로 관리한다.

연결 방식:
    SelfHealingConfig.ready()에서 connect_session_signals()를 호출하여
    Django 시그널에 자동으로 연결된다. 호스트 앱에서 별도 코드 불필요.

Architecture:
    adapters/celery/signal_hooks.py (Celery 시그널)과 동일한 패턴.
    selfhealing 패키지가 Django 인증 시그널을 어댑터 레이어에서 처리하므로
    호스트 앱(shopping 등)에 시그널 핸들러를 작성할 필요가 없다.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from django.http import HttpRequest

from selfhealing.services.security.session_registry import get_user_session_registry

logger = logging.getLogger(__name__)

_connected = False


def on_user_login_register_session(sender: Any, request: HttpRequest, user: Any, **kwargs: Any) -> None:
    """
    로그인 시 UserSessionRegistry에 session_key 매핑 등록.

    Redis 세션 백엔드에서는 user_id → session_key 역방향 조회가 불가능하므로,
    로그인 시점에 매핑을 등록하여 세션 무효화 시 역방향 조회를 지원한다.
    """
    session_key = request.session.session_key
    if not session_key:
        request.session.save()
        session_key = request.session.session_key

    if session_key and user and user.pk:
        registry = get_user_session_registry()
        registry.register(user.pk, session_key)


def on_user_logout_unregister_session(sender: Any, request: HttpRequest, user: Any, **kwargs: Any) -> None:
    """
    로그아웃 시 UserSessionRegistry에서 session_key 매핑 제거.

    로그인 시 등록한 session_key 매핑을 제거하여 불필요한 무효화 시도를 방지한다.
    """
    session_key = getattr(request.session, "session_key", None)
    if session_key and user and user.pk:
        registry = get_user_session_registry()
        registry.unregister(user.pk, session_key)


def connect_session_signals() -> None:
    """
    Django 세션 시그널 핸들러 연결.

    SelfHealingConfig.ready()에서 호출된다.
    dispatch_uid로 중복 연결을 방지한다.
    """
    global _connected
    if _connected:
        return

    from django.contrib.auth.signals import user_logged_in, user_logged_out

    user_logged_in.connect(
        on_user_login_register_session,
        dispatch_uid="selfhealing_session_register",
    )
    user_logged_out.connect(
        on_user_logout_unregister_session,
        dispatch_uid="selfhealing_session_unregister",
    )
    _connected = True
    logger.debug("[SelfHealing] Session signal handlers connected")


def disconnect_session_signals() -> None:
    """
    Django 세션 시그널 핸들러 해제 (테스트용).
    """
    global _connected
    from django.contrib.auth.signals import user_logged_in, user_logged_out

    user_logged_in.disconnect(dispatch_uid="selfhealing_session_register")
    user_logged_out.disconnect(dispatch_uid="selfhealing_session_unregister")
    _connected = False


def is_session_signals_connected() -> bool:
    """세션 시그널 연결 상태 확인."""
    return _connected
