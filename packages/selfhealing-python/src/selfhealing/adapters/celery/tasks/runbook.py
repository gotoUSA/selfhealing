"""
Runbook Celery Tasks — 어댑터 레이어.

EventBus 이벤트 기반 실행, 수동 실행, 승인 타이머 체크를 Celery 태스크로 래핑한다.

Reference:
    docs/self_healing/middleware_system/278_RUNBOOK_SERVICE.md §6
"""

from __future__ import annotations

from typing import Any

import structlog
from celery import shared_task

logger = structlog.get_logger(__name__)


@shared_task(
    bind=True,
    name="selfhealing.tasks.runbook.execute_runbook_for_event",
    max_retries=1,
    default_retry_delay=30,
    acks_late=True,
)
def execute_runbook_for_event(self, event_data: dict) -> dict:
    """이벤트 기반 Runbook 실행.

    EventBus → _on_event_received() → 이 태스크 (비동기).
    """
    try:
        from selfhealing.services.event_bus.bus import SelfHealingEvent
        from selfhealing.services.runbook.service import RunbookService

        event = SelfHealingEvent.from_dict(event_data)
        service = RunbookService()
        ctx = service.handle_event(event)

        if ctx is None:
            return {
                "status": "no_match",
                "event_type": event_data.get("event_type"),
            }

        return {
            "status": ctx.status.value,
            "execution_id": ctx.execution_id,
            "runbook_id": ctx.runbook_id,
        }

    except Exception as e:
        logger.exception("runbook_task.event_execution_failed", error=str(e))
        raise self.retry(exc=e)


@shared_task(
    bind=True,
    name="selfhealing.tasks.runbook.execute_runbook_manual",
    max_retries=0,
    acks_late=True,
)
def execute_runbook_manual(
    self,
    runbook_id: str,
    namespace: str = "global",
    trigger_event: dict | None = None,
) -> dict:
    """수동 Runbook 실행.

    API/CLI → 이 태스크 (비동기).
    """
    try:
        from selfhealing.services.runbook.service import RunbookService

        service = RunbookService()
        ctx = service.execute_runbook(runbook_id, namespace, trigger_event)

        return {
            "status": ctx.status.value,
            "execution_id": ctx.execution_id,
        }

    except Exception as e:
        # 락 충돌 시 소유자 정보 포함
        from selfhealing.services.runbook.exceptions import RunbookLockConflictError

        if isinstance(e, RunbookLockConflictError):
            try:
                from selfhealing.services.coordination.distributed_recovery_lock import (
                    get_distributed_recovery_lock,
                )

                lock = get_distributed_recovery_lock()
                owner = lock.get_lock_owner(namespace)
                return {
                    "status": "lock_conflict",
                    "current_owner": owner,
                    "namespace": namespace,
                    "message": (
                        f"Namespace '{namespace}' has an active execution. "
                        f"Use cancel_runbook_execution() to cancel or wait."
                    ),
                }
            except Exception:
                pass

        logger.exception("runbook_task.manual_execution_failed", error=str(e))
        return {"status": "error", "error": str(e)}


@shared_task(
    name="selfhealing.tasks.runbook.check_approval_timers",
)
def check_approval_timers() -> dict[str, Any]:
    """MEDIUM 리스크 승인 타이머 만료 체크.

    Celery Beat에서 주기적으로 호출 (매 30초).
    """
    from selfhealing.services.runbook.approval_gate import RunbookApprovalGate

    gate = RunbookApprovalGate()

    results: dict[str, Any] = {
        "timer_approved": 0,
        "timed_out": 0,
        "reminders_sent": 0,
    }

    try:
        timer_results = gate.check_timer_approval("")
        if timer_results is not None:
            results["timer_approved"] += 1
    except Exception as e:
        logger.warning("runbook_task.timer_check_failed", error=str(e))

    try:
        timed_out = gate.check_approval_timeouts()
        results["timed_out"] = len(timed_out)
    except Exception as e:
        logger.warning("runbook_task.timeout_check_failed", error=str(e))

    try:
        reminded = gate.check_and_send_reminders()
        results["reminders_sent"] = len(reminded)
    except Exception as e:
        logger.warning("runbook_task.reminder_check_failed", error=str(e))

    return results
