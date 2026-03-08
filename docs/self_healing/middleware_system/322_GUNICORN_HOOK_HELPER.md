# 322. Gunicorn Hook Helper — Fork Hook 통합 함수

> **Status**: Planning
> **Severity**: P2 (MEDIUM) — repo 분리 선행 조건
> **Target**: `selfhealing/server.py` (신규)
> **References**:
> - 319 — Repo Separation Overview (커플링 C4)
> - 316 — Gunicorn Preload Optimization

---

## 1. 현황 및 문제

### 1.1 현재: Consumer가 fork hook 8개를 직접 작성

`gunicorn.conf.py`에 selfhealing import 8개를 직접 나열하고, 각각 try/except ImportError로 감싼다.

```python
# gunicorn.conf.py — 현재 상태 (약 80줄의 selfhealing 코드)
def post_fork(server, worker):
    _reset_db_connections(worker)      # Django 표준
    _reset_redis_connections(worker)   # selfhealing import
    _reset_kafka_producer(worker)      # selfhealing import
    _reset_opentelemetry(worker)       # selfhealing import
    _reset_mmap_descriptors(worker)    # selfhealing import
    _reseed_rng(worker)                # stdlib

def post_worker_init(worker):
    from selfhealing.adapters.django.apps import SelfHealingConfig  # selfhealing import
    SelfHealingConfig.start_background_threads()

def worker_exit(server, worker):
    from selfhealing.coordination.shutdown_integration import ...  # selfhealing import
    from selfhealing.audit.async_audit_lifecycle import ...        # selfhealing import
```

**문제**: 새 consumer에서 이 80줄을 다시 작성해야 함

---

## 2. 설계

### 2.1 selfhealing/server.py (신규)

```python
# selfhealing/server.py
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

logger = logging.getLogger("gunicorn.error")


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
        logger.debug("Worker %s: selfhealing not available", worker.pid)
    except Exception as exc:
        logger.warning("Worker %s: background thread startup failed: %s", worker.pid, exc)


def worker_exit_cleanup(worker):
    """Trigger graceful shutdown pipeline on worker exit.

    Args:
        worker: Gunicorn worker instance
    """
    try:
        from selfhealing.coordination.shutdown_integration import (
            graceful_shutdown_leader_elector,
        )
        graceful_shutdown_leader_elector()
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("Worker %s: leader elector shutdown failed: %s", worker.pid, exc)

    try:
        from selfhealing.audit.async_audit_lifecycle import (
            graceful_shutdown_audit_system,
        )
        graceful_shutdown_audit_system()
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("Worker %s: audit system shutdown failed: %s", worker.pid, exc)

    logger.info("Worker %s: selfhealing graceful shutdown completed", worker.pid)


# === Internal reset functions ===

def _reset_redis(worker):
    try:
        from selfhealing.adapters.cache.redis_adapter import RedisCacheAdapter
        adapter = RedisCacheAdapter.get_instance()
        if adapter:
            adapter.reconnect()
    except ImportError:
        pass
    logger.info("Worker %s: Redis connections reset", worker.pid)


def _reset_kafka(worker):
    try:
        from selfhealing.adapters.kafka.producer import reset_kafka_producer
        reset_kafka_producer()
    except ImportError:
        pass
    logger.info("Worker %s: Kafka producer reset", worker.pid)


def _reset_otel(worker):
    try:
        from selfhealing.observability import reset_opentelemetry
        reset_opentelemetry()
    except ImportError:
        pass
    logger.info("Worker %s: OpenTelemetry reset", worker.pid)


def _reset_mmap(worker):
    try:
        from selfhealing.adapters.ipc import reset_cb_state_snapshot
        reset_cb_state_snapshot()
    except ImportError:
        pass
    logger.info("Worker %s: mmap descriptors reset", worker.pid)


def _reseed_rng(worker):
    import random
    random.seed()
    logger.info("Worker %s: RNG reseeded", worker.pid)
```

### 2.2 Consumer 사용법 (분리 후)

```python
# gunicorn.conf.py — 분리 후 (약 30줄, 기존 80줄 → 62% 감소)
import os
import logging

logger = logging.getLogger("gunicorn.error")

workers = int(os.environ.get("GUNICORN_WORKERS", 4))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", 4))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 60))
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")

_deployment_env = os.environ.get("DEPLOYMENT_ENV", "development")
preload_app = _deployment_env != "development"
reload = _deployment_env == "development"


def post_fork(server, worker):
    # Django DB connections (framework 표준)
    from django.db import connections
    for conn in connections.all():
        conn.close()
    # selfhealing resources (1줄)
    from selfhealing.server import post_fork_reset
    post_fork_reset(worker)


def post_worker_init(worker):
    from selfhealing.server import post_worker_init_start
    post_worker_init_start(worker)


def worker_exit(server, worker):
    from selfhealing.server import worker_exit_cleanup
    worker_exit_cleanup(worker)
```

---

## 3. 테스트 계획

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | `post_fork_reset()` 호출 | Redis/Kafka/OTEL/mmap/RNG 리셋 함수 모두 호출 확인 |
| 2 | selfhealing 미설치 환경 | ImportError 시 graceful skip 확인 |
| 3 | `post_worker_init_start()` | background threads 시작 확인 |
| 4 | `worker_exit_cleanup()` | leader elector + audit system 종료 확인 |
