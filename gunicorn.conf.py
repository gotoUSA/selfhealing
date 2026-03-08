"""
Gunicorn configuration for Self-Healing Django application.

Preload mode (--preload) is enabled in staging/production to:
- Initialize Django + Settings once in Master, then fork Workers (CoW memory sharing)
- Reduce cold-start time by ~75% (4 Worker initializations → 1)

Fork-safety:
- post_fork: Reset all external connections (DB, Redis, Kafka, OTEL, mmap) + RNG reseed
- post_worker_init: Restart background threads that died during fork
- worker_exit: Trigger graceful shutdown pipeline

Environment variable DEPLOYMENT_ENV controls preload/reload toggle:
- "development" (default): preload=OFF, reload=ON (hot reload for local dev)
- "staging" / "production": preload=ON, reload=OFF (cold-start optimization)
"""

import logging
import os
import sys

logger = logging.getLogger("gunicorn.error")

# ---------------------------------------------------------------------------
# Worker configuration
# ---------------------------------------------------------------------------
workers = int(os.environ.get("GUNICORN_WORKERS", 4))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", 4))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 60))

# Access/Error logging
accesslog = "-"
errorlog = "-"

# Bind
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")

# ---------------------------------------------------------------------------
# Environment-based preload/reload toggle (Section 4.1)
# --preload and --reload are mutually exclusive in Gunicorn.
# ---------------------------------------------------------------------------
_deployment_env = os.environ.get("DEPLOYMENT_ENV", "development")

if _deployment_env == "development":
    preload_app = False
    reload = True
else:
    preload_app = True
    reload = False


# ---------------------------------------------------------------------------
# post_fork: Connection reset + Fail-fast (Sections 5.1, 5.5, 5.6)
#
# Master에서 열린 소켓 FD가 fork 후 Worker에 복제되면 패킷 충돌 및
# 데이터 오염이 발생한다. 모든 외부 커넥션을 리셋하고, 실패 시
# Worker를 즉시 종료하여 Gunicorn Master가 새 Worker를 띄우도록 한다.
# ---------------------------------------------------------------------------
def post_fork(server, worker):
    """Reset all external connections after fork (Fail-fast)."""
    try:
        _reset_db_connections(worker)
        _reset_redis_connections(worker)
        _reset_kafka_producer(worker)
        _reset_opentelemetry(worker)
        _reset_mmap_descriptors(worker)
        _reseed_rng(worker)
    except Exception as exc:
        logger.error("Worker %s: post_fork failed: %s", worker.pid, exc)
        sys.exit(1)


def _reset_db_connections(worker):
    """Close all Django DB connections inherited from Master."""
    from django.db import connections

    for conn in connections.all():
        conn.close()
    logger.info("Worker %s: DB connections reset", worker.pid)


def _reset_redis_connections(worker):
    """Disconnect Redis connection pools inherited from Master."""
    try:
        from selfhealing.adapters.cache.redis_adapter import RedisCacheAdapter

        adapter = RedisCacheAdapter.get_instance()
        if adapter:
            adapter.reconnect()
    except ImportError:
        pass
    logger.info("Worker %s: Redis connections reset", worker.pid)


def _reset_kafka_producer(worker):
    """Destroy Kafka Producer singleton inherited from Master."""
    try:
        from selfhealing.adapters.kafka.producer import reset_kafka_producer

        reset_kafka_producer()
    except ImportError:
        pass
    logger.info("Worker %s: Kafka producer reset", worker.pid)


def _reset_opentelemetry(worker):
    """Reset OTEL gRPC exporter state inherited from Master."""
    try:
        from selfhealing.observability import reset_opentelemetry

        reset_opentelemetry()
    except ImportError:
        pass
    logger.info("Worker %s: OpenTelemetry reset", worker.pid)


def _reset_mmap_descriptors(worker):
    """Close mmap file descriptors inherited from Master."""
    try:
        from selfhealing.adapters.ipc import reset_cb_state_snapshot

        reset_cb_state_snapshot()
    except ImportError:
        pass
    logger.info("Worker %s: mmap descriptors reset", worker.pid)


def _reseed_rng(worker):
    """Reseed random.seed() with OS entropy to prevent identical Worker sequences."""
    import random

    random.seed()
    logger.info("Worker %s: RNG reseeded", worker.pid)


# ---------------------------------------------------------------------------
# post_worker_init: Background thread restart (Section 5.2)
#
# fork()는 호출한 메인 스레드만 복사하므로 Master에서 생성된
# background thread/timer는 Worker에 상속되지 않는다.
# Worker 초기화 완료 후 background thread를 재시작한다.
# ---------------------------------------------------------------------------
def post_worker_init(worker):
    """Start background threads in each Worker after fork."""
    os.environ["GUNICORN_WORKER"] = "1"

    try:
        from selfhealing.adapters.django.apps import SelfHealingConfig

        SelfHealingConfig.start_background_threads()
        worker.log.info("Worker %s: background threads started", worker.pid)
    except ImportError:
        worker.log.debug("Worker %s: selfhealing not available, skipping", worker.pid)
    except Exception as exc:
        worker.log.warning(
            "Worker %s: background thread startup failed: %s", worker.pid, exc
        )


# ---------------------------------------------------------------------------
# worker_exit: Graceful shutdown pipeline (Section 5.3)
#
# Gunicorn 환경에서 signal 통제권은 Gunicorn Master(Arbiter)에 있다.
# Worker 종료 시 worker_exit 훅에서 shutdown 파이프라인을 수동 호출한다.
# ---------------------------------------------------------------------------
def worker_exit(server, worker):
    """Trigger graceful shutdown pipeline on Worker exit."""
    try:
        from selfhealing.coordination.shutdown_integration import (
            graceful_shutdown_leader_elector,
        )

        graceful_shutdown_leader_elector()
    except (ImportError, Exception):
        pass

    try:
        from selfhealing.audit.async_audit_lifecycle import (
            graceful_shutdown_audit_system,
        )

        graceful_shutdown_audit_system()
    except (ImportError, Exception):
        pass

    worker.log.info("Worker %s: graceful shutdown completed", worker.pid)
