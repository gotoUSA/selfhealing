"""
Gunicorn hook helpers for selfhealing.

Provides single-function entry points for Gunicorn lifecycle hooks.
Consumer only needs to call these functions in their gunicorn.conf.py.

Usage:
    # gunicorn.conf.py
    def post_fork(server, worker):
        from django.db import connections
        for conn in connections.all():
            conn.close()
        from selfhealing.server import post_fork_reset
        post_fork_reset(worker)

    def post_worker_init(worker):
        from selfhealing.server import post_worker_init_start
        post_worker_init_start(worker)

    def worker_exit(server, worker):
        from selfhealing.server import worker_exit_cleanup
        worker_exit_cleanup(worker)
"""

import logging
import time

logger = logging.getLogger("gunicorn.error")

_SHUTDOWN_BUDGET_MARGIN_SECONDS = 10


def post_fork_reset(worker):
    """Reset all selfhealing external connections after fork.

    Covers: Redis, Kafka, OpenTelemetry, mmap, RNG.
    DB connection reset is NOT included — that is Django's responsibility.

    Args:
        worker: Gunicorn worker instance
    """
    _reset_redis(worker)
    _reset_kafka(worker)
    _reset_otel(worker)
    _reset_mmap(worker)
    _reseed_rng(worker)


def post_worker_init_start(worker):
    """Start background threads in forked worker.

    Args:
        worker: Gunicorn worker instance
    """
    import os

    os.environ["GUNICORN_WORKER"] = "1"

    try:
        from selfhealing.adapters.django.apps import SelfHealingConfig

        SelfHealingConfig.start_background_threads()
        logger.info("Worker %s: selfhealing background threads started", worker.pid)
    except ImportError:
        logger.warning("Worker %s: selfhealing package not installed", worker.pid)
    except Exception as exc:
        logger.warning(
            "Worker %s: background thread startup failed: %s", worker.pid, exc
        )


def worker_exit_cleanup(worker):
    """Trigger graceful shutdown pipeline on worker exit.

    Time Budget pattern: distributes the available budget (gunicorn timeout
    minus margin) dynamically across shutdown stages. If budget is exceeded,
    writes an emergency dump to local disk.

    Args:
        worker: Gunicorn worker instance
    """
    gunicorn_timeout = getattr(worker.cfg, "timeout", 60)
    budget = float(gunicorn_timeout) - _SHUTDOWN_BUDGET_MARGIN_SECONDS
    deadline = time.monotonic() + budget

    # 1. Background threads graceful stop
    remaining = deadline - time.monotonic()
    if remaining > 0:
        _stop_background_threads(worker)

    # 2. Leader elector shutdown
    remaining = deadline - time.monotonic()
    if remaining > 0:
        _shutdown_leader_electors(worker)

    # 3. Audit system shutdown
    remaining = deadline - time.monotonic()
    if remaining > 0:
        _shutdown_audit_system(worker)

    remaining = deadline - time.monotonic()
    if remaining < 0:
        _emergency_dump(worker)

    logger.info("Worker %s: selfhealing graceful shutdown completed", worker.pid)


def _stop_background_threads(worker):
    """Graceful stop for background daemon threads.

    Daemon threads are killed without interruption on process exit.
    This gives in-flight batch/metric pushes a chance to complete.
    """
    try:
        from selfhealing.adapters.django.apps import SelfHealingConfig

        SelfHealingConfig.stop_background_threads()
    except Exception as exc:
        logger.warning(
            "Worker %s: background thread stop failed: %s",
            worker.pid,
            exc,
        )


def _shutdown_leader_electors(worker):
    """Shutdown all registered leader electors."""
    try:
        from selfhealing.coordination.shutdown_integration import (
            _shutdown_all_electors,
        )

        _shutdown_all_electors()
    except Exception as exc:
        logger.warning(
            "Worker %s: leader elector shutdown failed: %s",
            worker.pid,
            exc,
        )


def _shutdown_audit_system(worker):
    """Shutdown audit system (flush WAL, save checkpoint)."""
    try:
        from selfhealing.audit.async_audit_lifecycle import (
            graceful_shutdown_audit_system,
        )

        graceful_shutdown_audit_system()
    except Exception as exc:
        logger.warning(
            "Worker %s: audit system shutdown failed: %s",
            worker.pid,
            exc,
        )


def _emergency_dump(worker):
    """Write minimal state to local disk when shutdown budget is exceeded.

    Preserves enough state for recovery on next boot, preventing complete
    data loss from SIGKILL.
    """
    import json
    import os
    from datetime import datetime, timezone
    from pathlib import Path

    dump_dir = Path(
        os.environ.get(
            "SELFHEALING_EMERGENCY_DUMP_DIR",
            "/tmp/selfhealing_emergency",
        )
    )
    dump_dir.mkdir(parents=True, exist_ok=True)
    dump_file = dump_dir / f"worker_{worker.pid}_{int(time.time())}.json"

    try:
        dump_file.write_text(
            json.dumps(
                {
                    "worker_pid": worker.pid,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "reason": "shutdown_budget_exceeded",
                }
            )
        )
        logger.warning(
            "Worker %s: shutdown budget exceeded, emergency dump written to %s",
            worker.pid,
            dump_file,
        )
    except Exception as exc:
        logger.error(
            "Worker %s: emergency dump failed: %s",
            worker.pid,
            exc,
        )


# === Internal reset functions ===


def _reset_redis(worker):
    """Reset Redis connections after fork.

    Creates a fresh RedisCacheAdapter and calls reconnect() to
    disconnect inherited pool connections and establish new ones.
    """
    from selfhealing.adapters.cache.redis_adapter import RedisCacheAdapter

    try:
        adapter = RedisCacheAdapter()
        adapter.reconnect()
        logger.info("Worker %s: Redis connections reset", worker.pid)
    except Exception as exc:
        logger.warning("Worker %s: Redis reset failed: %s", worker.pid, exc)


def _reset_kafka(worker):
    """Reset Kafka producer after fork (fork-safe).

    Uses reset_kafka_producer_after_fork() which only drops the reference
    without calling close()/flush(), avoiding deadlock from dead librdkafka
    background threads.
    """
    from selfhealing.adapters.kafka.config import get_kafka_settings

    settings = get_kafka_settings()
    if not settings.bootstrap_servers:
        logger.debug("Worker %s: Kafka not configured, skipping reset", worker.pid)
        return
    from selfhealing.adapters.kafka.producer import reset_kafka_producer_after_fork

    reset_kafka_producer_after_fork()
    logger.info("Worker %s: Kafka producer reset (fork-safe)", worker.pid)


def _reset_otel(worker):
    """Reset OpenTelemetry state after fork.

    Drops master's gRPC channels/thread references so the worker
    creates fresh Exporters on next use (Reset + Lazy Reinitialize).
    """
    from selfhealing.observability import reset_opentelemetry

    reset_opentelemetry()
    logger.info("Worker %s: OpenTelemetry reset", worker.pid)


def _reset_mmap(worker):
    """Re-initialize mmap CB snapshot as Reader after fork.

    mmap FD itself is shared via MAP_SHARED, but the Python wrapper
    (Lock, daemon thread, Writer flag) is not fork-safe. Destroy the
    master's is_writer=True singleton and create a Reader instance.

    1-Writer N-Reader pattern:
    - reset_cb_state_snapshot(): destroys master singleton
    - get_cb_state_snapshot(is_writer=False): creates Reader
    - Reader uses atomic write guarantee for lock-free reads
    """
    from selfhealing.adapters.ipc import (
        get_cb_state_snapshot,
        reset_cb_state_snapshot,
    )

    reset_cb_state_snapshot()
    get_cb_state_snapshot(is_writer=False)
    logger.info("Worker %s: mmap CB snapshot re-initialized as reader", worker.pid)


def _reseed_rng(worker):
    """Reseed random number generator after fork."""
    import random

    random.seed()
    logger.info("Worker %s: RNG reseeded", worker.pid)
