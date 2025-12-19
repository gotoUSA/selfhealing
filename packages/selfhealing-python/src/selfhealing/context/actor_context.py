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

    @classmethod
    def is_anonymous_or_system(cls) -> bool:
        """
        Check if current actor is anonymous or system (potentially untracked).

        Returns True if:
        - No actor is set (will default to SYSTEM_ACTOR)
        - Actor is anonymous
        - Actor is system

        Use this to detect potentially untracked operations.
        """
        actor = _current_actor.get()
        if actor is None:
            return True
        return actor.actor_type in ("system", "anonymous")


class ActorTrackingWarning(UserWarning):
    """Warning for untracked sensitive operations."""

    pass


def warn_if_untracked(operation: str) -> None:
    """
    Emit warning if current operation is not properly tracked.

    Use this in sensitive operations to alert about missing actor context.

    Usage:
        def force_open_circuit_breaker(service_name: str):
            warn_if_untracked("force_open_circuit_breaker")
            # ... do the operation
    """
    import warnings

    if ActorContext.is_anonymous_or_system():
        warnings.warn(
            f"Sensitive operation '{operation}' performed without actor tracking. "
            f"Current actor: {ActorContext.get_current().actor_id}. "
            f"Consider using ActorContext.set_actor() for audit trail.",
            ActorTrackingWarning,
            stacklevel=2,
        )
        logger.warning(
            f"[ActorContext] UNTRACKED_OPERATION operation={operation} "
            f"actor={ActorContext.get_current().actor_id}"
        )


def require_actor_for_action(action_name: str) -> Actor:
    """
    Require an actor for a specific action, with detailed error message.

    Use for operations that MUST be tracked (config changes, manual overrides, etc.)

    Usage:
        def change_critical_config(key, value):
            actor = require_actor_for_action("change_critical_config")
            # actor is guaranteed to be a real user, not system/anonymous
    """
    actor = ActorContext.get_current()

    if actor.actor_type in ("system", "anonymous"):
        raise RuntimeError(
            f"Action '{action_name}' requires a tracked actor. "
            f"Current actor '{actor.actor_id}' ({actor.actor_type}) is not sufficient. "
            f"This action must be performed by a logged-in user. "
            f"If this is a background job, use ActorContext.set_actor() to specify who initiated it."
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


# =============================================================================
# Celery Task 지원
# =============================================================================

def get_actor_for_celery() -> dict[str, Any]:
    """
    Get current actor info for passing to Celery task.

    Usage (in view/api):
        from selfhealing.context import get_actor_for_celery

        # Pass actor info to Celery task
        my_task.delay(
            order_id=123,
            actor_info=get_actor_for_celery(),
        )

    Usage (in task):
        @app.task
        def my_task(order_id: int, actor_info: dict):
            with restore_actor_from_celery(actor_info):
                do_work()  # ActorContext is now set
    """
    actor = ActorContext.get_current()
    return {
        "actor_id": actor.actor_id,
        "actor_type": actor.actor_type,
        "source": f"celery_from_{actor.source}",
        "ip_address": actor.ip_address,
        "session_id": actor.session_id,
        "original_set_at": actor.set_at.isoformat(),
    }


@contextmanager
def restore_actor_from_celery(actor_info: dict[str, Any]) -> Generator[Actor, None, None]:
    """
    Restore actor context in Celery task from passed info.

    Usage:
        @app.task
        def my_task(order_id: int, actor_info: dict):
            with restore_actor_from_celery(actor_info):
                # ActorContext is now set with original user info
                entry = AuditEntry(action=AuditAction.DLQ_REPLAY_START)
                # entry.actor_id will be the original user, not "system"
    """
    if not actor_info:
        # No actor info passed, log warning
        logger.warning(
            "[ActorContext] Celery task started without actor_info. "
            "Operations will be attributed to 'system'."
        )
        yield SYSTEM_ACTOR
        return

    with ActorContext.set_actor(
        actor_id=actor_info.get("actor_id", "unknown"),
        actor_type=actor_info.get("actor_type", "celery"),
        source=actor_info.get("source", "celery"),
        ip_address=actor_info.get("ip_address"),
        session_id=actor_info.get("session_id"),
        original_request_time=actor_info.get("original_set_at"),
    ) as actor:
        yield actor


# =============================================================================
# Management Command 지원
# =============================================================================

@contextmanager
def set_management_command_actor(
    command_name: str,
    run_by: Optional[str] = None,
) -> Generator[Actor, None, None]:
    """
    Set actor context for Django management command.

    Usage:
        class Command(BaseCommand):
            def handle(self, *args, **options):
                with set_management_command_actor("cleanup_dlq", run_by="cron"):
                    do_cleanup()
    """
    import getpass
    import socket

    actor_id = run_by or f"{getpass.getuser()}@{socket.gethostname()}"

    with ActorContext.set_actor(
        actor_id=actor_id,
        actor_type="management_command",
        source=f"manage.py:{command_name}",
    ) as actor:
        logger.info(
            f"[ActorContext] Management command '{command_name}' started by {actor_id}"
        )
        yield actor

