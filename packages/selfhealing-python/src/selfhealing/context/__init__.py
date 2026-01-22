"""
Context module for tracking who/what is performing actions.

Provides thread-safe, async-safe context tracking for:
- Actor (who is performing the action)
- Request context (web request info)
- Audit context (automatic audit logging)
- Causation context (cascade event causality tracking)

Failure Protection:
- warn_if_untracked(): 추적 안 된 민감 작업 경고
- require_actor_for_action(): 추적 필수 작업에서 강제
- get_actor_for_celery() / restore_actor_from_celery(): Celery task 지원
- set_management_command_actor(): Management command 지원
- get_causation_for_celery() / restore_causation_from_celery(): Cascade 인과관계 전파
"""

from selfhealing.context.actor_context import (
    Actor,
    ActorContext,
    ActorTrackingWarning,
    SYSTEM_ACTOR,
    ANONYMOUS_ACTOR,
    get_audit_actor_info,
    warn_if_untracked,
    require_actor_for_action,
    get_actor_for_celery,
    restore_actor_from_celery,
    set_management_command_actor,
)

from selfhealing.context.causation_context import (
    CausationInfo,
    CausationContext,
    get_causation_for_celery,
    restore_causation_from_celery,
    get_causation_for_kafka,
    restore_causation_from_kafka,
    CELERY_HEADER_CASCADE_ID,
    CELERY_HEADER_PARENT_EVENT,
    CELERY_HEADER_CHAIN_DEPTH,
    CELERY_HEADER_NAMESPACE,
)

__all__ = [
    # Actor context
    "Actor",
    "ActorContext",
    "ActorTrackingWarning",
    "SYSTEM_ACTOR",
    "ANONYMOUS_ACTOR",
    "get_audit_actor_info",
    "warn_if_untracked",
    "require_actor_for_action",
    "get_actor_for_celery",
    "restore_actor_from_celery",
    "set_management_command_actor",
    # Causation context
    "CausationInfo",
    "CausationContext",
    "get_causation_for_celery",
    "restore_causation_from_celery",
    "get_causation_for_kafka",
    "restore_causation_from_kafka",
    "CELERY_HEADER_CASCADE_ID",
    "CELERY_HEADER_PARENT_EVENT",
    "CELERY_HEADER_CHAIN_DEPTH",
    "CELERY_HEADER_NAMESPACE",
]

