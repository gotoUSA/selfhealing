# 322. Gunicorn Hook Helper — Fork Hook 통합 함수

> **Status**: Planning
> **Severity**: P2 (MEDIUM) — repo 분리 선행 조건
> **Target**: `selfhealing/server.py` (신규)
> **References**:
> - 319 — Repo Separation Overview (커플링 C4)
> - 316 — Gunicorn Preload Optimization
> **Revision**: R1 — 설계 리뷰 반영 (Silent Failure 제거, Fork-Safety, Time Budget, Sidecar, GIL Metrics)

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
import time

from selfhealing.settings import get_settings

logger = logging.getLogger("gunicorn.error")

# R1: Gunicorn timeout에서 마진 10초를 제외한 종료 예산 (초)
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
        # selfhealing 패키지가 설치되지 않은 환경 (CI 등)
        logger.warning("Worker %s: selfhealing package not installed", worker.pid)
    except Exception as exc:
        logger.warning("Worker %s: background thread startup failed: %s", worker.pid, exc)


def worker_exit_cleanup(worker):
    """Trigger graceful shutdown pipeline on worker exit.

    R1: Time Budget 패턴 적용.
    Gunicorn timeout 내에서 모든 종료 작업을 완료하기 위해
    전체 예산(budget)을 각 단계에 동적으로 분배한다.
    예산 초과 시 Emergency Dump로 상태를 로컬 디스크에 기록한다.

    Args:
        worker: Gunicorn worker instance
    """
    gunicorn_timeout = getattr(worker.cfg, "timeout", 60)
    budget = float(gunicorn_timeout) - _SHUTDOWN_BUDGET_MARGIN_SECONDS
    deadline = time.monotonic() + budget

    # 1. Background threads graceful stop (daemon 스레드 유실 방지)
    remaining = deadline - time.monotonic()
    if remaining > 0:
        _stop_background_threads(worker, timeout=min(remaining, 5.0))

    # 2. Leader elector shutdown
    remaining = deadline - time.monotonic()
    if remaining > 0:
        _shutdown_leader_elector(worker, timeout=min(remaining, 5.0))

    # 3. Audit system shutdown (가장 시간 소모가 큰 단계)
    remaining = deadline - time.monotonic()
    if remaining > 0:
        _shutdown_audit_system(worker, timeout=max(remaining - 1.0, 1.0))

    # 4. 예산 초과 시 Emergency Dump
    remaining = deadline - time.monotonic()
    if remaining < 0:
        _emergency_dump(worker)

    logger.info("Worker %s: selfhealing graceful shutdown completed", worker.pid)


def _stop_background_threads(worker, timeout):
    """Background daemon 스레드들의 우아한 종료."""
    try:
        from selfhealing.adapters.django.apps import SelfHealingConfig
        SelfHealingConfig.stop_background_threads(timeout=timeout)
    except Exception as exc:
        logger.warning(
            "Worker %s: background thread stop failed: %s", worker.pid, exc,
        )


def _shutdown_leader_elector(worker, timeout):
    """Leader elector 종료."""
    try:
        from selfhealing.coordination.shutdown_integration import (
            graceful_shutdown_leader_elector,
        )
        graceful_shutdown_leader_elector(timeout=timeout)
    except Exception as exc:
        logger.warning(
            "Worker %s: leader elector shutdown failed: %s", worker.pid, exc,
        )


def _shutdown_audit_system(worker, timeout):
    """Audit 시스템 종료."""
    try:
        from selfhealing.audit.async_audit_lifecycle import (
            graceful_shutdown_audit_system,
        )
        graceful_shutdown_audit_system(timeout=timeout)
    except Exception as exc:
        logger.warning(
            "Worker %s: audit system shutdown failed: %s", worker.pid, exc,
        )


def _emergency_dump(worker):
    """종료 예산 초과 시 남은 상태를 로컬 디스크에 긴급 기록.

    WAL 동기화나 체크포인트 저장에 예산이 부족할 경우,
    SIGKILL로 데이터가 완전히 유실되는 것보다
    로컬 파일에 최소한의 상태를 남기는 것이 낫다.
    다음 부팅 시 이 파일을 읽어 복구할 수 있다.
    """
    import json
    import os
    from datetime import datetime, timezone
    from pathlib import Path

    dump_dir = Path(os.environ.get(
        "SELFHEALING_EMERGENCY_DUMP_DIR", "/tmp/selfhealing_emergency",
    ))
    dump_dir.mkdir(parents=True, exist_ok=True)
    dump_file = dump_dir / f"worker_{worker.pid}_{int(time.time())}.json"

    try:
        dump_file.write_text(json.dumps({
            "worker_pid": worker.pid,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reason": "shutdown_budget_exceeded",
        }))
        logger.warning(
            "Worker %s: shutdown budget exceeded, emergency dump written to %s",
            worker.pid, dump_file,
        )
    except Exception as exc:
        logger.error(
            "Worker %s: emergency dump failed: %s", worker.pid, exc,
        )


# === Internal reset functions ===

def _reset_redis(worker):
    """Redis 연결 재설정.

    R1: ImportError 대신 settings 플래그로 명시적 분기.
    server.py는 selfhealing 패키지 내부이므로 내부 모듈의
    ImportError는 환경 깨짐을 의미한다. 조용히 넘기지 않는다.
    """
    from selfhealing.adapters.cache.redis_adapter import RedisCacheAdapter
    adapter = RedisCacheAdapter.get_instance()
    if adapter:
        adapter.reconnect()
    logger.info("Worker %s: Redis connections reset", worker.pid)


def _reset_kafka(worker):
    """Kafka producer 재설정.

    R1-Fork-Safety: fork() 이후 자식 프로세스에서는
    librdkafka 백그라운드 스레드가 복제되지 않으므로
    기존 인스턴스의 close()/flush()를 호출하면 Deadlock이 발생한다.
    참조만 해제하고 OS의 자원 회수에 맡긴다.
    """
    settings = get_settings()
    if not settings.kafka_producer.bootstrap_servers:
        logger.debug("Worker %s: Kafka not configured, skipping reset", worker.pid)
        return
    from selfhealing.adapters.kafka.producer import reset_kafka_producer_after_fork
    reset_kafka_producer_after_fork()
    logger.info("Worker %s: Kafka producer reset (fork-safe)", worker.pid)


def _reset_otel(worker):
    """OpenTelemetry 상태 무효화.

    Reset(글로벌 참조 해제) → 다음 사용 시 Lazy Reinitialize 2단계 패턴.
    마스터의 gRPC 채널/스레드 참조를 버리고, 워커에서 새 Exporter로 재생성한다.
    """
    from selfhealing.observability import reset_opentelemetry
    reset_opentelemetry()
    logger.info("Worker %s: OpenTelemetry reset", worker.pid)


def _reset_mmap(worker):
    """mmap CB 스냅샷 인스턴스 재설정.

    mmap FD 자체는 MAP_SHARED로 공유되지만,
    Python 래퍼 객체(Lock, 데몬 스레드, Writer 플래그)는 fork-safe하지 않다.
    마스터의 Writer 인스턴스를 버리고, 워커에서 Reader로 재생성한다.
    """
    from selfhealing.adapters.ipc import reset_cb_state_snapshot
    reset_cb_state_snapshot()
    logger.info("Worker %s: mmap descriptors reset", worker.pid)


def _reseed_rng(worker):
    import random
    random.seed()
    logger.info("Worker %s: RNG reseeded", worker.pid)
```

### 2.1.1 Kafka Producer fork-safe 리셋 (신규 함수)

`adapters/kafka/producer.py`에 fork 전용 리셋 함수를 추가한다.
기존 `reset_kafka_producer()`는 테스트용으로 유지하되, fork 이후에는 반드시 이 함수를 사용한다.

```python
# adapters/kafka/producer.py — 추가

def reset_kafka_producer_after_fork() -> None:
    """Post-fork Producer 리셋.

    Fork 이후 librdkafka의 백그라운드 스레드는 자식 프로세스로 복제되지 않는다.
    기존 인스턴스에 close()/flush()를 호출하면 이미 죽은 스레드에 대해
    join을 시도하여 Deadlock 또는 Segfault가 발생한다.

    따라서 참조만 해제하고, 다음 get_kafka_producer() 호출 시
    새 인스턴스가 생성되도록 한다. 마스터의 Producer 자원은
    마스터 프로세스 종료 시 OS가 회수한다.
    """
    global _producer
    with _producer_lock:
        _producer = None  # close() 없이 참조만 해제
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

## 3. GIL 경합 방지 — Sidecar Process + GIL Metrics

### 3.1 문제: 워커 내 CPU 바운드 백그라운드 스레드

Gunicorn `gthread` 모델에서 워커당 6개의 백그라운드 데몬 스레드가 동작한다.
이 중 2개가 CPU 바운드 작업을 수행하여 요청 처리 스레드와 GIL 경합을 유발한다.

| # | 서비스 | 주기 | 작업 유형 | GIL 영향 |
|---|--------|------|-----------|---------|
| 1 | Gauge Hydration | 0-60s (1회) | I/O (Redis) | Low |
| 2 | Precomputed Cache | 15s | I/O (Redis) | Moderate |
| 3 | **System Metrics Cache** | **1s** | **CPU (psutil)** | **High** |
| 4 | Meta-Watchdog | 10s | I/O (health probes) | Low |
| 5 | **Correlation Engine** | **5s** | **CPU (DAG/ML)** | **High** |
| 6 | Capacity Reservation | 1s | I/O (calendar) | Low |

**High GIL Impact 스레드**:
- `SystemMetricsCache`: `psutil.cpu_percent()`가 매 1초 `/proc/stat` 파싱 + 부동소수점 연산
- `CorrelationEngineService`: DAG 구성 + 상관 행렬 계산 + ML 추론

나머지 4개는 I/O 바운드로 GIL을 자주 해제하므로 경합이 미미하다.

### 3.2 해결: CPU 바운드 2개를 Sidecar Process로 분리

Sidecar만이 GIL을 **근본적으로 제거**한다.
Adaptive Interval, Thread Budget, Bulkhead Consolidation은 Sidecar 적용 시 불필요하다.

```
K8s Pod
┌──────────────────────────────────────────────────────┐
│                                                      │
│  Container 1: gunicorn-worker (요청 처리 전담)         │
│  ├── 요청 스레드 풀 (gthread)                          │
│  ├── Gauge Hydration (I/O, daemon)                   │
│  ├── Precomputed Cache (I/O, daemon)                 │
│  ├── Meta-Watchdog (I/O, daemon)                     │
│  ├── Capacity Reservation (I/O, daemon)              │
│  └── mmap reader (/dev/shm/selfhealing_metrics)      │
│                                                      │
│  Container 2: selfhealing-sidecar (CPU 작업 전담)      │
│  ├── System Metrics → mmap writer                    │
│  ├── Correlation Engine → Redis pub/sub 결과 전달      │
│  └── Health endpoint :9091/healthz                   │
│                                                      │
│  Volume: emptyDir (tmpfs) → /dev/shm/selfhealing_*   │
└──────────────────────────────────────────────────────┘
```

**IPC 방식**: 기존 `CBStateSnapshot`(`adapters/ipc/cb_state_snapshot.py`)의 mmap 패턴을 재사용한다.
System Metrics 결과를 mmap에 쓰고, 워커의 `SystemMetricsCache._do_refresh()`가 psutil 대신 mmap에서 읽도록 변경한다.

**K8s manifest 변경**:
- sidecar container 1개 추가
- `emptyDir` (medium: Memory) volume 공유
- sidecar에 별도 리소스 제한 (`cpu: 100m`, `memory: 128Mi`)

**워커 코드 변경**:
```python
# services/system_metrics_cache.py — 변경
def _do_refresh(self):
    if self._sidecar_enabled:
        # mmap에서 읽기 (~10μs, GIL 점유 최소)
        metrics = self._read_from_shared_memory()
    else:
        # Fallback: 직접 psutil 호출 (sidecar 미배포 환경)
        metrics = self._sample_psutil()
    self._cached = metrics
```

### 3.3 GIL Contention Metrics — Meta-Watchdog probe 추가

Sidecar 효과를 검증하고, 향후 새 백그라운드 스레드 추가 시 조기 경보를 제공한다.
기존 Meta-Watchdog(`meta/watchdog.py`)의 5개 probe(CB, DLQ, Redis, Recovery, Audit)에
`GILContentionProbe` 1개를 추가한다.

```python
# meta/probes/gil_contention.py (신규)
import time

from selfhealing.meta.watchdog import HealthStatus


class GILContentionProbe:
    """GIL 경합을 스케줄링 지연으로 간접 측정.

    time.sleep(0)으로 GIL을 양보한 뒤 되돌아오는 시간을 10회 측정하여
    P90 지연을 계산한다. 정상 환경에서는 < 0.1ms,
    심한 GIL 경합 시 수 ms까지 증가한다.

    임계값:
    - HEALTHY: P90 < 1ms
    - DEGRADED: 1ms ≤ P90 < 5ms
    - UNHEALTHY: P90 ≥ 5ms
    """

    DEGRADED_THRESHOLD_MS = 1.0
    UNHEALTHY_THRESHOLD_MS = 5.0

    def check(self) -> tuple[HealthStatus, dict]:
        delays_ns = []
        for _ in range(10):
            t0 = time.perf_counter_ns()
            time.sleep(0)
            delays_ns.append(time.perf_counter_ns() - t0)

        delays_ns.sort()
        p90_ms = delays_ns[8] / 1_000_000

        details = {
            "p90_ms": round(p90_ms, 3),
            "min_ms": round(delays_ns[0] / 1_000_000, 3),
            "max_ms": round(delays_ns[-1] / 1_000_000, 3),
        }

        if p90_ms >= self.UNHEALTHY_THRESHOLD_MS:
            return HealthStatus.UNHEALTHY, details
        elif p90_ms >= self.DEGRADED_THRESHOLD_MS:
            return HealthStatus.DEGRADED, details
        return HealthStatus.HEALTHY, details
```

**Prometheus 메트릭 노출**:
```python
# Gauge 추가 (metrics/prometheus.py)
selfhealing_gil_contention_p90_ms = Gauge(
    "selfhealing_gil_contention_p90_ms",
    "GIL contention P90 latency in milliseconds",
)
```

---

## 4. 설계 결정 기록 (R1)

### 4.1 Silent Failure 제거

| 변경 전 | 변경 후 | 근거 |
|---------|---------|------|
| 내부 모듈을 `try/except ImportError: pass`로 감쌈 | settings 플래그로 명시적 분기 (예: `kafka_producer.bootstrap_servers` 확인) | `server.py`는 selfhealing 패키지 내부이므로, 내부 모듈의 ImportError는 환경 깨짐을 의미한다. 조용히 넘기면 리셋 실패와 로직 버그를 구분할 수 없다 |
| `post_worker_init_start()`에서 `logger.debug` | `logger.warning`으로 변경 | 패키지 미설치는 비정상 상황이므로 warning이 적절 |

### 4.2 Kafka Fork-Safety

| 변경 전 | 변경 후 | 근거 |
|---------|---------|------|
| `reset_kafka_producer()` → `_producer.close()` 호출 | `reset_kafka_producer_after_fork()` → 참조만 해제 (`_producer = None`) | librdkafka 백그라운드 스레드는 fork 시 복제되지 않는다. 자식에서 `close()`를 호출하면 죽은 스레드에 join을 시도하여 Deadlock/Segfault 발생. 마스터의 자원은 OS가 회수 |

### 4.3 Time Budget 패턴

| 변경 전 | 변경 후 | 근거 |
|---------|---------|------|
| 각 종료 단계가 독립 실행, 총합 미관리 | `deadline = now + (gunicorn_timeout - 10s)` 예산 기반 동적 할당 | 개별 타임아웃의 최악 총합 (5s + 30s + α)이 Gunicorn timeout(60s)을 초과하면 SIGKILL로 체크포인트 미저장 → 중복 재생 발생 |
| 예산 초과 시 대응 없음 | Emergency Dump → 로컬 디스크에 최소 상태 기록 | SIGKILL 전에 최소한의 상태를 남겨 다음 부팅 시 복구 가능 |

### 4.4 Background Thread Graceful Stop

| 변경 전 | 변경 후 | 근거 |
|---------|---------|------|
| `worker_exit_cleanup()`에서 daemon 스레드 정리 없음 | `_stop_background_threads()` 단계 추가 (timeout=5s) | daemon 스레드는 프로세스 종료 시 인터럽트 없이 즉사한다. 실행 중이던 배치/메트릭 푸시가 유실될 수 있으므로, 종료 전 우아한 stop을 기다린다 |

### 4.5 GIL 경합 방지

| 변경 전 | 변경 후 | 근거 |
|---------|---------|------|
| CPU 바운드 스레드 2개가 워커 내부에서 GIL 경합 | Sidecar Process로 CPU 작업 분리 (System Metrics, Correlation Engine) | 완화책(Adaptive Interval 등)은 GIL 안에서의 최적화이지 제거가 아니다. Sidecar만이 근본 해결이며, 적용 시 나머지 완화책은 불필요해진다 |
| GIL 경합 관찰 수단 없음 | Meta-Watchdog에 GILContentionProbe 추가 | Sidecar 효과 검증 + 새 스레드 추가 시 조기 경보 |

---

## 5. 테스트 계획

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | `post_fork_reset()` 호출 | Redis/Kafka/OTEL/mmap/RNG 리셋 함수 모두 호출 확인 |
| 2 | `post_worker_init_start()` | background threads 시작 확인 |
| 3 | `worker_exit_cleanup()` | background stop → leader elector → audit system 순서 확인 |
| 4 | `_reset_kafka()` — Kafka 미설정 | `bootstrap_servers` 비어 있으면 skip 확인 |
| 5 | `_reset_kafka()` — fork-safe | `reset_kafka_producer_after_fork()`가 `close()` 없이 참조만 해제하는지 확인 |
| 6 | Time Budget 초과 | 종료 예산 소진 시 `_emergency_dump()` 호출 확인 |
| 7 | Time Budget 정상 | 각 단계에 남은 예산이 올바르게 전달되는지 확인 |
| 8 | Emergency Dump 파일 생성 | JSON 파일이 지정 경로에 올바른 형식으로 생성되는지 확인 |
| 9 | GILContentionProbe | P90 지연 측정 + HEALTHY/DEGRADED/UNHEALTHY 분류 확인 |
| 10 | Sidecar mmap 읽기 | `_sidecar_enabled=True` 시 psutil 대신 mmap에서 메트릭 읽기 확인 |
