# 165. 엔터프라이즈 스케일 처리 - 개요

> **버전**: 1.0.0
> **작성일**: 2026-01-31
> **목적**: 대기업 환경에서 Pod당 초당 수만 건 처리를 위한 시스템 개선 마스터 플랜

---

## 1. 현재 시스템 한계점 분석 (코드 근거)

### 1.1 In-Memory 버퍼 제한

**파일**: `packages/selfhealing-python/src/selfhealing/audit/event_buffer.py`
**라인**: 305-340

```python
# 단일 요청당 최대 이벤트 수 (메모리 폭발 방지)
DEFAULT_MAX_EVENTS = 100

def add_event(self, event: AuditEvent) -> bool:
    if len(self.events) >= self._max_events:
        self._truncated_count += 1  # 초과 이벤트는 DROP
        self._mark_last_event_truncated()
        return False
    self.events.append(event)
    return True
```

**문제**: 요청당 100개 초과 이벤트는 **유실**됩니다.

---

### 1.2 Redis 동기 처리 병목

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/audit/redis_buffer.py`
**라인**: 126-128

```python
pipe = self._redis.pipeline()
pipe.lpush(key, json.dumps(payload, default=str))
pipe.expire(key, self._ttl_seconds)
pipe.execute()  # 동기 블로킹!
```

**문제**: 매 이벤트마다 Redis RTT 발생 (초당 수만 건이면 병목)

---

### 1.3 초당 이벤트 수 제한

**파일**: `packages/selfhealing-python/src/selfhealing/settings/cascade_retention.py`
**라인**: 123-128

```python
max_events_per_second: int = Field(
    default=1000,
    ge=100,
    le=10000,  # 최대 10,000/초
    description="초당 최대 이벤트 처리 수",
)
```

**문제**: 설정 최댓값이 10,000/초로 제한되어 있음

---

### 1.4 deque 고정 크기 한계

**파일**: `packages/selfhealing-python/src/selfhealing/services/throttle/time_bucketed_window.py`
**라인**: 7

```python
# - 기존 deque(maxlen=100)은 10k TPS에서 10ms 데이터만 담음
# - 고TPS 환경에서 샘플 부족으로 Gradient 계산 정확도 저하
```

**문제**: 코드 자체가 10k TPS 한계를 인식하고 있음

---

### 1.5 Celery Worker 고정 Concurrency

**파일**: `k8s/celery-critical-worker.yaml`
**라인**: 77-79

```yaml
# Concurrency 2 (동시 처리 2개)
- -c
- "2"
```

**문제**: Pod당 동시 2개 태스크만 처리. 수평 확장 자동화(HPA) 없음

---

## 2. 활용 가능한 기존 코드 패턴

### 2.1 RingBuffer (비침투 Back-Pressure)

**파일**: `packages/selfhealing-python/src/selfhealing/audit/ring_buffer.py`
**라인**: 40-79

```python
class RingBuffer(Generic[T]):
    """
    Thread-Safe Ring Buffer with Backpressure.
    Shadow Logging을 위한 비침투 버퍼.
    메인 애플리케이션을 절대 블로킹하지 않음.
    """
    def __init__(
        self,
        capacity: int = 10000,  # 최대 10만까지 설정 가능
        strategy: BackpressureStrategy = BackpressureStrategy.DROP_OLDEST,
    ):
```

**현재 상태**: Audit 이벤트에 **적용되지 않음**

---

### 2.2 AsyncHealingLogger (배치 비동기 처리)

**파일**: `packages/selfhealing-python/src/selfhealing/utils/async_logger.py`
**라인**: 36-55

```python
class AsyncHealingLogger:
    """
    비동기 힐링 이벤트 로거
    - 일반 이벤트: 배치로 모아서 전송
    - CRITICAL 이벤트: 즉시 전송 (비동기지만 바로)
    """
    _queue: queue.Queue = queue.Queue()
    _worker_thread: threading.Thread | None = None
```

**현재 상태**: Audit 미들웨어에 **연동되지 않음**

---

### 2.3 DjangoAuditAdapter.log_batch() (벌크 삽입)

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/audit/django_adapter.py`
**라인**: 100-128

```python
def log_batch(
    self,
    entries: list[AuditEntry],
) -> tuple[int, int]:
    """
    Batch log multiple audit entries.
    Uses bulk_insert_ignore_conflict for optimal performance.
    """
    inserted, skipped = self._model_class.bulk_insert_ignore_conflict(records)
```

**현재 상태**: 배치 API 존재하나 **미들웨어에서 미사용**

---

### 2.4 RedisAuditBuffer.flush_to_external() (Sidecar 플러시)

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/audit/redis_buffer.py`
**라인**: 167-210

```python
def flush_to_external(
    self,
    target_adapter: AuditLogAdapterProtocol,
    batch_size: int = 100,
    domain: str | None = None,
) -> int:
    """Redis 버퍼를 외부 저장소로 플러시 (Sidecar/배치 작업용)."""
```

**현재 상태**: 함수 존재하나 **Celery 태스크 미구현**

---

### 2.5 Kafka 헤더 전파 (분산 추적)

**파일**: `packages/selfhealing-python/src/selfhealing/context/causation_context.py`
**라인**: 512-534

```python
def get_causation_for_kafka() -> dict[str, bytes]:
    """Kafka 메시지 전송 시 전달할 causation 헤더 생성."""
    return {
        f"{KAFKA_HEADER_PREFIX}cascade_id": info.cascade_id.encode("utf-8"),
        f"{KAFKA_HEADER_PREFIX}parent_event": info.parent_event_id.encode("utf-8"),
        ...
    }
```

**현재 상태**: 헤더 생성 코드 존재, **KafkaAuditAdapter 미구현**

---

### 2.6 BatchSettings (배치 크기 설정)

**파일**: `packages/selfhealing-python/src/selfhealing/settings/batch.py`
**라인**: 133-137

```python
async_logger_max_queue_size: int = Field(
    default=5000,
    ge=100,
    le=100000,  # 최대 10만
    description="AsyncLogger 최대 큐 크기",
)
```

**현재 상태**: 설정 인프라 완비

---

## 3. 구현 계획 개요

### 3.0 Phase 0: WAL 통합 (데이터 유실 0% 보장) ⭐ 필수

| 문서 | 작업 | 예상 효과 |
|-----|------|----------|
| [166_RINGBUFFER_AUDIT_INTEGRATION.md](166_RINGBUFFER_AUDIT_INTEGRATION.md) | WAL + RingBuffer 통합 | **데이터 유실 0%** |
| - | CheckpointManager 구현 | 재시작 시 중복 방지 |
| - | GracefulShutdown 구현 | 종료 시 미처리 데이터 보장 |

**⚠️ 중요**: Phase 0은 다른 모든 Phase의 **전제 조건**입니다.
- WAL 없이 RingBuffer만 사용하면 **프로세스 종료 시 데이터 유실**
- WAL 없이 Kafka만 사용하면 **네트워크 장애 시 데이터 유실**

**코드 근거**:
- `wal.py`: WriteAheadLog 클래스 존재 (os.fsync로 디스크 영속화)
- `sync_worker.py`: AuditSyncWorker 클래스 존재 (WAL → 중앙 저장소 동기화)
- **미존재**: CheckpointManager (신규 구현 필요)

### 3.1 Phase 1: 코드만으로 해결 (인프라 변경 없음)

| 문서 | 작업 | 예상 효과 |
|-----|------|----------|
| [166_RINGBUFFER_AUDIT_INTEGRATION.md](166_RINGBUFFER_AUDIT_INTEGRATION.md) | WAL + RingBuffer를 Audit에 적용 | 이벤트 유실 방지 |
| [167_ASYNC_AUDIT_PIPELINE.md](167_ASYNC_AUDIT_PIPELINE.md) | AsyncHealingLogger + GracefulShutdown | 동기 블로킹 제거 |
| [168_REDIS_BATCH_OPTIMIZATION.md](168_REDIS_BATCH_OPTIMIZATION.md) | Redis 배치 처리 + Celery 플러시 | Redis RTT 90% 감소 |

### 3.2 Phase 2: 설정 변경

| 문서 | 작업 | 예상 효과 |
|-----|------|----------|
| [169_SETTINGS_SCALE_LIMITS.md](169_SETTINGS_SCALE_LIMITS.md) | max_events_per_second 상향 | 10만/초 지원 |

### 3.3 Phase 3: 인프라 추가 필요

| 문서 | 작업 | 필요 인프라 |
|-----|------|------------|
| [170_KAFKA_AUDIT_ADAPTER.md](170_KAFKA_AUDIT_ADAPTER.md) | Kafka Adapter 구현 | Kafka Cluster |
| [171_KUBERNETES_AUTOSCALING.md](171_KUBERNETES_AUTOSCALING.md) | K8s HPA 구현 | KEDA 또는 Prometheus Adapter |

---

## 4. 구현 순서 (의존성 기반)

```
Phase 0 (Week 0-1): WAL 통합 ⭐ 최우선
├── Step 0-1: WAL + RingBuffer 파이프라인 연결 (166)
│   └── 이벤트 → WAL.write() → RingBuffer → 처리
├── Step 0-2: CheckpointManager 구현
│   └── last_processed_seq 디스크 저장/복구
└── Step 0-3: GracefulShutdown 핸들러 (167)
    └── SIGTERM 수신 시 WAL 플러시 + 워커 종료 대기

Phase 1 (Week 1-2): 코드 변경만
├── Step 1: RingBuffer 적용 (166)
│   └── RequestAuditBuffer가 RingBuffer 사용
├── Step 2: AsyncHealingLogger 연동 (167)
│   └── AuditMiddleware → AsyncHealingLogger
└── Step 3: Redis 배치 최적화 (168)
    ├── RedisAuditBuffer.log_batch() 구현
    └── Celery Beat 플러시 태스크

Phase 2 (Week 3): 설정 변경
└── Step 4: Settings 제한 상향 (169)
    └── max_events_per_second: 100,000

Phase 3 (Week 4-5): 인프라 추가
├── Step 5: Kafka Adapter (170)
│   └── 초당 수십만 처리 가능
└── Step 6: K8s HPA (171)
    └── 자동 수평 확장
```

---

## 5. 예상 성능 개선

| 지표 | 현재 | Phase 0 후 | Phase 1 후 | Phase 3 후 |
|-----|------|-----------|-----------|------------|
| 데이터 유실 보장 | ❌ 유실 가능 | ✅ **0% 보장** | ✅ 0% | ✅ 0% |
| 초당 이벤트 처리 | ~1,000 | ~5,000 | ~50,000 | ~500,000+ |
| 요청 지연 추가 | ~5-10ms | ~1ms | ~0.1ms | ~0.01ms |
| 이벤트 유실률 | 높음 | 0% (WAL) | 0% | 0% |
| 수평 확장 | 수동 | 수동 | 수동 | 자동 (HPA) |

---

## 6. 관련 문서

- [166_RINGBUFFER_AUDIT_INTEGRATION.md](166_RINGBUFFER_AUDIT_INTEGRATION.md) - RingBuffer 적용
- [167_ASYNC_AUDIT_PIPELINE.md](167_ASYNC_AUDIT_PIPELINE.md) - 비동기 파이프라인
- [168_REDIS_BATCH_OPTIMIZATION.md](168_REDIS_BATCH_OPTIMIZATION.md) - Redis 배치 최적화
- [169_SETTINGS_SCALE_LIMITS.md](169_SETTINGS_SCALE_LIMITS.md) - 설정 제한 상향
- [170_KAFKA_AUDIT_ADAPTER.md](170_KAFKA_AUDIT_ADAPTER.md) - Kafka Adapter
- [171_KUBERNETES_AUTOSCALING.md](171_KUBERNETES_AUTOSCALING.md) - K8s HPA

---

## 7. 검증 기준

### 7.1 부하 테스트 시나리오

```python
# 테스트 스크립트 예시 (load_tests/ 디렉토리에 추가)
async def test_enterprise_scale():
    """초당 50,000 이벤트 처리 테스트."""
    tasks = []
    for _ in range(50000):
        tasks.append(generate_audit_event())

    start = time.time()
    await asyncio.gather(*tasks)
    elapsed = time.time() - start

    assert elapsed < 1.0, f"50k events took {elapsed}s (should be < 1s)"
```

### 7.2 성공 기준

- [ ] 초당 50,000 이벤트 처리 (Phase 1)
- [ ] 요청 지연 추가 < 1ms (Phase 1)
- [ ] 이벤트 유실률 0% (Phase 1)
- [ ] 자동 수평 확장 동작 (Phase 3)
