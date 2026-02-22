"""
Redis Audit 버퍼 플러시 Celery Tasks.

Redis 버퍼에 누적된 Audit 이벤트를 PostgreSQL/Kafka로 배치 전송.

Tasks:
1. flush_redis_audit_to_db - Redis → PostgreSQL 플러시
2. retry_audit_fallback_buffer - 폴백 버퍼 재시도
"""

from __future__ import annotations

import os
import time
from typing import Any

import structlog

logger = structlog.get_logger()


# =============================================================================
# 환경 변수 설정
# =============================================================================

REDIS_AUDIT_BATCH_SIZE = int(os.environ.get("SELFHEALING_REDIS_BATCH_SIZE", "500"))
REDIS_AUDIT_FLUSH_INTERVAL = int(os.environ.get("SELFHEALING_REDIS_FLUSH_INTERVAL", "10"))


# =============================================================================
# Thin Task Wrappers
# =============================================================================


def flush_redis_audit_to_db(
    batch_size: int | None = None,
    delete_after: bool = True,
) -> dict[str, Any]:
    """
    Redis Audit 버퍼 → PostgreSQL 플러시.

    Celery Beat에서 주기적으로 호출하여 대량 이벤트를 배치 처리.

    Args:
        batch_size: 배치 크기 (기본: 환경변수 또는 500)
        delete_after: 전송 후 Redis에서 삭제 여부

    Returns:
        dict: {
            "success": bool,
            "flushed_count": int,
            "duration_ms": float,
        }
    """
    from selfhealing.adapters.audit.redis_buffer import create_redis_audit_buffer

    start = time.time()
    batch_size = batch_size or REDIS_AUDIT_BATCH_SIZE

    try:
        # Redis 버퍼 가져오기
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        redis_buffer = create_redis_audit_buffer(redis_url)

        if redis_buffer is None:
            logger.warning("flush_redis_audit_to_db.redis_buffer_unavailable")
            return {
                "success": False,
                "flushed_count": 0,
                "duration_ms": 0,
                "error": "Redis buffer unavailable",
            }

        # DB 어댑터 가져오기
        from selfhealing.adapters.audit.django_adapter import DjangoAuditLogAdapter

        db_adapter = DjangoAuditLogAdapter()

        # 플러시 실행
        flushed_count = redis_buffer.flush_to_external(
            target_adapter=db_adapter,
            batch_size=batch_size,
        )

        duration_ms = (time.time() - start) * 1000

        logger.info(
            "flush_redis_audit_to_db.flushed_entries_ms",
            flushed_count=flushed_count,
            duration_ms=duration_ms,
        )

        return {
            "success": True,
            "flushed_count": flushed_count,
            "duration_ms": round(duration_ms, 2),
        }

    except Exception as e:
        duration_ms = (time.time() - start) * 1000
        logger.exception(
            "flush_redis_audit_to_db.failed",
            error=e,
        )
        return {
            "success": False,
            "flushed_count": 0,
            "duration_ms": round(duration_ms, 2),
            "error": str(e),
        }


def retry_audit_fallback_buffer() -> dict[str, Any]:
    """
    폴백 버퍼에 저장된 Audit 이벤트를 Redis로 재시도.

    Redis 장애 복구 후 호출하여 임시 저장된 이벤트 복구.

    Returns:
        dict: {
            "success": bool,
            "recovered_count": int,
            "remaining_count": int,
        }
    """
    from selfhealing.adapters.audit.redis_buffer import create_redis_audit_buffer

    try:
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        redis_buffer = create_redis_audit_buffer(redis_url)

        if redis_buffer is None:
            logger.warning("retry_audit_fallback_buffer.redis_buffer_unavailable")
            return {
                "success": False,
                "recovered_count": 0,
                "remaining_count": -1,
                "error": "Redis buffer unavailable",
            }

        recovered_count = redis_buffer.retry_fallback_buffer()
        remaining_count = redis_buffer.get_fallback_buffer_size()

        logger.info(
            "retry_audit_fallback_buffer.recovered_entries_remaining",
            recovered_count=recovered_count,
            remaining_count=remaining_count,
        )

        return {
            "success": True,
            "recovered_count": recovered_count,
            "remaining_count": remaining_count,
        }

    except Exception as e:
        logger.exception(
            "retry_audit_fallback_buffer.failed",
            error=e,
        )
        return {
            "success": False,
            "recovered_count": 0,
            "remaining_count": -1,
            "error": str(e),
        }


def get_redis_audit_buffer_stats() -> dict[str, Any]:
    """
    Redis Audit 버퍼 상태 조회.

    Returns:
        dict: 버퍼 통계 정보
    """
    from selfhealing.adapters.audit.redis_buffer import create_redis_audit_buffer

    try:
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        redis_buffer = create_redis_audit_buffer(redis_url)

        if redis_buffer is None:
            return {"error": "Redis buffer unavailable"}

        return redis_buffer.get_buffer_stats()

    except Exception as e:
        logger.exception(
            "get_redis_audit_buffer_stats.failed",
            error=e,
        )
        return {"error": str(e)}


# =============================================================================
# Celery Beat Schedule
# =============================================================================


def get_audit_flush_beat_schedule() -> dict[str, Any]:
    """
    Redis Audit 플러시 Celery Beat 스케줄 반환.

    Returns:
        dict: Beat 스케줄 설정
    """
    return {
        # Redis Audit 버퍼 → DB 플러시 (10초마다)
        "flush-redis-audit-to-db": {
            "task": "selfhealing.tasks.audit_flush.flush_redis_audit_to_db",
            "schedule": float(REDIS_AUDIT_FLUSH_INTERVAL),
            "options": {
                "queue": "audit_flush",
                "expires": REDIS_AUDIT_FLUSH_INTERVAL * 3,  # 만료 시간
            },
        },
        # 폴백 버퍼 재시도 (1분마다)
        "retry-audit-fallback-buffer": {
            "task": "selfhealing.tasks.audit_flush.retry_audit_fallback_buffer",
            "schedule": 60.0,
            "options": {
                "queue": "audit_flush",
                "expires": 120,
            },
        },
    }


# =============================================================================
# Celery Task Registration
# =============================================================================


def register_audit_flush_tasks_with_celery(app) -> None:
    """
    Celery 앱에 Audit 플러시 태스크 등록.

    Args:
        app: Celery 앱 인스턴스
    """
    app.task(
        bind=True,
        name="selfhealing.tasks.audit_flush.flush_redis_audit_to_db",
        max_retries=3,
        default_retry_delay=30,
        acks_late=True,
    )(lambda self, **kwargs: flush_redis_audit_to_db(**kwargs))

    app.task(
        bind=True,
        name="selfhealing.tasks.audit_flush.retry_audit_fallback_buffer",
        max_retries=3,
        default_retry_delay=60,
        acks_late=True,
    )(lambda self: retry_audit_fallback_buffer())

    logger.info("cell_registry.bulkheads_registered")


__all__ = [
    "flush_redis_audit_to_db",
    "retry_audit_fallback_buffer",
    "get_redis_audit_buffer_stats",
    "get_audit_flush_beat_schedule",
    "register_audit_flush_tasks_with_celery",
]
