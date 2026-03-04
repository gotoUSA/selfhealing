"""
Runbook Celery Tasks — 서비스 레이어 태스크.

승인 후 재개, 고아 실행 스캔 등 RunbookService의 비동기 처리를 담당한다.

Reference:
    docs/self_healing/middleware_system/278_RUNBOOK_SERVICE.md §14-15
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from celery import shared_task

from selfhealing.services.runbook.execution_models import (
    ApprovalDecisionType,
    RunbookExecutionContext,
    RunbookExecutionStatus,
)

logger = structlog.get_logger(__name__)

ORPHAN_SCAN_STALE_THRESHOLD_SECONDS = 600
"""고아 런북 판별 임계값 (초). 10분."""

APPROVED_STALE_THRESHOLD_SECONDS = 120
"""승인 완료 후 재개되지 않은 런북 판별 임계값 (초). 2분."""


@shared_task(
    bind=True,
    name="selfhealing.runbook.resume_pipeline",
    max_retries=3,
    default_retry_delay=60,
    queue="selfhealing_runbook",
)
def resume_runbook_task(self, execution_id: str) -> dict:
    """승인 후 파이프라인 재개."""
    try:
        from selfhealing.services.runbook.service import RunbookService

        service = RunbookService()
        ctx = service.resume_pipeline(execution_id)
        return {"status": ctx.status.value, "execution_id": execution_id}
    except Exception as e:
        logger.exception(
            "runbook_task.resume_failed",
            execution_id=execution_id,
            error=str(e),
        )
        raise self.retry(exc=e)


@shared_task(
    name="selfhealing.runbook.scan_orphan_executions",
    queue="selfhealing_recovery",
)
def scan_orphan_runbook_executions() -> dict[str, Any]:
    """고아 런북을 주기적으로 스캔하여 재개 또는 실패 처리.

    Celery Beat로 2분 간격 실행.

    스캔 대상:
    1. status=EXECUTING + started_at > ORPHAN_SCAN_STALE_THRESHOLD(10분) → 고아
    2. status=WAITING_APPROVAL + approval이 APPROVED인데 재개되지 않음 → 유실 복구

    GC Pause 방어 (scan_orphan_sagas 패턴):
    - 재개 전 분산 락 획득 시도
    - 락 획득 성공 시에만 resume_runbook_task.delay() 디스패치
    """
    from selfhealing.core.state_backend import get_state_backend

    backend = get_state_backend()
    results: dict[str, Any] = {"scanned": 0, "resumed": 0, "skipped": 0}
    now_utc = datetime.now(timezone.utc)

    try:
        active_entries = backend.get_all("selfhealing:runbook:execution:*")
    except Exception as e:
        logger.warning("runbook_orphan_scan.backend_error", error=str(e))
        return results

    results["scanned"] = len(active_entries)

    for key, data in active_entries.items():
        try:
            ctx = RunbookExecutionContext.from_dict(data)
        except Exception:
            results["skipped"] += 1
            continue

        is_orphan = False

        if ctx.status == RunbookExecutionStatus.EXECUTING:
            if ctx.started_at:
                started = datetime.fromisoformat(ctx.started_at)
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                is_orphan = (
                    now_utc - started
                ).total_seconds() > ORPHAN_SCAN_STALE_THRESHOLD_SECONDS

        elif ctx.status == RunbookExecutionStatus.WAITING_APPROVAL:
            is_orphan = _is_approved_but_not_resumed(ctx, now_utc)

        if not is_orphan:
            results["skipped"] += 1
            continue

        # GC Pause 방어: 락 획득 시도
        try:
            from selfhealing.services.coordination.distributed_recovery_lock import (
                get_distributed_recovery_lock,
            )

            lock = get_distributed_recovery_lock()
            acquired = lock.acquire(ctx.namespace, ctx.execution_id)
            if acquired:
                lock.release(ctx.namespace, ctx.execution_id)
                resume_runbook_task.delay(ctx.execution_id)
                results["resumed"] += 1
            else:
                results["skipped"] += 1
        except Exception as e:
            logger.warning(
                "runbook_orphan_scan.lock_error",
                execution_id=ctx.execution_id,
                error=str(e),
            )
            results["skipped"] += 1

    logger.info(
        "runbook_orphan_scan.completed",
        scanned=results["scanned"],
        resumed=results["resumed"],
        skipped=results["skipped"],
    )
    return results


def _is_approved_but_not_resumed(
    ctx: RunbookExecutionContext,
    now_utc: datetime,
) -> bool:
    """승인 완료되었으나 재개되지 않은 런북 감지."""
    try:
        from selfhealing.services.runbook.approval_gate import (
            get_runbook_approval_gate,
        )

        gate = get_runbook_approval_gate()
        request = gate._load_approval_request(ctx.execution_id)
        if request is None:
            return False

        approved_statuses = {
            ApprovalDecisionType.MANUALLY_APPROVED,
            ApprovalDecisionType.TIMER_APPROVED,
        }
        if request.status not in approved_statuses:
            return False

        if request.decided_at:
            decided = datetime.fromisoformat(request.decided_at)
            if decided.tzinfo is None:
                decided = decided.replace(tzinfo=timezone.utc)
            return (
                now_utc - decided
            ).total_seconds() > APPROVED_STALE_THRESHOLD_SECONDS

    except Exception:
        pass

    return False
