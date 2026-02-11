"""
Security Hooks - 보안 이벤트 콜백 레지스트리.

selfhealing 패키지가 호스트 앱의 인증 시스템에 의존하지 않으면서도
보안 위반 시 호스트 앱의 토큰 무효화 등을 트리거할 수 있도록 합니다.

Usage (호스트 앱의 AppConfig.ready()):
    from selfhealing.services.security.hooks import register_session_invalidation_hook

    def blacklist_user_jwt(user_id: int) -> str:
        from rest_framework_simplejwt.token_blacklist.models import (
            BlacklistedToken, OutstandingToken,
        )
        tokens = OutstandingToken.objects.filter(user_id=user_id)
        count = 0
        for token in tokens:
            _, created = BlacklistedToken.objects.get_or_create(token=token)
            if created:
                count += 1
        return f"jwt_blacklisted({count})"

    register_session_invalidation_hook(blacklist_user_jwt)
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

# 콜백 타입: (user_id: int) -> str (결과 설명)
# ──────────────────────────────────────────────────────────────────
# user_id 타입이 int인 근거:
#   - shopping.User(AbstractUser)에 커스텀 PK 없음 → default PK 사용
#   - DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField" (base.py L267)
#   - BigAutoField는 Python int 타입
# ──────────────────────────────────────────────────────────────────
SessionInvalidationHook = Callable[[int], str]

_hooks: list[SessionInvalidationHook] = []


def register_session_invalidation_hook(hook: SessionInvalidationHook) -> None:
    """
    세션 무효화 시 실행할 콜백을 등록.

    등록된 콜백은 _invalidate_user_sessions(user_id)가 호출될 때
    순서대로 실행됩니다.

    Args:
        hook: (user_id: int) -> str 형태의 콜백 함수
    """
    _hooks.append(hook)
    logger.info(
        f"[SecurityHooks] Session invalidation hook registered: "
        f"{getattr(hook, '__module__', '?')}.{getattr(hook, '__qualname__', repr(hook))}"
    )


def get_session_invalidation_hooks() -> list[SessionInvalidationHook]:
    """등록된 세션 무효화 콜백 목록 반환."""
    return list(_hooks)


def clear_session_invalidation_hooks() -> None:
    """모든 콜백 제거 (테스트용)."""
    _hooks.clear()
