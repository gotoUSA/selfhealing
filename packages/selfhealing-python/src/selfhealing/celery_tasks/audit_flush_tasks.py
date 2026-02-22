"""
Audit Buffer 플러시 Celery 태스크.

Redis Audit 버퍼를 외부 저장소로 안전하게 플러시.
분산 락으로 동시 실행 방지.
"""

from __future__ import annotations

import time
from datetime import timedelta

from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


@shared_task(
    bind=True,
    name="selfhealing.celery_tasks.flush_redis_audit_buffer",
    queue="audit_flush",
    max_retries=3,
    default_retry_delay=30,
    time_limit=300,
    soft_time_limit=290,
    acks_late=True,
)
def flush_redis_audit_buffer(
    self,
    batch_size: int = 500,
) -> dict:
    """
    Redis Audit 버퍼를 외부 저장소로 플러시.

    분산 락으로 동시 실행을 방지하여 중복 처리 없음.
    Processing Queue 패턴으로 데이터 손실 방지.

    Args:
        batch_size: 배치 크기 (기본 500)

    Returns:
        플러시 결과 딕셔너리
    """
    start_time = time.time()
    task_id = self.request.id or "unknown"

    # 분산 락 획득 시도
    lock_acquired = False
    lock = None
    lock_namespace = "audit-buffer-flush"
    session_id = f"celery-{task_id}"

    try:
        from selfhealing.services.coordination.distributed_recovery_lock import (
            DistributedRecoveryLock,
        )

        lock = DistributedRecoveryLock(lock_timeout=timedelta(minutes=5))
        lock_acquired = lock.acquire(
            namespace=lock_namespace,
            session_id=session_id,
            blocking=False,
        )

    except ImportError:
        # 분산 락 없이 진행 (단일 워커 환경)
        logger.warning("redis_audit_buffer.distributed_lock_unavailable")
        lock_acquired = True
    except Exception as e:
        logger.exception(
            "flush_redis_audit_buffer.lock_acquisition_error",
            error=e,
        )
        lock_acquired = True  # Fail-open

    if not lock_acquired:
        logger.info(
            "[flush_redis_audit_buffer] Skipped - another worker is processing",
            extra={"task_id": task_id},
        )
        return {
            "status": "skipped",
            "reason": "lock_not_acquired",
            "task_id": task_id,
        }

    try:
        from selfhealing.adapters.audit.redis_buffer import RedisAuditBuffer

        # Redis 버퍼 인스턴스 획득
        redis_buffer = _get_redis_buffer()
        if redis_buffer is None:
            return {
                "status": "error",
                "reason": "redis_buffer_not_available",
                "task_id": task_id,
            }

        # 외부 어댑터 획득
        target_adapter = _get_target_adapter()
        if target_adapter is None:
            return {
                "status": "error",
                "reason": "target_adapter_not_available",
                "task_id": task_id,
            }

        # 안전한 플러시 실행
        flushed_count = redis_buffer.flush_to_external_safe(
            target_adapter=target_adapter,
            batch_size=batch_size,
        )

        duration_ms = (time.time() - start_time) * 1000

        logger.info(
            f"[flush_redis_audit_buffer] Completed",  # noqa: G004
            extra={
                "flushed_count": flushed_count,
                "duration_ms": round(duration_ms, 1),
                "task_id": task_id,
            },
        )

        # 메트릭 기록
        try:
            from selfhealing.metrics.audit_buffer_metrics import record_flush

            record_flush("all", flushed_count)
        except ImportError:
            pass

        return {
            "status": "success",
            "flushed_count": flushed_count,
            "duration_ms": round(duration_ms, 1),
            "task_id": task_id,
        }

    except Exception as e:
        logger.exception(
            "flush_redis_audit_buffer.failed",
            error=e,
        )

        # 재시도
        raise self.retry(exc=e)

    finally:
        # 락 해제
        if lock is not None and lock_acquired:
            try:
                lock.release(namespace=lock_namespace, session_id=session_id)
            except Exception as e:
                logger.warning(
                    "flush_redis_audit_buffer.lock_release_failed",
                    error=e,
                )


@shared_task(
    bind=True,
    name="selfhealing.celery_tasks.recover_orphaned_processing_queues",
    queue="audit_flush",
    max_retries=1,
    time_limit=120,
    soft_time_limit=110,
)
def recover_orphaned_processing_queues(
    self,
    timeout_seconds: int = 300,
) -> dict:
    """
    타임아웃된 고아 Processing Queue 복구.

    5분 이상 처리되지 않은 Processing Queue의 항목을 Buffer로 복원.

    Args:
        timeout_seconds: 고아 판단 임계 시간 (기본 5분)

    Returns:
        복구 결과 딕셔너리
    """
    task_id = self.request.id or "unknown"

    try:
        redis_buffer = _get_redis_buffer()
        if redis_buffer is None:
            return {
                "status": "error",
                "reason": "redis_buffer_not_available",
                "task_id": task_id,
            }

        recovered_total = redis_buffer.recover_orphaned_processing_queues(timeout_seconds=timeout_seconds)

        logger.info(
            f"[recover_orphaned_processing_queues] Completed",  # noqa: G004
            extra={
                "recovered_total": recovered_total,
                "task_id": task_id,
            },
        )

        # 메트릭 기록
        try:
            from selfhealing.metrics.audit_buffer_metrics import record_orphan_recovery

            record_orphan_recovery("all", recovered_total)
        except ImportError:
            pass

        return {
            "status": "success",
            "recovered_total": recovered_total,
            "task_id": task_id,
        }

    except Exception as e:
        logger.exception(
            "recover_orphaned_processing_queues.failed",
            error=e,
        )
        return {
            "status": "error",
            "error": str(e),
            "task_id": task_id,
        }


@shared_task(
    bind=True,
    name="selfhealing.celery_tasks.apply_audit_buffer_safety_ltrim",
    queue="audit_flush",
    max_retries=0,
    time_limit=60,
)
def apply_audit_buffer_safety_ltrim(self) -> dict:
    """
    Audit 버퍼에 Safety LTRIM 적용.

    버퍼가 임계치를 초과하면 오래된 항목 삭제.
    """
    task_id = self.request.id or "unknown"

    try:
        redis_buffer = _get_redis_buffer()
        if redis_buffer is None:
            return {
                "status": "error",
                "reason": "redis_buffer_not_available",
                "task_id": task_id,
            }

        trimmed = redis_buffer.apply_safety_ltrim()

        total_trimmed = sum(trimmed.values())

        logger.info(
            f"[apply_audit_buffer_safety_ltrim] Completed",  # noqa: G004
            extra={
                "trimmed_domains": trimmed,
                "total_trimmed": total_trimmed,
                "task_id": task_id,
            },
        )

        return {
            "status": "success",
            "trimmed_domains": trimmed,
            "total_trimmed": total_trimmed,
            "task_id": task_id,
        }

    except Exception as e:
        logger.exception(
            "apply_audit_buffer_safety_ltrim.failed",
            error=e,
        )
        return {
            "status": "error",
            "error": str(e),
            "task_id": task_id,
        }


def _get_redis_buffer():
    """Redis Audit 버퍼 인스턴스 획득."""
    try:
        import os

        from selfhealing.adapters.audit.redis_buffer import create_redis_audit_buffer

        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        return create_redis_audit_buffer(redis_url)
    except Exception as e:
        logger.exception(
            "failed_create_buffer",
            error=e,
        )
        return None


def _get_target_adapter():
    """대상 어댑터 획득 (파일 어댑터 기본)."""
    try:
        import os

        from selfhealing.adapters.audit.file_adapter import FileAuditLogAdapter

        log_dir = os.environ.get(
            "SELFHEALING_AUDIT_LOG_DIR",
            "/var/log/selfhealing/audit",
        )
        return FileAuditLogAdapter(log_dir=log_dir)
    except ImportError:
        logger.warning("audit_flush.file_adapter_unavailable")
        return None
    except Exception as e:
        logger.exception(
            "failed",
            error=e,
        )
        return None


# Celery Beat 스케줄 설정
BEAT_SCHEDULE = {
    "flush-redis-audit-buffer": {
        "task": "selfhealing.celery_tasks.flush_redis_audit_buffer",
        "schedule": 10.0,  # 10초마다
        "options": {
            "queue": "audit_flush",
            "expires": 30,
        },
    },
    "recover-orphaned-processing-queues": {
        "task": "selfhealing.celery_tasks.recover_orphaned_processing_queues",
        "schedule": 300.0,  # 5분마다
        "options": {
            "queue": "audit_flush",
        },
    },
    "apply-audit-buffer-safety-ltrim": {
        "task": "selfhealing.celery_tasks.apply_audit_buffer_safety_ltrim",
        "schedule": 60.0,  # 1분마다
        "options": {
            "queue": "audit_flush",
        },
    },
}
