"""
Context module for tracking who/what is performing actions.

Provides thread-safe, async-safe context tracking for:
- Actor (who is performing the action)
- Request context (web request info)
- Audit context (automatic audit logging)
"""

from selfhealing.context.actor_context import (
    Actor,
    ActorContext,
    SYSTEM_ACTOR,
    ANONYMOUS_ACTOR,
    get_audit_actor_info,
)

__all__ = [
    "Actor",
    "ActorContext",
    "SYSTEM_ACTOR",
    "ANONYMOUS_ACTOR",
    "get_audit_actor_info",
]
