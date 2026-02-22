"""
Saga Celery Tasks.

Saga 오케스트레이터를 위한 Celery 태스크.

주요 태스크:
- resume_saga_instance_task: 중단된 Saga 인스턴스 재개
- scan_orphan_sagas: 고아/중단 Saga를 주기적으로 스캔하여 재개
"""

from datetime import datetime, timezone
from typing import Any

import structlog

# Celery는 선택적 의존성
try:
    from celery import shared_task
except ImportError:
    # Celery가 없으면 dummy decorator
    def shared_task(*args, **kwargs):  # type: ignore[misc]
        def decorator(func):
            func.delay = lambda *a, **kw: None
            func.apply_async = lambda *a, **kw: None
            return func

        if args and callable(args[0]):
            return decorator(args[0])
        return decorator


from selfhealing.services.saga.models import SagaStatus

logger = structlog.get_logger()

# =============================================================================
# 상수
# =============================================================================

STALE_THRESHOLD_SECONDS = 300
"""고아 Saga 판별 임계값 (초). 5분."""


# =============================================================================
# resume_saga_instance_task
# =============================================================================


@shared_task(
    name="selfhealing.resume_saga_instance",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    queue="selfhealing_recovery",
)
def resume_saga_instance_task(self, instance_id: str) -> dict[str, Any]:
    """중단된 Saga 인스턴스를 재개하는 Celery Task.

    호출 경로:
    1. scan_orphan_sagas() Beat Task → 고아 감지 시 디스패치
    2. _execute_forward() Async Yield → RETRY_SCHEDULED + countdown으로 디스패치
    3. 외부 API → 수동 재개 트리거

    Args:
        instance_id: 재개할 Saga 인스턴스 ID

    Returns:
        최종 상태를 포함하는 dict
    """
    from selfhealing.services.saga.orchestrator import SagaOrchestrator

    orchestrator = SagaOrchestrator()
    instance = orchestrator.resume_saga(instance_id)
    return {
        "instance_id": instance_id,
        "final_status": instance.status.value,
    }


# =============================================================================
# scan_orphan_sagas
# =============================================================================


@shared_task(
    name="selfhealing.scan_orphan_sagas",
    queue="selfhealing_recovery",
)
def scan_orphan_sagas() -> dict[str, Any]:
    """고아/중단 Saga를 주기적으로 스캔하여 재개.

    Celery Beat로 2분 간격 실행.

    고아 판별 기준:
    1. status == RUNNING 이지만 updated_at이 STALE_THRESHOLD(5분)보다 오래됨
    2. status == SUSPENDED 이고 해당 서킷브레이커가 CLOSED로 복귀
    3. status == COMPENSATING 이지만 updated_at이 STALE_THRESHOLD보다 오래됨

    GC Pause 방어:
    - 재개를 트리거할 때 반드시 "신규 분산 락 획득 시도"를 먼저 수행
    - 락 획득 성공 시에만 resume_saga_instance_task를 dispatch

    Returns:
        스캔 결과 요약 dict
    """
    results: dict[str, Any] = {"scanned": 0, "resumed": 0, "skipped": 0}

    # 비-터미널 상태의 모든 Saga 인스턴스 스캔
    active_instances = _scan_active_saga_instances()
    results["scanned"] = len(active_instances)

    now_utc = datetime.now(timezone.utc)

    for instance in active_instances:
        is_stale = False

        if instance.status in {SagaStatus.RUNNING, SagaStatus.COMPENSATING}:
            # started_at이 STALE_THRESHOLD보다 오래되면 고아
            if instance.started_at:
                try:
                    last_update = datetime.fromisoformat(instance.started_at)
                    is_stale = (now_utc - last_update).total_seconds() > STALE_THRESHOLD_SECONDS
                except (ValueError, TypeError):
                    is_stale = True

        elif instance.status == SagaStatus.SUSPENDED:
            # SUSPENDED는 항상 재개 시도 (resume_saga에서 CB 재확인)
            is_stale = True

        if not is_stale:
            results["skipped"] += 1
            continue

        # GC Pause 방어: 락 획득 시도
        lock_namespace = f"saga:{instance.saga_name}:{instance.id}"
        acquired = _try_acquire_lock(lock_namespace, instance.id)

        if acquired:
            # 즉시 해제 — resume_saga_instance_task가 다시 획득
            _release_lock(lock_namespace, instance.id)

            # Celery Task로 재개 위임
            resume_saga_instance_task.delay(instance.id)
            results["resumed"] += 1
        else:
            # 다른 워커가 처리 중 — skip
            results["skipped"] += 1

    return results


# =============================================================================
# 내부 헬퍼 함수
# =============================================================================


def _scan_active_saga_instances():
    """비-터미널 상태의 활성 Saga 인스턴스를 스캔한다.

    Returns:
        SagaInstance 목록
    """
    try:
        from selfhealing.core.state_backend import get_state_backend
        from selfhealing.services.saga.models import SagaInstance

        backend = get_state_backend()

        # 패턴 기반 키 조회 (Redis SCAN 또는 InMemory 순회)
        if hasattr(backend, "scan"):
            keys = backend.scan("saga:instance:*")
        elif hasattr(backend, "_data"):
            # InMemoryStateBackend
            keys = [k for k in backend._data if k.startswith("saga:instance:")]
        else:
            return []

        terminal_statuses = {
            SagaStatus.COMPLETED.value,
            SagaStatus.COMPENSATED.value,
            SagaStatus.COMPENSATION_FAILED.value,
            SagaStatus.TIMED_OUT.value,
        }

        instances = []
        for key in keys:
            try:
                data = backend.get(key)
                if data is None:
                    continue
                if isinstance(data, str):
                    import json

                    data = json.loads(data)
                if data.get("status") not in terminal_statuses:
                    instances.append(SagaInstance.from_dict(data))
            except Exception:
                continue

        return instances
    except Exception as e:
        logger.exception(
            "[Saga] scan_active_saga_instances failed",
            extra={"error": str(e)},
        )
        return []


def _try_acquire_lock(lock_namespace: str, instance_id: str) -> bool:
    """분산 락 획득을 시도한다."""
    try:
        from selfhealing.services.coordination.distributed_recovery_lock import (
            DistributedRecoveryLock,
        )

        lock = DistributedRecoveryLock()
        return lock.acquire(
            namespace=lock_namespace,
            session_id=instance_id,
        )
    except Exception:
        return False


def _release_lock(lock_namespace: str, instance_id: str) -> None:
    """분산 락을 해제한다."""
    try:
        from selfhealing.services.coordination.distributed_recovery_lock import (
            DistributedRecoveryLock,
        )

        lock = DistributedRecoveryLock()
        lock.release(
            namespace=lock_namespace,
            session_id=instance_id,
        )
    except Exception:
        pass


def get_saga_beat_schedule() -> dict[str, Any]:
    """Saga 관련 Celery Beat 스케줄을 반환한다.

    Returns:
        Beat 스케줄 dict
    """
    return {
        "scan-orphan-sagas": {
            "task": "selfhealing.scan_orphan_sagas",
            "schedule": 120.0,  # 2분 간격
            "options": {"queue": "selfhealing_recovery"},
        },
    }
