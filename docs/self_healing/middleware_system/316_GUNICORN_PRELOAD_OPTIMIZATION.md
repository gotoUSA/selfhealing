# 316. Gunicorn Preload Optimization — 프로세스 모델 기반 Cold Start 최적화

> **Status**: Implemented (Phase 1–8)
> **Severity**: P2 (MEDIUM) — fork-safety 누락은 데이터 손상 위험
> **Target**: 배포 설정 (docker-compose.yml, k8s/, gunicorn.conf.py)
> **References**:
> - 313 — Settings & Configuration Consistency
> - 315 — Settings SSOT Migration
> - 220 — System Metrics Cache Layer (preload 관련 기존 논의)

---

## 1. 현황 및 문제

### 1.1 현재 배포 구성

| 컴포넌트 | 구성 | 코드 위치 |
|---------|------|----------|
| Django API | `gunicorn --workers=4 --threads=4 --worker-class=gthread` | `docker-compose.yml:38` |
| K8s | `gunicorn --workers=4` (preload 미사용) | `k8s/django-api-deployment.yaml:71-74` |
| Celery Worker | `--concurrency=8`, 6개 큐 | `docker-compose.yml:107` |
| Celery Beat | 단일 프로세스 | `docker-compose.yml:143` |

### 1.2 문제: Worker별 독립 초기화

현재 `--preload` 미사용 상태이므로 각 Gunicorn worker가 앱을 독립적으로 초기화한다:

```
Master process → fork() → Worker 1 → Django init + Settings init
                       → Worker 2 → Django init + Settings init
                       → Worker 3 → Django init + Settings init
                       → Worker 4 → Django init + Settings init
```

**비용**: Settings 초기화 × 4회 + Django ORM/middleware 초기화 × 4회

### 1.3 Preload 적용 시

```
Master process → Django init + Settings init → fork() → Worker 1 (CoW 메모리 공유)
                                                      → Worker 2 (CoW 메모리 공유)
                                                      → Worker 3 (CoW 메모리 공유)
                                                      → Worker 4 (CoW 메모리 공유)
```

**비용**: Settings 초기화 × 1회 + Django ORM/middleware 초기화 × 1회

---

## 2. 패턴 B vs D 비교 (315와의 관계)

315(SharedEnvSource)와 316(Gunicorn Preload)은 서로 다른 레이어의 문제를 해결한다:

| 기준 | 315: SharedEnvSource | 316: Gunicorn Preload |
|------|---------------------|----------------------|
| 해결 레이어 | Settings 내부 (I/O 최적화) | 프로세스 모델 (fork 최적화) |
| 적용 범위 | Gunicorn + Celery + 테스트 | **Gunicorn만** |
| Celery 효과 | O | X (별도 프로세스 모델) |
| 테스트 효과 | O | X (테스트에 gunicorn 없음) |
| 메모리 효율 | 변화 없음 | CoW로 메모리 공유 |
| 코드 변경량 | 108개 settings 수정 | 설정 파일 2-3줄 |
| Hot reload 영향 | 없음 | `--preload`와 `--reload` 병용 불가 |
| DB connection | 영향 없음 | `post_fork` 훅 필요 |

**결론**: 315와 316은 상호 배타적이 아니며, **둘 다 적용 시 최대 효과**를 얻는다.

---

## 3. 개선 계획

### 3.1 Gunicorn 설정 파일 생성

```python
# gunicorn.conf.py (신규)
import multiprocessing

# Worker 설정
workers = 4
worker_class = "gthread"
threads = 4
timeout = 60

# Preload 활성화 — Master에서 앱 초기화 후 Worker fork
preload_app = True

# Access/Error 로그
accesslog = "-"
errorlog = "-"

# Bind
bind = "0.0.0.0:8000"


def post_fork(server, worker):
    """Worker fork 후 DB connection 재초기화.

    Master에서 열린 DB connection이 fork 후 공유되면
    connection pool 충돌이 발생한다.
    post_fork에서 모든 connection을 닫아 Worker가
    독립적으로 새 connection을 생성하도록 한다.
    """
    from django.db import connections

    for conn in connections.all():
        conn.close()


def post_worker_init(worker):
    """Worker 초기화 완료 후 로깅."""
    worker.log.info(f"Worker {worker.pid} initialized (preload mode)")
```

### 3.2 docker-compose.yml 변경

```yaml
# BEFORE
command: >
  sh -c "python manage.py migrate --noinput &&
         python manage.py collectstatic --noinput &&
         gunicorn myproject.wsgi:application --bind 0.0.0.0:8000 --workers 4 --threads 4 --timeout 60 --worker-class gthread --access-logfile - --error-logfile -"

# AFTER
command: >
  sh -c "python manage.py migrate --noinput &&
         python manage.py collectstatic --noinput &&
         gunicorn myproject.wsgi:application -c gunicorn.conf.py"
```

### 3.3 K8s Deployment 변경

```yaml
# k8s/django-api-deployment.yaml
command:
  - gunicorn
  - myproject.wsgi:application
  - -c
  - gunicorn.conf.py
```

### 3.4 wsgi.py에서 Settings 선행 초기화 (선택적)

```python
# myproject/wsgi.py
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

application = get_wsgi_application()

# Preload 모드에서 Master가 settings를 미리 초기화
# Worker fork 시 CoW로 메모리 공유
from selfhealing.settings import get_config
get_config()
```

---

## 4. 주의 사항

### 4.1 Hot Reload 비활성화

`--preload`와 `--reload`는 함께 사용할 수 없다.

| 환경 | preload | reload | 비고 |
|------|---------|--------|------|
| 개발 (로컬) | OFF | ON | 코드 변경 시 자동 재시작 |
| 스테이징 | ON | OFF | 프로덕션과 동일 구성 |
| 프로덕션 | ON | OFF | Cold Start 최적화 |

**구현**: `gunicorn.conf.py`에서 환경변수로 분기.

```python
import os

env = os.environ.get("DEPLOYMENT_ENV", "development")

if env == "development":
    preload_app = False
    reload = True
else:
    preload_app = True
    reload = False
```

### 4.2 DB Connection Pool 관리

`--preload` 사용 시 Master에서 생성된 DB connection이 fork 후 Worker에 복제된다.
이 connection은 **동일한 소켓 FD를 공유**하므로 데이터 손상이 발생할 수 있다.

**해결**: `post_fork` 훅에서 모든 connection을 닫는다 (3.1절 참조).

### 4.3 Signal Handling

`--preload` 모드에서 graceful shutdown은 다음 순서로 동작한다:

1. Master가 SIGTERM 수신
2. Master가 각 Worker에 SIGTERM 전송
3. Worker가 현재 요청 처리 완료 후 종료
4. Master 종료

현재 프로젝트의 Graceful Shutdown 코드 (`core/shutdown_coordinator.py`)와의
호환성 확인이 필요하다.

### 4.4 Celery에는 효과 없음

Celery는 자체 프로세스 모델(`prefork`, `eventlet`, `gevent`)을 사용하므로
Gunicorn `--preload`의 혜택을 받지 않는다.

Celery의 Cold Start 최적화는 별도 접근이 필요하다:
- `celery worker --pool=prefork`는 자체 fork를 수행
- Settings 최적화는 315(SharedEnvSource)로 대응

---

## 5. Fork-Safety 심층 분석

코드 리뷰를 통해 `--preload` 적용 시 발생하는 fork-safety 문제를 7개 영역으로 분류한다.

### 5.1 외부 네트워크 커넥션 — 소켓 FD 공유 위험

`post_fork`에서 Django DB 외의 모든 외부 커넥션(소켓 FD)도 리셋해야 한다.
Master에서 열린 소켓이 fork 후 Worker에 복제되면, 동일한 FD를 여러 프로세스가
공유하여 패킷 충돌 및 데이터 오염이 발생한다.

**현재 코드에 리셋 메서드는 존재하지만 post_fork 통합이 없다:**

| 커넥션 | 라이브러리 | 전역 관리 방식 | 리셋 메서드 | post_fork 호출 |
|--------|-----------|---------------|-----------|:-----------:|
| Redis | `redis` + `django-redis` | Multi-strategy fallback (4단계) | `reconnect()` → `connection_pool.disconnect()` | X |
| Kafka Producer | `confluent-kafka` | Thread-locked singleton (`_producer_lock`) | `reset_kafka_producer()` → `close()` + `None` | X |
| Kafka Consumer | `confluent-kafka` | 인스턴스별 관리 | `close()` | X |
| OTEL (gRPC) | `opentelemetry-exporter-otlp` | 모듈 레벨 singleton | `reset_opentelemetry()` → 전체 state 초기화 | X |
| gRPC Sidecar | `grpcio` | 인스턴스별 (lazy init) | `socket.close()` | X |
| etcd | `etcd3` | 리소스별 인스턴스 | N/A | X |
| mmap (CB State) | `mmap` | singleton | `_close_shm()` → `mmap.close()` | X |

**코드 위치:**
- Redis reconnect: `adapters/cache/redis_adapter.py:708-726`
- Kafka reset: `adapters/kafka/producer.py:417-428`
- OTEL reset: `observability/__init__.py:284-299`
- mmap close: `adapters/ipc/cb_state_snapshot.py:327-335`

**Kafka Producer 싱글톤이 특히 위험하다.** Thread-lock 기반 싱글톤(`_producer_lock`)이
fork 후 복제되면 deadlock이나 패킷 오염이 발생할 수 있다.

**mmap FD**는 fork 후 invalid FD가 되어 `SIGBUS` 크래시를 유발할 수 있다.

### 5.2 AppConfig.ready() 스레드 오펀 — fork 후 백그라운드 스레드 사망

`--preload` 모드에서 `AppConfig.ready()`는 Master 프로세스에서 1회 실행된다.
OS 레벨에서 `fork()`는 호출한 메인 스레드만 복사하므로, Master에서 생성된
`threading.Thread`나 `Timer`는 Worker 프로세스에 상속되지 않는다.

**SelfHealingConfig.ready()에서 생성하는 4개 백그라운드 스레드/타이머:**

| 컴포넌트 | 타입 | 코드 위치 | daemon | 접근 리소스 | 미작동 시 영향 |
|---------|------|----------|:------:|-----------|-------------|
| Gauge Hydration | `Timer` | `adapters/django/apps.py:496-498` | O | Redis, DB | Prometheus 게이지 0/missing (60초간) |
| Precomputed Cache | `Timer` (재귀) | `services/precomputed_cache/worker.py:78-80` | O | L1/L2 Cache, DB | L3 엔드포인트 P95: 7ms → 100ms+ |
| System Metrics | `Timer` (재귀) | `services/system_metrics_cache.py:177-179` | O | psutil | 웹 스레드에서 100ms 블로킹 |
| **Meta-Watchdog** | **Thread** | `meta/watchdog.py:737-742` | O | Redis, DB, Slack/PagerDuty | **치명적: 자동 복구 불가, 에스컬레이션 미발송** |

**포크 가드 존재 여부:** `os.getpid()`, `SERVER_SOFTWARE`, `post_worker_init`,
`is_gunicorn_master()` — **전부 없음.**

**Meta-Watchdog이 가장 위험하다.** 이 스레드가 fork 후 사라지면:
- Stuck Circuit Breaker 감지 불가
- PagerDuty/Slack 알림 미발송
- Recovery audit trail 미기록 → GDPR/SOC2 컴플라이언스 위반

**해결 전략:**

`ready()`에서 앱 초기화만 수행하고, 스레드를 띄우는 `_start_*` 계열 메서드들은
Gunicorn의 `post_worker_init` 훅에서 호출되도록 진입점을 분리한다.

```python
# gunicorn.conf.py
def post_worker_init(worker):
    """Worker별 백그라운드 스레드 시작."""
    os.environ["GUNICORN_WORKER"] = "1"
    from selfhealing.adapters.django.apps import SelfHealingConfig
    app_config = SelfHealingConfig.create_current("selfhealing")
    app_config.start_background_threads()
    worker.log.info(f"Worker {worker.pid}: background threads started")
```

로컬 개발 환경(`manage.py runserver`)에서는 `ready()` 내부에서 직접 실행:

```python
# adapters/django/apps.py
import os

_is_managed_worker = os.environ.get("GUNICORN_WORKER") == "1"
_is_dev_server = "runserver" in sys.argv or os.environ.get("DJANGO_DEV_SERVER") == "1"

if _is_managed_worker or _is_dev_server:
    self._start_background_threads()
```

### 5.3 Graceful Shutdown 시그널 충돌 — 6곳에서 12개 핸들러 덮어쓰기

Gunicorn 환경에서 시그널 통제권은 Gunicorn Master(Arbiter)에 있다.
Worker 내부의 Django 애플리케이션이 `signal.SIGTERM`을 직접 가로채면
Gunicorn의 프로세스 라이프사이클 관리와 충돌한다.

**현재 코드베이스의 시그널 핸들러 등록 현황:**

| 파일 | 시그널 | 체이닝 | Gunicorn 감지 |
|------|--------|:------:|:-----------:|
| `core/shutdown_coordinator.py:266-267` | SIGTERM, SIGINT | X (덮어쓰기) | X |
| `coordination/shutdown_integration.py:77-80` | SIGTERM, SIGINT | X (덮어쓰기) | Win32만 |
| `audit/async_audit_lifecycle.py:457-460` | SIGTERM, SIGINT | X (덮어쓰기) | main_thread만 |
| `audit/persistence/disk_buffer.py:1311-1312` | SIGTERM, SIGINT | **O** (원본 호출) | X |
| `adapters/audit/redis_buffer.py:845-847` | SIGTERM, SIGINT | X (덮어쓰기) | X |
| `sidecar/entrypoint.py:134-135` | SIGTERM, SIGINT | X (덮어쓰기) | X (별도 프로세스) |

**6개 중 5개가 체이닝 없이 덮어쓰기.** 마지막 등록 핸들러만 실행되고 나머지는 무시된다.

**해결 전략:**

1. Gunicorn 환경 감지 시(`SERVER_SOFTWARE` 환경변수 확인) `signal.signal()` 등록을 건너뛴다.
2. 대신 `worker_exit` 훅에서 `GracefulShutdownCoordinator.initiate_shutdown()`을 수동 호출한다.
3. 장기적으로 Signal Dispatcher 패턴을 도입하여 단일 핸들러에서 등록된 콜백을 순서대로 호출한다.

```python
# gunicorn.conf.py
def worker_exit(server, worker):
    """Worker 종료 시 graceful shutdown 파이프라인 실행."""
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

    worker.log.info(f"Worker {worker.pid}: graceful shutdown completed")
```

### 5.4 Prometheus 멀티프로세스 환경 — 메트릭 파편화

Gunicorn Worker 간 메모리 격리로 인해 Prometheus 메트릭이 파편화된다.
각 Worker의 메트릭이 독립적인 메모리 공간에 존재하므로, `/metrics` 엔드포인트가
요청을 받는 Worker의 메트릭만 반환한다.

**현재 상태:**

| 항목 | 상태 | 영향 |
|------|:----:|------|
| 총 메트릭 인스턴스 | 168개 (Counter 93 / Gauge 53 / Histogram 22) | — |
| Gauge `multiprocess_mode` 설정 | **0개** (53개 Gauge 전부 미설정) | Worker 간 집계 불가 |
| `MultiProcessCollector` | **미사용** | 공유 메모리 파일 미생성 |
| `PROMETHEUS_MULTIPROC_DIR` | **미설정** | mmap 기반 집계 불가 |
| Prometheus text exposition 엔드포인트 | **없음** | K8s ServiceMonitor 스크래핑 불가 |
| `REGISTRY._names_to_collectors` 직접 접근 | 9개 파일 | 멀티프로세스 모드 비호환 |

`SelfHealingMetricsView`(`api/django/views/health.py:187-203`)는 비즈니스 메트릭을
JSON으로 반환하며, `prometheus_client.generate_latest()`를 호출하지 않는다.

**OTEL Metrics 전환으로 근본 해결 (5.8절 참조).**

### 5.5 post_fork 커넥션 리셋 — Fail-fast 전략

`post_fork`에서 각 커넥션을 리셋할 때, 반드시 `try-except` 블록으로 감싸고
실패 시 `sys.exit(1)`을 호출하여 Worker를 강제 종료시킨다.

연결 풀이 꼬인 상태로 Worker가 트래픽을 받게 두는 것(Zombie 상태)보다
빠르고 명시적으로 죽여서 Gunicorn Master가 새 Worker를 띄우게 하는 것(Fail-fast)이
안전하다.

```python
# gunicorn.conf.py — 완전한 post_fork 구현
import sys
import logging

logger = logging.getLogger("gunicorn.error")


def post_fork(server, worker):
    """Worker fork 후 모든 외부 커넥션 재초기화 (Fail-fast)."""
    try:
        _reset_db_connections(worker)
        _reset_redis_connections(worker)
        _reset_kafka_producer(worker)
        _reset_opentelemetry(worker)
        _reset_mmap_descriptors(worker)
        _reseed_rng(worker)
    except Exception as e:
        logger.error(f"Worker {worker.pid}: post_fork failed: {e}")
        sys.exit(1)


def _reset_db_connections(worker):
    """Django DB 커넥션 리셋."""
    from django.db import connections
    for conn in connections.all():
        conn.close()
    logger.info(f"Worker {worker.pid}: DB connections reset")


def _reset_redis_connections(worker):
    """Redis 커넥션 풀 리셋."""
    try:
        from selfhealing.adapters.cache.redis_adapter import RedisCacheAdapter
        adapter = RedisCacheAdapter.get_instance()
        if adapter:
            adapter.reconnect()
    except ImportError:
        pass
    logger.info(f"Worker {worker.pid}: Redis connections reset")


def _reset_kafka_producer(worker):
    """Kafka Producer 싱글톤 리셋."""
    try:
        from selfhealing.adapters.kafka.producer import reset_kafka_producer
        reset_kafka_producer()
    except ImportError:
        pass
    logger.info(f"Worker {worker.pid}: Kafka producer reset")


def _reset_opentelemetry(worker):
    """OTEL gRPC exporter 리셋."""
    try:
        from selfhealing.observability import reset_opentelemetry
        reset_opentelemetry()
    except ImportError:
        pass
    logger.info(f"Worker {worker.pid}: OpenTelemetry reset")


def _reset_mmap_descriptors(worker):
    """mmap 파일 디스크립터 리셋."""
    try:
        from selfhealing.adapters.ipc import reset_cb_state_snapshot
        reset_cb_state_snapshot()
    except ImportError:
        pass
    logger.info(f"Worker {worker.pid}: mmap descriptors reset")


def _reseed_rng(worker):
    """난수 생성기 리시드 (5.6절 참조)."""
    import random
    random.seed()
    logger.info(f"Worker {worker.pid}: RNG reseeded")
```

### 5.6 난수 생성기(RNG) 시드 초기화

Master에서 초기화된 `random` 모듈의 Mersenne Twister PRNG 시드가
Worker로 그대로 복사되면, 모든 Worker가 동일한 난수 시퀀스를 생성한다.

**참고:** `uuid.uuid4()`는 내부적으로 `os.urandom(16)`을 호출하여 커널 엔트로피 풀
(`/dev/urandom`)에서 직접 읽으므로 fork 후에도 안전하다. 리시드 불필요.

**`random` 모듈 사용처 — 영향받는 코드:**

| 위험도 | 사용처 | 영향 |
|:------:|--------|------|
| HIGH | `core/backoff.py:87,141,190,238,359` — `random.uniform()` jitter | 4개 Worker 동일 jitter → Thundering Herd 발생 |
| HIGH | `services/circuit_breaker/load_shedding/manager.py:293` — `random.random()` | Worker 간 동일 shed 패턴 → 트래픽 분산 실패 |
| HIGH | `services/canary/feature_flag.py:601` — `random.uniform(0, 100)` | 카나리 트래픽 10% 의도가 100%로 변질 |
| MEDIUM | `api/django/tiering/middleware.py:192` — `random.random()` | 동일 요청 드롭 패턴 |
| MEDIUM | `audit/performance/sampling.py:127` — `random.sample()` | 동일 샘플 선택 |

**해결:** `post_fork`에서 `random.seed()` 한 줄이면 충분하다.
인자 없는 `random.seed()`는 내부적으로 `os.urandom`을 사용하여 시스템 엔트로피 기반으로 리시드한다.

```python
import random
random.seed()  # os.urandom 기반 자동 리시드
```

### 5.7 Sidecar 잔존물 정리 — Library 전환 후 불필요 코드

시스템이 Sidecar에서 Library로 전환됨에 따라, `--preload` 최적화 적용 전
불필요한 IPC 인프라를 정리하여 fork 시 복제되는 메모리와 FD를 줄인다.

**SIDECAR-ONLY — 제거 가능 (12개 아티팩트, ~2,500줄):**

| # | 파일 | 줄 수 | 용도 |
|---|------|:-----:|------|
| 1 | `sidecar/entrypoint.py` | 263 | 독립 sidecar 프로세스 오케스트레이터 |
| 2 | `adapters/ipc/grpc_server.py` | ~400 | gRPC 서버 (sidecar 모드 전용) |
| 3 | `adapters/ipc/uds_server.py` | 809 | UDS 서버 (JSON-RPC 2.0) |
| 4 | `adapters/ipc/event_stream_proxy.py` | ~100 | EventBus → gRPC 스트리밍 브릿지 |
| 5 | `adapters/ipc/auth.py` | ~80 | Static Bearer Token 인증 |
| 6 | `adapters/ipc/sidecar_ipc_probe.py` | ~100 | Meta-Watchdog IPC 헬스 프로브 |
| 7 | `adapters/ipc/sidecar_metrics.py` | ~80 | Sidecar 전용 Prometheus 메트릭 16개 |
| 8 | `adapters/ipc/protocol/json_rpc.py` | ~100 | JSON-RPC 2.0 프로토콜 구현 |
| 9 | `adapters/ipc/protocol/selfhealing.proto` | 391 | gRPC proto 정의 (6개 서비스) |
| 10 | `Dockerfile.sidecar` | ~40 | Sidecar 컨테이너 이미지 |
| 11 | `k8s/sidecar-injection.yaml` | ~100 | K8s sidecar 주입 매니페스트 |
| 12 | 설계 문서 183~186 | 4문서 | Sidecar 아키텍처 문서 |

**테스트 파일도 함께 제거 가능 (~160개 테스트):**
`test_grpc_server.py`, `test_uds_server.py`, `test_sidecar_ipc_probe.py`,
`test_sidecar_metrics.py`, `test_event_stream_proxy.py`, `test_auth.py`, `test_json_rpc.py`

**DUAL-USE — 유지 (라이브러리에서도 가치 있음):**

| 파일 | 용도 | 유지 사유 |
|------|------|----------|
| `adapters/ipc/cb_state_snapshot.py` | mmap 기반 CB 상태 (sub-10μs) | 고성능 로컬 캐시 |
| `adapters/ipc/cb_state_cache.py` | TTL 기반 CB 상태 캐시 | Redis hit 감소 |
| `adapters/ipc/request_handler.py` | 서비스 라우팅 추상화 | 재사용 가능 |
| `adapters/ipc/exceptions.py` | IPC 예외 계층 | 에러 코드 체계 |

**참고:** `cb_state_snapshot.py`의 mmap은 fork 후 FD가 깨지므로, 유지 시
`post_fork`에서 `reset_cb_state_snapshot()`을 반드시 호출해야 한다 (5.5절 참조).

**아키텍처 안전성:** Sidecar 코드는 `adapters/ipc/` 하위에 깔끔하게 격리되어 있다.
Core 서비스는 `ProviderRegistry`(`factory.py`)를 통해 어댑터를 동적 등록하므로,
`ipc/` 어댑터를 제거해도 다른 어댑터에 영향 없다.

### 5.8 OTEL Metrics 전환 — Prometheus 멀티프로세스 근본 해결

개발 단계인 현재가 전환 최적 시점이다. 프로덕션 이후 전환 시 비용이 3~5배 증가한다.

**OTEL Metrics로 전환하면 5.4절의 문제가 근본적으로 해결된다:**

- `PROMETHEUS_MULTIPROC_DIR` 설정 불필요 (OTEL이 자체 처리)
- `child_exit` 훅에서 `mark_process_dead()` 호출 불필요
- Gauge `multiprocess_mode` 파라미터 53개 추가 불필요
- `REGISTRY._names_to_collectors` private API 접근 제거

**현재 OTEL 인프라 상태:**

| 항목 | 상태 |
|------|:----:|
| `opentelemetry-api` / `opentelemetry-sdk` | O (설치됨) |
| TracerProvider / OTLP Span Exporter | O (구현됨) |
| MeterProvider / OTEL Metrics SDK | **X (없음)** |
| `opentelemetry-exporter-prometheus` | **X (미설치)** |
| 메트릭 추상화 레이어 | **X (직접 호출)** |

**현재 메트릭 현황:**

| 항목 | 수치 |
|------|------|
| 총 메트릭 인스턴스 | 168개 (sidecar 16개 제거 시 152개) |
| 중앙 허브 (`metrics/prometheus.py`) | 47개 |
| 분산 모듈별 메트릭 | 121개 (65+ 파일) |
| 직접 `.inc()/.set()/.observe()` 호출 | ~42곳 |
| 영향받는 테스트 파일 | 11개 |

**`opentelemetry-exporter-prometheus`의 `PrometheusMetricReader`는 내부적으로
prometheus_client의 REGISTRY를 사용하므로**, 기존 Grafana 대시보드를 깨뜨리지 않으면서
점진 전환이 가능하다.

**전환 로드맵:**

| Phase | 작업 | 비용 | 비고 |
|-------|------|:----:|------|
| 0 | `MetricsSettings`에 `backend` 필드 추가, `pyproject.toml`에 `opentelemetry-exporter-prometheus` 추가, `observability/__init__.py`에 `MeterProvider` 초기화 | 낮음 | 316과 병행 |
| 1 | `SelfHealingMetrics` 클래스를 OTEL Meter 기반으로 전환 (47개), `PrometheusMetricReader`로 `/metrics` text exposition 추가 | 중간 | 316과 병행 |
| 2 | 분산된 모듈별 메트릭 점진 전환, `services/metrics/registry.py`의 `get_or_create_*`를 OTEL 대응, 11개 테스트 파일 업데이트 | 중간 | 이후 |
| 3 | `prometheus_client` 직접 참조 제거, `REGISTRY._names_to_collectors` 패턴 제거, `prometheus_client`를 optional dependency로 강등 | 낮음 | 정리 |

---

## 6. 검증 계획

### 6.1 Cold Start 측정

```bash
# Preload OFF (현재)
time gunicorn myproject.wsgi:application --workers=4 --worker-class=gthread

# Preload ON (변경 후)
time gunicorn myproject.wsgi:application -c gunicorn.conf.py
```

### 6.2 DB Connection 검증

```python
# 각 Worker에서 독립 connection 확인
def post_fork(server, worker):
    from django.db import connections
    for conn in connections.all():
        conn.close()
    # 검증: 새 connection 생성 확인
    from django.db import connection
    connection.ensure_connection()
    worker.log.info(f"Worker {worker.pid}: DB connection OK (pid-isolated)")
```

### 6.3 Graceful Shutdown 호환성

```bash
# Graceful shutdown 테스트
kill -SIGTERM <master_pid>
# Worker가 현재 요청 처리 완료 후 종료되는지 확인
```

### 6.4 외부 커넥션 격리 검증

```python
# 각 Worker에서 Redis/Kafka 독립 커넥션 확인
def post_worker_init(worker):
    import os
    from selfhealing.adapters.redis import get_redis_client
    client = get_redis_client()
    if client:
        assert client.ping(), "Redis connection failed"
    worker.log.info(f"Worker {worker.pid}: all external connections OK")
```

### 6.5 백그라운드 스레드 활성 검증

```python
# 각 Worker에서 백그라운드 스레드 작동 확인
def post_worker_init(worker):
    import threading
    thread_names = [t.name for t in threading.enumerate()]
    assert "SelfHealerWatchdog" in thread_names, "Meta-Watchdog not running"
    worker.log.info(f"Worker {worker.pid}: {len(thread_names)} threads active")
```

### 6.6 RNG 독립성 검증

```python
# 각 Worker에서 난수 시퀀스 독립 확인
def post_worker_init(worker):
    import random
    sample = [random.random() for _ in range(3)]
    worker.log.info(f"Worker {worker.pid}: RNG sample={sample}")
    # 각 Worker 로그를 비교하여 시퀀스가 다른지 확인
```

---

## 7. 구현 순서

| Phase | 작업 | 우선순위 | 의존성 |
|:-----:|------|:--------:|--------|
| 1 | `gunicorn.conf.py` 생성 (preload + post_fork + post_worker_init + worker_exit) | **P1** | 없음 |
| 2 | `post_fork`: 전체 커넥션 리셋 + Fail-fast (5.1, 5.5절) | **P1** | Phase 1 |
| 3 | `post_worker_init`: 백그라운드 스레드 재시작 (5.2절) | **P1** | Phase 1 |
| 4 | `post_fork`: `random.seed()` RNG 리시드 (5.6절) | **P2** | Phase 1 |
| 5 | `worker_exit`: GracefulShutdownCoordinator 연동 (5.3절) | **P2** | Phase 1 |
| 6 | Sidecar 잔존물 정리 — SIDECAR-ONLY 12개 아티팩트 제거 (5.7절) | **P2** | 없음 |
| 7 | OTEL Metrics Phase 0~1 — MeterProvider + 중앙 허브 전환 (5.8절) | **P1** | 없음 |
| 8 | Prometheus text exposition 엔드포인트 추가 (5.4, 5.8절) | **P1** | Phase 7 |
| 9 | `docker-compose.yml` 명령어 변경 | P3 | Phase 1 |
| 10 | `k8s/django-api-deployment.yaml` 명령어 변경 | P3 | Phase 1 |
| 11 | `wsgi.py`에 settings 선행 초기화 추가 (선택적) | P3 | 315 완료 후 |
| 12 | Cold Start 측정 + 전체 검증 (6절) | P3 | Phase 9 |
| 13 | OTEL Metrics Phase 2~3 — 분산 메트릭 전환 + 정리 (5.8절) | P3 | Phase 7 |

---

## 8. 엔터프라이즈 준비도 평가

### 단일 프로세스 vs 멀티 프로세스 비교

| 영역 | 단일 프로세스 | 멀티 프로세스 (Gunicorn 4W) | K8s 멀티 Pod |
|------|:-----------:|:-------------------------:|:----------:|
| 커넥션 관리 | 우수 | **FD 공유 위험** | FD 공유 위험 |
| 백그라운드 스레드 | 정상 동작 | **전부 사망** | 전부 사망 |
| Graceful Shutdown | 정상 동작 | **시그널 충돌** | 시그널 충돌 |
| Prometheus 메트릭 | 정확 | **파편화** | 파편화 |
| RNG 독립성 | 해당 없음 | **jitter 동기화** | jitter 동기화 |
| OTEL 트레이싱 | 정상 | **gRPC 채널 깨짐** | gRPC 채널 깨짐 |

### 빅테크 기준 비교

| 기준 | 현재 상태 | 필요 조치 | Phase |
|------|:--------:|----------|:-----:|
| Netflix Hystrix: Fork-safe CB | CB는 Redis 기반이라 OK, **Watchdog 사망** | post_worker_init에서 Watchdog 재시작 | 3 |
| Google SRE: Error Budget 정확성 | **Gauge 파편화** | OTEL Metrics 전환 | 7 |
| AWS Well-Architected: Failure Isolation | **FD 공유로 blast radius 확대** | post_fork 커넥션 격리 | 2 |
| CNCF OTEL: Distributed Tracing | **fork 후 gRPC exporter 깨짐** | OTEL reset in post_fork | 2 |
| Google SRE: Graceful Degradation | ResilientStorageBackend Memory+WAL 폴백 우수 | — | — |

---

## 9. 관련 문서

| 문서 | 관계 |
|------|------|
| 220 | System Metrics Cache Layer — `AppConfig.ready()`와 preload 상호작용 기존 논의 |
| 313 | Settings Configuration Consistency — 하드코딩 제거 (선행 완료) |
| 315 | Settings SSOT Migration — SharedEnvSource (같은 레이어가 아닌 보완 관계) |
| 183~186 | Sidecar Pattern — Library 전환으로 정리 대상 (5.7절) |

---

## 10. 변경 이력

| 날짜 | 버전 | 변경 내용 |
|------|------|----------|
| 2026-03-07 | 1.0.0 | 초안 작성 (313 Q2 리뷰에서 분리) |
| 2026-03-08 | 1.1.0 | Phase 6 실행: Sidecar-only 18개 파일 제거 (소스 10 + 테스트 8). OTEL API 정합성 8건 수정 (otel_backend.py ↔ prometheus.py 시그니처 일치). 리뷰 10건 반영: worker_exit 예외 처리, observability 로그 메시지, apps.py 조건문 단순화, set_info 설명 주석 |
| 2026-03-08 | 2.0.0 | Fork-safety 심층 분석 7개 영역 추가 (5절), OTEL Metrics 전환 계획 (5.8절), Sidecar 잔존물 정리 (5.7절), 엔터프라이즈 준비도 평가 (8절), 검증 계획 확장 (6절), 구현 순서 재편 (7절), Severity P3→P2 상향 |
