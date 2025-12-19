"""
Actor Context - 누가 이 작업을 수행했는지 자동 추적

문제:
- AuditEntry에 actor_id를 수동으로 넣어야 함
- 잊어버리면 누가 설정을 변경했는지 추적 불가
- 캐시 문제로 설정이 안 바뀐 채 남아있으면 대형 사고

해결:
- ActorContext로 현재 사용자를 thread-local하게 추적
- Django middleware에서 자동으로 설정
- Admin 페이지, API 호출 모두 커버

Usage:
    # Django middleware에서 자동 설정
    class ActorMiddleware:
        def __call__(self, request):
            with ActorContext.set_actor(
                actor_id=request.user.email,
                actor_type="user",
                source="web"
            ):
                return self.get_response(request)

    # 어디서든 현재 actor 조회
    actor = ActorContext.get_current()
    print(f"Current user: {actor.actor_id}")

    # Celery task에서 명시적 설정
    @task
    def my_task(actor_id: str):
        with ActorContext.set_actor(actor_id=actor_id, actor_type="scheduler"):
            do_work()
"""

from __future__ import annotations

import contextvars
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Generator, Optional

logger = logging.getLogger(__name__)

# Context variable for thread-safe actor tracking
_current_actor: contextvars.ContextVar[Optional["Actor"]] = contextvars.ContextVar(
    "current_actor", default=None
)


@dataclass
class Actor:
    """
    현재 작업을 수행하는 주체 정보.

    Attributes:
        actor_id: 사용자 식별자 (email, username, user_id 등)
        actor_type: 주체 유형 (user, system, scheduler, api_client 등)
        source: 요청 출처 (web, api, celery, management_command 등)
        ip_address: 요청 IP (보안 감사용)
        session_id: 세션 ID (같은 세션 내 작업 연결)
        set_at: Actor가 설정된 시점
        metadata: 추가 정보 (user-agent, request_id 등)
    """

    actor_id: str
    actor_type: str = "user"
    source: str = "unknown"
    ip_address: Optional[str] = None
    session_id: Optional[str] = None
    set_at: datetime = field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for audit logging."""
        return {
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "source": self.source,
            "ip_address": self.ip_address,
            "session_id": self.session_id,
            "set_at": self.set_at.isoformat(),
            "metadata": self.metadata,
        }


# Sentinel for anonymous/system actor
SYSTEM_ACTOR = Actor(
    actor_id="system",
    actor_type="system",
    source="internal",
)

ANONYMOUS_ACTOR = Actor(
    actor_id="anonymous",
    actor_type="anonymous",
    source="unknown",
)


class ActorContext:
    """
    Thread-safe context for tracking who is performing an action.

    Uses Python's contextvars for async/thread safety.
    Works with Django, Celery, asyncio, and plain threads.
    """

    @classmethod
    @contextmanager
    def set_actor(
        cls,
        actor_id: str,
        actor_type: str = "user",
        source: str = "unknown",
        ip_address: Optional[str] = None,
        session_id: Optional[str] = None,
        **metadata: Any,
    ) -> Generator[Actor, None, None]:
        """
        Set the current actor for this context.

        Usage:
            with ActorContext.set_actor(actor_id="admin@example.com"):
                # All audit logs in this block will have this actor
                do_something()
        """
        actor = Actor(
            actor_id=actor_id,
            actor_type=actor_type,
            source=source,
            ip_address=ip_address,
            session_id=session_id,
            metadata=metadata,
        )
        token = _current_actor.set(actor)
        try:
            logger.debug(f"[ActorContext] Set actor: {actor_id} ({actor_type}) from {source}")
            yield actor
        finally:
            _current_actor.reset(token)
            logger.debug(f"[ActorContext] Cleared actor: {actor_id}")

    @classmethod
    def set_actor_from_django_request(cls, request: Any) -> Generator[Actor, None, None]:
        """
        Set actor from Django request object.

        Extracts user info, IP address, session ID automatically.
        """
        # Extract user info
        if hasattr(request, "user") and request.user.is_authenticated:
            actor_id = getattr(request.user, "email", None) or str(request.user.pk)
            actor_type = "user"
        else:
            actor_id = "anonymous"
            actor_type = "anonymous"

        # Extract IP address
        ip_address = cls._get_client_ip(request)

        # Extract session ID
        session_id = None
        if hasattr(request, "session") and request.session.session_key:
            session_id = request.session.session_key

        # Determine source
        source = "api" if "/api/" in request.path else "web"

        return cls.set_actor(
            actor_id=actor_id,
            actor_type=actor_type,
            source=source,
            ip_address=ip_address,
            session_id=session_id,
            path=request.path,
            method=request.method,
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )

    @classmethod
    def _get_client_ip(cls, request: Any) -> Optional[str]:
        """Extract client IP from Django request."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")

    @classmethod
    def get_current(cls) -> Actor:
        """
        Get the current actor.

        Returns SYSTEM_ACTOR if no actor is set (background jobs, etc.)
        """
        actor = _current_actor.get()
        if actor is None:
            return SYSTEM_ACTOR
        return actor

    @classmethod
    def get_current_or_none(cls) -> Optional[Actor]:
        """Get the current actor, or None if not set."""
        return _current_actor.get()

    @classmethod
    def is_set(cls) -> bool:
        """Check if an actor is currently set."""
        return _current_actor.get() is not None

    @classmethod
    def require_actor(cls) -> Actor:
        """
        Get the current actor, raising if not set.

        Use this when an action MUST have an actor (security-critical operations).
        """
        actor = _current_actor.get()
        if actor is None:
            raise RuntimeError(
                "ActorContext not set. Security-critical operations require an actor. "
                "Use ActorContext.set_actor() or ensure middleware is configured."
            )
        return actor


def get_audit_actor_info() -> dict[str, Any]:
    """
    Get actor info formatted for AuditEntry.

    Returns dict with actor_id, actor_type that can be unpacked into AuditEntry.

    Usage:
        entry = AuditEntry(
            action=AuditAction.CONFIG_CHANGE,
            **get_audit_actor_info(),  # Adds actor_id, actor_type
            ...
        )
    """
    actor = ActorContext.get_current()
    return {
        "actor_id": actor.actor_id,
        "actor_type": actor.actor_type,
    }
