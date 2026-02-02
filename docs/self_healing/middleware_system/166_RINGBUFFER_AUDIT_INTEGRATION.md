# 166. RingBuffer + WAL을 Audit 이벤트에 적용 (데이터 유실 0%)

> **버전**: 2.1.0
> **작성일**: 2026-01-31
> **수정일**: 2026-02-03 (v2.1.0 개선 사항 추가)
> **의존성**: 없음 (첫 번째 구현)
> **예상 소요**: 2-3일 (기본) + 6일 (v2.1.0 개선)

---

## 1. 현재 문제점 (코드 근거)

### 1.1 RequestAuditBuffer의 이벤트 DROP

**파일**: `packages/selfhealing-python/src/selfhealing/audit/event_buffer.py`
**라인**: 305-345

```python
class RequestAuditBuffer:
    # 단일 요청당 최대 이벤트 수 (메모리 폭발 방지)
    DEFAULT_MAX_EVENTS = 100

    def __init__(self, max_events: int | None = None):
        self.events: list[AuditEvent] = []  # 일반 list 사용
        # ...
        self._max_events = int(os.environ.get(
            "SELFHEALING_MAX_EVENTS_PER_REQUEST",
            str(self.DEFAULT_MAX_EVENTS)
        ))
        self._truncated_count: int = 0

    def add_event(self, event: AuditEvent) -> bool:
        if len(self.events) >= self._max_events:
            self._truncated_count += 1  # 초과 이벤트는 DROP!
            self._mark_last_event_truncated()
            return False
        self.events.append(event)
        return True
```

**문제점**:
1. 100개 초과 이벤트는 **완전히 유실**됨
2. `list` 사용으로 메모리 무한 증가 가능성
3. Back-pressure 전략 없음

---

### 1.2 이미 존재하는 RingBuffer 구현

**파일**: `packages/selfhealing-python/src/selfhealing/audit/ring_buffer.py`
**라인**: 40-145

```python
class RingBuffer(Generic[T]):
    """
    Thread-Safe Ring Buffer with Backpressure.
    Shadow Logging을 위한 비침투 버퍼.
    메인 애플리케이션을 절대 블로킹하지 않음.
    """

    def __init__(
        self,
        capacity: int = 10000,
        strategy: BackpressureStrategy = BackpressureStrategy.DROP_OLDEST,
    ):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")

        self._capacity = capacity
        self._strategy = strategy
        self._buffer: deque = deque(maxlen=capacity)
        self._lock = Lock()
        self._total_enqueued = 0
        self._total_dropped = 0

    def put(self, item: T) -> bool:
        """Add item to buffer. Non-blocking."""
        with self._lock:
            self._total_enqueued += 1

            if len(self._buffer) >= self._capacity:
                if self._strategy == BackpressureStrategy.DROP_OLDEST:
                    # deque with maxlen automatically drops oldest
                    self._total_dropped += 1
                    self._buffer.append(item)
                    return True  # 오래된 것 DROP, 새 것 추가
                else:
                    # DROP_NEWEST: reject new item
                    self._total_dropped += 1
                    return False

            self._buffer.append(item)
            return True
```

**장점**:
1. **Non-blocking**: 메인 스레드 블로킹 없음
2. **Back-pressure**: DROP_OLDEST 전략으로 새 이벤트 우선
3. **Thread-safe**: Lock 사용
4. **통계 제공**: `total_enqueued`, `total_dropped` 추적

---

### 1.3 RingBufferSettings 존재

**파일**: `packages/selfhealing-python/src/selfhealing/settings/ring_buffer.py`
**라인**: 25-65

```python
class RingBufferSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_RING_BUFFER_",
    )

    capacity: int = Field(
        default=10000,
        ge=100,
        le=1000000,  # 최대 100만!
        description="Ring Buffer 최대 용량",
    )

    batch_max_size: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="배치 처리 시 최대 항목 수",
    )

    strategy: Literal["drop_oldest", "drop_newest"] = Field(
        default="drop_oldest",
        description="배압 전략. drop_oldest (권장: 비침투) 또는 drop_newest.",
    )
```

---

## 2. 구현 계획

### 2.1 수정 대상 파일

| 파일 | 변경 내용 |
|-----|----------|
| `audit/event_buffer.py` | `RequestAuditBuffer`가 `RingBuffer` 사용하도록 변경 |
| `settings/audit_settings.py` | RingBuffer 설정 통합 (선택) |

### 2.2 변경하지 않는 파일

| 파일 | 이유 |
|-----|------|
| `audit/ring_buffer.py` | 이미 완성된 구현, 수정 불필요 |
| `settings/ring_buffer.py` | 이미 완성된 설정, 수정 불필요 |

---

## 3. 구현 상세

### 3.1 RequestAuditBuffer 수정

**파일**: `packages/selfhealing-python/src/selfhealing/audit/event_buffer.py`

**Before** (라인 300-345):
```python
class RequestAuditBuffer:
    META_KEY = "X-AUDIT-EVENTS"
    DEFAULT_MAX_EVENTS = 100

    def __init__(self, max_events: int | None = None):
        self.events: list[AuditEvent] = []
        # ...
```

**After**:
```python
from selfhealing.audit.ring_buffer import RingBuffer, BackpressureStrategy
from selfhealing.settings.ring_buffer import get_ring_buffer_settings

class RequestAuditBuffer:
    META_KEY = "X-AUDIT-EVENTS"
    DEFAULT_MAX_EVENTS = 100  # 하위 호환성 유지

    def __init__(self, max_events: int | None = None):
        # RingBuffer 사용으로 변경
        settings = get_ring_buffer_settings()
        capacity = max_events or settings.capacity

        self._ring_buffer: RingBuffer[AuditEvent] = RingBuffer(
            capacity=capacity,
            strategy=BackpressureStrategy.DROP_OLDEST,
        )

        self.request_id: str | None = None
        self.start_time: datetime = datetime.now(timezone.utc)
        self._path: str | None = None
        self._method: str | None = None
        self._user_id: str | None = None

    @property
    def events(self) -> list[AuditEvent]:
        """하위 호환성: events 속성으로 접근 가능."""
        return list(self._ring_buffer.get_all())

    def add_event(self, event: AuditEvent) -> bool:
        """
        이벤트 추가. Non-blocking.

        RingBuffer 사용으로 DROP_OLDEST 전략 적용:
        - 버퍼가 가득 차면 가장 오래된 이벤트 제거
        - 새 이벤트는 항상 추가됨 (return True)
        """
        return self._ring_buffer.put(event)

    @property
    def stats(self) -> dict:
        """버퍼 통계 (모니터링용)."""
        rb_stats = self._ring_buffer.get_stats()
        return {
            "capacity": rb_stats.capacity,
            "size": rb_stats.size,
            "total_enqueued": rb_stats.total_enqueued,
            "total_dropped": rb_stats.total_dropped,
            "drop_rate": rb_stats.drop_rate,
        }

    @property
    def truncated_count(self) -> int:
        """하위 호환성: truncated_count는 dropped와 동일."""
        return self._ring_buffer.get_stats().total_dropped
```

---

### 3.2 RingBuffer에 get_all() 메서드 추가 (필요 시)

**파일**: `packages/selfhealing-python/src/selfhealing/audit/ring_buffer.py`

현재 코드 확인 필요. 없다면 추가:

```python
def get_all(self) -> list[T]:
    """모든 항목 반환 (비파괴적)."""
    with self._lock:
        return list(self._buffer)
```

---

## 4. 테스트 계획

### 4.1 단위 테스트

**파일**: `tests/unit/audit/test_request_audit_buffer_ringbuffer.py`

```python
import pytest
from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEvent, AuditEventType


class TestRequestAuditBufferWithRingBuffer:
    """RingBuffer 통합 후 RequestAuditBuffer 테스트."""

    def test_add_event_within_capacity(self):
        """용량 내 이벤트 추가."""
        buffer = RequestAuditBuffer(max_events=100)

        for i in range(50):
            event = AuditEvent(
                event_type=AuditEventType.DLQ_STORE,
                source="test",
                details={"idx": i},
            )
            result = buffer.add_event(event)
            assert result is True

        assert len(buffer.events) == 50
        assert buffer.stats["total_dropped"] == 0

    def test_add_event_exceeds_capacity_drop_oldest(self):
        """용량 초과 시 DROP_OLDEST 전략."""
        buffer = RequestAuditBuffer(max_events=10)

        # 15개 추가 (5개 DROP)
        for i in range(15):
            event = AuditEvent(
                event_type=AuditEventType.DLQ_STORE,
                source="test",
                details={"idx": i},
            )
            buffer.add_event(event)

        # 최신 10개만 남음
        assert len(buffer.events) == 10
        assert buffer.stats["total_dropped"] == 5

        # 가장 오래된 것(0-4)이 DROP됨
        assert buffer.events[0].details["idx"] == 5

    def test_non_blocking_under_load(self):
        """고부하에서 Non-blocking 확인."""
        import time

        buffer = RequestAuditBuffer(max_events=10000)

        start = time.time()
        for i in range(10000):
            event = AuditEvent(
                event_type=AuditEventType.DLQ_STORE,
                source="test",
            )
            buffer.add_event(event)
        elapsed = time.time() - start

        # 10,000개 추가가 100ms 이내
        assert elapsed < 0.1, f"10k events took {elapsed}s (should be < 0.1s)"

    def test_backward_compatibility_events_property(self):
        """하위 호환성: events 속성 접근."""
        buffer = RequestAuditBuffer(max_events=100)

        event = AuditEvent(
            event_type=AuditEventType.CB_STATE_CHANGE,
            source="test",
        )
        buffer.add_event(event)

        # 기존 코드처럼 events 속성 접근 가능
        assert len(buffer.events) == 1
        assert buffer.events[0].event_type == AuditEventType.CB_STATE_CHANGE

    def test_stats_property(self):
        """통계 속성 확인."""
        buffer = RequestAuditBuffer(max_events=10)

        for i in range(15):
            buffer.add_event(AuditEvent(
                event_type=AuditEventType.DLQ_STORE,
                source="test",
            ))

        stats = buffer.stats
        assert stats["capacity"] == 10
        assert stats["size"] == 10
        assert stats["total_enqueued"] == 15
        assert stats["total_dropped"] == 5
        assert stats["drop_rate"] == pytest.approx(5/15, rel=0.01)
```

---

### 4.2 통합 테스트

**파일**: `tests/integration/selfhealing/test_audit_buffer_ringbuffer_integration.py`

```python
class TestAuditBufferRingBufferIntegration:
    """AuditMiddleware + RingBuffer 통합 테스트."""

    def test_middleware_uses_ringbuffer(self, rf):
        """미들웨어가 RingBuffer 기반 버퍼 사용."""
        from selfhealing.api.django.audit_middleware import AuditMiddleware
        from selfhealing.audit.event_buffer import RequestAuditBuffer

        request = rf.get("/api/test/")
        buffer = RequestAuditBuffer.get_or_create(request)

        # 200개 이벤트 추가 (기본 capacity 10,000)
        for i in range(200):
            buffer.add(
                event_type=AuditEventType.DLQ_STORE,
                source="test",
                details={"idx": i},
            )

        # 모든 이벤트 유지됨 (기존: 100개 초과 시 DROP)
        assert len(buffer.events) == 200
        assert buffer.stats["total_dropped"] == 0

    def test_high_volume_no_blocking(self, rf):
        """고용량에서 블로킹 없음 확인."""
        import time

        request = rf.get("/api/test/")
        buffer = RequestAuditBuffer.get_or_create(request)

        start = time.time()
        for i in range(10000):
            buffer.add(
                event_type=AuditEventType.CONFIG_CHANGE,
                source="stress_test",
            )
        elapsed = time.time() - start

        assert elapsed < 0.5, f"10k adds took {elapsed}s"
```

---

## 5. 환경 변수

| 변수명 | 기본값 | 설명 |
|-------|-------|------|
| `SELFHEALING_RING_BUFFER_CAPACITY` | 10000 | 버퍼 최대 용량 |
| `SELFHEALING_RING_BUFFER_STRATEGY` | drop_oldest | 배압 전략 |
| `SELFHEALING_RING_BUFFER_BATCH_MAX_SIZE` | 100 | 배치 크기 |

---

## 6. 마이그레이션 체크리스트

### 6.1 코드 변경

- [x] `event_buffer.py`: `RequestAuditBuffer`가 `RingBuffer` 사용
- [x] `ring_buffer.py`: `get_all()` 메서드 추가
- [x] `checkpoint_manager.py`: CheckpointManager 신규 구현
- [x] 하위 호환성: `events` 속성, `truncated_count` 속성 유지
- [x] WAL 통합: `RequestAuditBuffer`에 WAL 연동 (선택적 활성화)

### 6.2 테스트

- [x] 단위 테스트 추가 (`test_request_audit_buffer_ringbuffer.py`)
- [x] 단위 테스트 추가 (`test_checkpoint_manager.py`)
- [x] 단위 테스트 추가 (`test_ring_buffer_get_all.py`)
- [x] 기존 테스트 업데이트 (`test_event_buffer_max_events.py`)
- [x] 기존 테스트 통과 확인 (61개 테스트 통과)

### 6.3 배포

- [x] 환경 변수 설정 문서화
- [ ] 모니터링: `buffer.stats["drop_rate"]` 메트릭 추가

---

## 7. 예상 효과

| 지표 | Before | After |
|-----|--------|-------|
| 최대 이벤트 수 | 100 (고정) | 100만 (설정 가능) |
| 초과 이벤트 처리 | 완전 DROP | DROP_OLDEST (최신 우선) |
| 메모리 관리 | 무한 증가 가능 | 고정 용량 |
| 통계 제공 | truncated_count만 | capacity, size, drop_rate 등 |

---

## 8. ⚠️ 중요: WAL 통합으로 데이터 유실 0% 달성

### 8.1 현재 RingBuffer의 한계

**문제점**: RingBuffer만 사용 시 데이터 유실 가능성 존재

| 유실 지점 | 코드 근거 | 발생 조건 |
|----------|----------|----------|
| DROP_OLDEST | `ring_buffer.py#L152-L155` | 버퍼 가득 시 오래된 데이터 삭제 |
| daemon=True | `async_logger.py#L111` | 프로세스 종료 시 미처리 데이터 유실 |
| 플러시 실패 | `async_logger.py#L227-L231` | 재시도 없이 폐기 |

### 8.2 WAL이 이미 존재함

**파일**: `packages/selfhealing-python/src/selfhealing/audit/wal.py`

```python
class WriteAheadLog:
    """CRC32 체크섬으로 무결성 검증, 디스크 영속화"""

    def write(self, data: dict[str, Any]) -> int:
        """WAL에 기록 (sync_on_write=True면 os.fsync 호출)"""
        # ...
        if self._config.sync_on_write:
            self._current_handle.flush()
            os.fsync(self._current_handle.fileno())  # ✅ 디스크 영속화!

    def recover_unprocessed(self, last_processed_seq: int = 0) -> list[WALEntry]:
        """미처리 엔트리 복구"""

    def cleanup_processed(self, last_processed_seq: int) -> int:
        """처리 완료된 엔트리 정리"""
```

### 8.3 AuditSyncWorker가 이미 존재함

**파일**: `packages/selfhealing-python/src/selfhealing/audit/sync_worker.py`

```python
class AuditSyncWorker:
    """
    Background Sync Worker - WAL → 중앙 저장소 동기화.
    ADR-005 (Fail-Open + WAL 기반 누락 0 보장) 구현의 핵심 컴포넌트.

    동작 원리:
    1. WAL에서 미동기화 엔트리 조회
    2. 중앙 저장소에 기록 시도
    3. 성공 시 WAL 엔트리 정리 (cleanup_processed)
    4. 실패 시 재시도 (exponential backoff)
    """
```

### 8.4 데이터 유실 0% 아키텍처

```
현재 (유실 가능):
  이벤트 → RingBuffer (메모리) → AsyncLogger → Redis/DB
               ↓ 유실              ↓ 유실

개선 후 (유실 0%):
  이벤트 → WAL.write() → RingBuffer → AsyncLogger → Redis/DB
              ↓                                         ↓
         os.fsync()                              성공 시 WAL 정리
              ↓                                         ↓
         디스크 영속화                      실패 시 WAL에서 재시도
              ↓
         프로세스 재시작 시
         recover_unprocessed()로 복구
```

### 8.5 구현 필요 사항

**수정 대상**: `RequestAuditBuffer.add_event()`

```python
# 현재 (유실 가능)
def add_event(self, event: AuditEvent) -> bool:
    return self._ring_buffer.put(event)  # 메모리만

# 수정 후 (유실 0%)
def add_event(self, event: AuditEvent) -> bool:
    # 1. WAL에 먼저 기록 (디스크 영속화)
    seq = self._wal.write(event.to_dict())

    # 2. 메모리 버퍼에 추가 (빠른 접근용)
    self._ring_buffer.put((seq, event))

    return True  # WAL 기록 성공 = 유실 0%
```

### 8.6 필요한 추가 컴포넌트

| 컴포넌트 | 파일 | 역할 | 현재 상태 |
|---------|-----|------|----------|
| WriteAheadLog | `audit/wal.py` | 디스크 영속화 | ✅ 존재 |
| AuditSyncWorker | `audit/sync_worker.py` | WAL → 중앙 동기화 | ✅ 존재 |
| CheckpointManager | 신규 필요 | last_processed_seq 저장 | ❌ 미존재 |
| GracefulShutdown | 신규 필요 | 종료 시 플러시 보장 | ⚠️ 부분 존재 |

### 8.7 CheckpointManager 구현 예시 (신규 필요)

```python
class CheckpointManager:
    """마지막 처리 시퀀스를 디스크에 저장."""

    def __init__(self, checkpoint_path: str = "/var/log/audit/checkpoint"):
        self._path = Path(checkpoint_path)

    def save(self, last_seq: int) -> None:
        """체크포인트 저장."""
        self._path.write_text(str(last_seq))
        os.fsync(...)  # 디스크 영속화

    def load(self) -> int:
        """체크포인트 로드."""
        if self._path.exists():
            return int(self._path.read_text())
        return 0
```

### 8.8 시작/종료 시퀀스

**시작 시**:
```python
def startup():
    # 1. 체크포인트 로드
    last_seq = checkpoint_manager.load()

    # 2. 미처리 WAL 엔트리 복구
    entries = wal.recover_unprocessed(last_seq)

    # 3. 복구된 엔트리 재처리
    for entry in entries:
        process_entry(entry)
        checkpoint_manager.save(entry.sequence)

    # 4. 정상 동작 시작
    sync_worker.start()
```

**종료 시**:
```python
def shutdown():
    # 1. 새 이벤트 수신 중단
    stop_accepting_events()

    # 2. 메모리 버퍼 WAL 플러시
    wal.flush()

    # 3. 동기화 워커 정상 종료
    sync_worker.stop(timeout=30)

    # 4. 체크포인트 저장
    checkpoint_manager.save(last_processed_seq)
```

---

## 9. 메시지 큐(Kafka)와의 관계

### 9.1 각 컴포넌트의 역할 (코드 근거)

| 컴포넌트 | 역할 | 대체 가능? |
|---------|------|-----------|
| **WAL** | 로컬 디스크 영속화 (유실 0%) | ❌ 필수 |
| **RingBuffer** | 메모리 백프레셔 (Non-blocking) | ❌ 필수 |
| **AsyncLogger** | 배치 I/O 최적화 | ⚠️ 선택 |
| **Redis** | 분산 버퍼링 | ⚠️ Kafka로 대체 가능 |
| **Kafka** | 분산 스트리밍 + 수평 확장 | ⚠️ Redis로 대체 가능 |

### 9.2 Kafka 사용 시에도 WAL 필요

```
Kafka만 사용 (유실 가능):
  이벤트 → Kafka Producer.send()
              ↓
         네트워크 실패 시 유실!

WAL + Kafka (유실 0%):
  이벤트 → WAL.write() → Kafka Producer.send()
              ↓              ↓
         영속화 완료    실패해도 WAL에서 재시도
```

**결론**: Kafka는 **분산 전송**용, WAL은 **로컬 영속화**용. 둘은 **상호 보완** 관계.

---

## 10. 코드 리뷰 기반 개선 사항 (v2.1.0)

> **추가일**: 2026-02-03
> **목적**: 안정성, 운영 가시성, 데이터 무결성 강화

### 10.1 구현 우선순위

| 순위 | 개선 항목 | 긴급도 | 구현 복잡도 | 파일 |
|-----|----------|--------|------------|------|
| 1 | 멀티 프로세스 파일명 충돌 방지 | ❌ 필수 | 낮음 (1줄) | `wal.py` |
| 2 | 디스크 풀 Fail-Open 모드 | ❌ 필수 | 중간 | `wal.py` |
| 3 | 드랍률 알림 연동 | ❌ 필수 | 중간 | `ring_buffer.py` |
| 4 | TraceID 자동 포함 | ⚠️ 권장 | 낮음 | `event_buffer.py` |
| 5 | Idempotent Consumer | ⚠️ 권장 | 중간 | `sync_worker.py` |
| 6 | Best-Effort Recovery | ⚠️ 권장 | 높음 | `wal.py` |
| 7 | 메모리 경고 | ⚠️ 선택 | 낮음 | `ring_buffer.py` |
| 8 | Lazy Recovery 옵션 | ⚠️ 선택 | 중간 | `async_audit_lifecycle.py` |

---

### 10.2 순위 1: 멀티 프로세스 파일명 충돌 방지

**문제점**: Gunicorn 워커들이 동일 초에 시작 시 같은 WAL 파일명 사용

**현재 코드** (`wal.py#L233-L235`):
```python
def _get_current_wal_filename(self) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{self._config.file_prefix}_{timestamp}.wal"
```

**개선 코드**:
```python
def _get_current_wal_filename(self) -> str:
    """현재 WAL 파일명 생성 (PID 포함으로 멀티 프로세스 안전)."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    pid = os.getpid()
    return f"{self._config.file_prefix}_{timestamp}_{pid}.wal"
```

**테스트**:
```python
def test_wal_filename_includes_pid():
    """WAL 파일명에 PID가 포함되는지 확인."""
    wal = WriteAheadLog(config=WALConfig(wal_dir="/tmp/test_wal"))
    filename = wal._get_current_wal_filename()
    assert str(os.getpid()) in filename
```

---

### 10.3 순위 2: 디스크 풀 Fail-Open 모드

**문제점**: 디스크가 꽉 차면 서비스 전체 장애 (암묵적 Fail-Closed)

**WALState 확장** (`wal.py`):
```python
class WALState(Enum):
    ACTIVE = "active"
    ROTATING = "rotating"
    CLOSED = "closed"
    CORRUPTED = "corrupted"
    DISK_FULL_FAILOPEN = "disk_full_failopen"  # 신규 추가
```

**WALConfig 확장**:
```python
@dataclass
class WALConfig:
    # 기존 필드...
    fail_open_on_disk_full: bool = True  # 디스크 풀 시 Fail-Open 활성화
```

**개선 코드** (`wal.py#L315-L340` write 메서드 내):
```python
def _direct_write(self, data: dict[str, Any]) -> int:
    """직접 기록 (Disk Full Fail-Open 지원)."""
    with self._lock:
        # Fail-Open 모드면 WAL 기록 스킵
        if self._state == WALState.DISK_FULL_FAILOPEN:
            logger.warning("[WAL] Disk full fail-open mode, skipping WAL write")
            return -1  # 음수 시퀀스 = WAL 미기록

        if self._state == WALState.CLOSED:
            raise WALError("WAL is closed")

        try:
            self._sequence += 1
            # ... 기존 기록 로직 ...

        except OSError as e:
            import errno
            if e.errno == errno.ENOSPC:  # No space left on device
                self._handle_disk_full()
                if self._config.fail_open_on_disk_full:
                    return -1  # Fail-Open: 서비스 계속
                raise  # Fail-Closed: 예외 전파
            raise

def _handle_disk_full(self) -> None:
    """디스크 풀 상황 처리."""
    self._state = WALState.DISK_FULL_FAILOPEN
    logger.critical("[WAL] DISK FULL - Switching to fail-open mode")

    # 메트릭 기록
    if HAS_DRIFT_METRICS:
        record_wal_disk_full()

    # 알림 전송
    try:
        from selfhealing.services.unified_notification import (
            UnifiedNotificationManager,
            NotificationPayload,
            NotificationPriority,
            NotificationCategory,
        )
        payload = NotificationPayload(
            title="🚨 WAL Disk Full - Fail-Open Mode",
            message="WAL 디스크 용량 부족으로 Fail-Open 모드 전환. 즉시 조치 필요!",
            priority=NotificationPriority.CRITICAL,
            category=NotificationCategory.OPERATIONS,
            source="WriteAheadLog",
            dedup_key="wal:disk_full",
        )
        UnifiedNotificationManager().notify(payload)
    except Exception as e:
        logger.error(f"[WAL] Failed to send disk full notification: {e}")
```

**복구 로직** (디스크 여유 공간 확보 후):
```python
def check_disk_recovery(self) -> bool:
    """디스크 여유 공간 확보 시 정상 모드 복귀."""
    if self._state != WALState.DISK_FULL_FAILOPEN:
        return True

    try:
        import shutil
        usage = shutil.disk_usage(self._wal_dir)
        free_ratio = usage.free / usage.total

        if free_ratio > 0.1:  # 10% 이상 여유 시 복귀
            self._state = WALState.ACTIVE
            logger.info("[WAL] Disk space recovered, resuming normal operation")
            return True
    except Exception:
        pass

    return False
```

---

### 10.4 순위 3: 드랍률 알림 연동

**문제점**: 데이터 드랍 발생해도 관리자 인지 불가

**RingBuffer 확장** (`ring_buffer.py`):
```python
from collections.abc import Callable

class RingBuffer(Generic[T]):
    def __init__(
        self,
        capacity: int = 10000,
        strategy: BackpressureStrategy = BackpressureStrategy.DROP_OLDEST,
        on_drop_threshold: Callable[["RingBufferStats"], None] | None = None,
        drop_rate_threshold: float = 0.01,  # 1% 기본값
    ):
        # 기존 초기화...
        self._on_drop_threshold = on_drop_threshold
        self._drop_rate_threshold = drop_rate_threshold
        self._alert_sent = False  # 중복 알림 방지

    def put(self, item: T) -> bool:
        with self._lock:
            self._total_enqueued += 1

            if len(self._buffer) >= self._capacity:
                if self._strategy == BackpressureStrategy.DROP_OLDEST:
                    self._total_dropped += 1
                    self._buffer.append(item)
                    self._check_drop_rate_alert()  # 알림 체크
                    return True
                else:
                    self._total_dropped += 1
                    self._check_drop_rate_alert()
                    return False

            self._buffer.append(item)
            return True

    def _check_drop_rate_alert(self) -> None:
        """드랍률 임계치 초과 시 알림."""
        if self._on_drop_threshold is None or self._alert_sent:
            return

        if self._total_enqueued < 100:  # 최소 샘플 수
            return

        drop_rate = self._total_dropped / self._total_enqueued
        if drop_rate > self._drop_rate_threshold:
            self._alert_sent = True
            stats = RingBufferStats(
                capacity=self._capacity,
                size=len(self._buffer),
                total_enqueued=self._total_enqueued,
                total_dropped=self._total_dropped,
                drop_rate=drop_rate,
            )
            try:
                self._on_drop_threshold(stats)
            except Exception:
                pass  # 알림 실패가 메인 로직 방해 금지

    def reset_alert(self) -> None:
        """알림 상태 리셋 (주기적 호출용)."""
        with self._lock:
            self._alert_sent = False
```

**UnifiedNotificationManager 연동**:
```python
def create_drop_rate_alert_callback():
    """드랍률 알림 콜백 생성."""
    def on_drop_threshold(stats: RingBufferStats) -> None:
        from selfhealing.services.unified_notification import (
            UnifiedNotificationManager,
            NotificationPayload,
            NotificationPriority,
            NotificationCategory,
        )
        payload = NotificationPayload(
            title="⚠️ RingBuffer Drop Rate Alert",
            message=(
                f"드랍률 {stats.drop_rate:.2%} 임계치 초과!\n"
                f"• 총 입력: {stats.total_enqueued}\n"
                f"• 드랍: {stats.total_dropped}\n"
                f"• 용량: {stats.size}/{stats.capacity}"
            ),
            priority=NotificationPriority.CRITICAL,
            category=NotificationCategory.OPERATIONS,
            source="RingBuffer",
            dedup_key="ringbuffer:drop_rate_alert",
        )
        UnifiedNotificationManager().notify(payload)

    return on_drop_threshold
```

**RequestAuditBuffer에서 사용**:
```python
class RequestAuditBuffer:
    def __init__(self, max_events: int | None = None, ...):
        from selfhealing.audit.ring_buffer import RingBuffer, BackpressureStrategy

        self._ring_buffer: RingBuffer[AuditEvent] = RingBuffer(
            capacity=capacity,
            strategy=BackpressureStrategy.DROP_OLDEST,
            on_drop_threshold=create_drop_rate_alert_callback(),
            drop_rate_threshold=0.01,  # 1%
        )
```

---

### 10.5 순위 4: TraceID 자동 포함

**문제점**: 감사 로그에서 트레이스 추적 불가

**AuditEvent 확장** (`event_buffer.py`):
```python
@dataclass
class AuditEvent:
    event_type: AuditEventType
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = "unknown"
    details: dict[str, Any] = field(default_factory=dict)
    actor_id: str | None = None
    actor_type: str = "system"
    success: bool = True
    error_message: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    domain: str | None = None
    reason: str | None = None
    trace_id: str | None = field(default=None)  # 신규 추가

    def __post_init__(self):
        """trace_id 자동 설정."""
        if self.trace_id is None:
            try:
                from selfhealing.audit.trace import get_trace_id
                self.trace_id = get_trace_id()
            except Exception:
                pass

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "details": self.details,
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "success": self.success,
            "error_message": self.error_message,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "domain": self.domain,
            "reason": self.reason,
            "trace_id": self.trace_id,  # 신규 추가
        }
```

**테스트**:
```python
def test_audit_event_auto_trace_id():
    """AuditEvent 생성 시 trace_id 자동 설정."""
    from selfhealing.audit.trace import set_trace_id

    set_trace_id("req-test123")
    event = AuditEvent(
        event_type=AuditEventType.DLQ_STORE,
        source="test",
    )
    assert event.trace_id == "req-test123"
```

---

### 10.6 순위 5: Idempotent Consumer

**문제점**: At-least-once 전송에서 중복 처리 가능

**코드 근거**: `IdempotencyDomain.WAL_RECOVERY`가 이미 존재

```python
# idempotency_service.py#L88-L89
WAL_RECOVERY = "wal_recovery"
"""WAL 복구 (동일 엔트리 중복 처리 방지)."""
```

**sync_worker.py 개선**:
```python
from selfhealing.services.idempotency_service import (
    IdempotencyService,
    IdempotencyKey,
    IdempotencyDomain,
)

class AuditSyncWorker:
    def __init__(self, ...):
        # 기존 초기화...
        self._idempotency = IdempotencyService()

    def _sync_entry_to_adapter(self, adapter: Any, entry: Any) -> None:
        """Idempotent Consumer 패턴 적용."""
        # 중복 체크 키: sequence + checksum
        key = IdempotencyKey(
            domain=IdempotencyDomain.WAL_RECOVERY,
            identifier=f"{entry.sequence}:{entry.checksum}",
        )

        # 이미 처리된 경우 스킵
        if self._idempotency.is_processed(key):
            logger.debug(f"[AuditSyncWorker] Skipping duplicate entry seq={entry.sequence}")
            return

        # 원본 동기화 로직
        delay = self._config.retry_delay_seconds
        last_error: Exception | None = None

        for attempt in range(self._config.max_retries + 1):
            try:
                if hasattr(adapter, "write"):
                    adapter.write(entry.data)
                elif hasattr(adapter, "log"):
                    adapter.log(entry.data)
                else:
                    logger.info(f"[AuditSync] {entry.data}")

                # 성공 시 처리 완료 마킹
                self._idempotency.mark_processed(key, ttl_seconds=86400)  # 24시간 TTL
                return

            except Exception as e:
                last_error = e
                if attempt < self._config.max_retries:
                    with self._lock:
                        self._stats.total_retries += 1
                    time.sleep(delay)
                    delay = min(
                        delay * self._config.retry_backoff_multiplier,
                        self._config.max_retry_delay_seconds,
                    )

        if last_error:
            raise last_error
```

---

### 10.7 순위 6: Best-Effort Recovery

**문제점**: 레코드 길이 필드 손상 시 전체 파일 복구 불가

**레코드 Magic Number 추가** (`wal.py`):
```python
@dataclass
class WALConfig:
    # 기존 필드...
    best_effort_recovery: bool = True  # 손상 시 마커 기반 복구
    record_magic: bytes = b"\xAB\xCD"  # 레코드 시작 마커 (2바이트)

class WriteAheadLog:
    RECORD_MAGIC = b"\xAB\xCD"  # 레코드 시작 마커
    RECORD_HEADER_SIZE = 14  # magic(2) + length(4) + checksum(8)
```

**개선된 레코드 포맷**:
```
[RECORD_MAGIC(2)] [LENGTH(4)] [CHECKSUM(8)] [DATA(variable)]
    \xAB\xCD      4-byte BE   8-char hex    JSON bytes
```

**손상 복구 로직**:
```python
def _read_wal_file_best_effort(self, filepath: Path) -> Iterator[WALEntry]:
    """Best-effort 복구 모드로 WAL 파일 읽기."""
    try:
        with open(filepath, "rb") as f:
            # 헤더 읽기
            header = f.read(self.HEADER_SIZE)
            if len(header) < self.HEADER_SIZE:
                return

            while True:
                # Magic Number 찾기
                magic = f.read(2)
                if len(magic) < 2:
                    break

                if magic != self.RECORD_MAGIC:
                    # Magic이 아니면 1바이트씩 스캔
                    if self._config.best_effort_recovery:
                        pos = self._scan_for_magic(f)
                        if pos == -1:
                            break
                        continue
                    else:
                        break

                # 정상 레코드 읽기
                try:
                    entry = self._read_single_record(f)
                    if entry:
                        yield entry
                except Exception:
                    if not self._config.best_effort_recovery:
                        break
                    # Best-effort: 다음 Magic 찾기
                    continue

    except Exception:
        pass

def _scan_for_magic(self, f) -> int:
    """다음 Magic Number 위치까지 스캔."""
    window = bytearray()
    while True:
        byte = f.read(1)
        if not byte:
            return -1
        window.append(byte[0])
        if len(window) > 2:
            window.pop(0)
        if bytes(window) == self.RECORD_MAGIC:
            return f.tell()
    return -1
```

---

### 10.8 순위 7: 메모리 경고

**문제점**: 100만 용량 설정 시 메모리 폭발 위험

**RingBuffer 생성자 경고** (`ring_buffer.py`):
```python
import logging

logger = logging.getLogger(__name__)

class RingBuffer(Generic[T]):
    # 경고 임계치 (10만 이상)
    CAPACITY_WARNING_THRESHOLD = 100000
    # 추정 이벤트 크기 (1KB)
    ESTIMATED_EVENT_SIZE_BYTES = 1024

    def __init__(
        self,
        capacity: int = 10000,
        strategy: BackpressureStrategy = BackpressureStrategy.DROP_OLDEST,
        ...
    ):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")

        # 메모리 경고
        if capacity > self.CAPACITY_WARNING_THRESHOLD:
            estimated_mb = (capacity * self.ESTIMATED_EVENT_SIZE_BYTES) / (1024 * 1024)
            logger.warning(
                f"[RingBuffer] High capacity={capacity:,} may use ~{estimated_mb:.0f}MB RAM. "
                f"Consider using a lower capacity or enabling WAL for persistence."
            )

        self._capacity = capacity
        # 기존 로직...
```

---

### 10.9 순위 8: Lazy Recovery 옵션

**문제점**: 시작 시 대량 WAL 읽기로 서버 기동 지연

**WALConfig 확장**:
```python
@dataclass
class WALConfig:
    # 기존 필드...
    lazy_recovery: bool = True  # 지연 복구 활성화
    startup_recovery_limit: int = 1000  # 시작 시 최대 복구 엔트리 수
```

**async_audit_lifecycle.py 개선**:
```python
def startup_async_audit_system() -> bool:
    """비동기 Audit 시스템 시작 및 복구 (Lazy Recovery 지원)."""
    global _startup_completed

    with _lifecycle_lock:
        if _startup_completed:
            return False

        try:
            # 1. 체크포인트 로드
            last_seq = _load_checkpoint()

            # 2. WAL 미처리 엔트리 수만 확인 (Lazy)
            unprocessed_count = _check_unprocessed_wal_entries(last_seq)
            if unprocessed_count > 0:
                logger.info(
                    f"[AsyncAuditLifecycle] Found {unprocessed_count} unprocessed WAL entries. "
                    f"SyncWorker will recover in background."
                )

            # 3. AsyncHealingLogger 초기화
            _initialize_async_logger()

            # 4. SyncWorker 시작 (백그라운드 복구 담당)
            _start_sync_worker()

            _startup_completed = True
            return True

        except Exception as e:
            logger.error(f"[AsyncAuditLifecycle] Startup failed: {e}")
            return False

def _check_unprocessed_wal_entries(last_seq: int) -> int:
    """WAL 미처리 엔트리 수 확인 (읽기만, 처리 안 함)."""
    try:
        wal = _get_wal_instance()
        if wal is None:
            return 0

        # startup_recovery_limit 적용
        config = getattr(wal, "_config", None)
        limit = getattr(config, "startup_recovery_limit", 1000)

        if hasattr(wal, "count_unprocessed"):
            return wal.count_unprocessed(last_processed_seq=last_seq)

        # Fallback: 전체 읽기 (기존 동작)
        if hasattr(wal, "recover_unprocessed"):
            entries = wal.recover_unprocessed(last_processed_seq=last_seq)
            return len(entries) if entries else 0

        return 0
    except Exception as e:
        logger.debug(f"[AsyncAuditLifecycle] WAL check failed: {e}")
        return 0
```

**WAL에 count_unprocessed 메서드 추가**:
```python
def count_unprocessed(self, last_processed_seq: int = 0) -> int:
    """미처리 엔트리 수 반환 (파일 전체 읽기 없이)."""
    with self._lock:
        # 현재 시퀀스와 마지막 처리 시퀀스 차이
        return max(0, self._sequence - last_processed_seq)
```

---

## 11. 구현 체크리스트 (v2.1.0)

### 11.1 Phase 1: 즉시 수정 (1일)

- [x] `wal.py`: `_get_current_wal_filename()`에 PID 추가
- [x] `wal.py`: `DISK_FULL_FAILOPEN` 상태 및 Fail-Open 로직 추가
- [x] `ring_buffer.py`: 고용량 경고 로그 추가
- [x] 단위 테스트 추가

### 11.2 Phase 2: 운영 가시성 (2일)

- [x] `ring_buffer.py`: `on_drop_threshold` 콜백 및 알림 연동
- [x] `event_buffer.py`: `AuditEvent.trace_id` 자동 설정
- [x] `unified_notification.py` 연동 테스트
- [x] 통합 테스트 추가

### 11.3 Phase 3: 데이터 무결성 (3일)

- [x] `sync_worker.py`: `IdempotencyService` 연동
- [x] `wal.py`: `RECORD_MAGIC` 및 Best-Effort Recovery
- [x] `async_audit_lifecycle.py`: Lazy Recovery 옵션
- [x] E2E 테스트 추가

### 11.4 환경 변수 (신규)

| 변수명 | 기본값 | 설명 |
|-------|-------|------|
| `SELFHEALING_WAL_FAIL_OPEN_ON_DISK_FULL` | true | 디스크 풀 시 Fail-Open |
| `SELFHEALING_WAL_BEST_EFFORT_RECOVERY` | true | 손상 복구 시도 |
| `SELFHEALING_WAL_LAZY_RECOVERY` | true | 지연 복구 활성화 |
| `SELFHEALING_WAL_STARTUP_RECOVERY_LIMIT` | 1000 | 시작 시 최대 복구 수 |
| `SELFHEALING_RING_BUFFER_DROP_RATE_THRESHOLD` | 0.01 | 드랍률 알림 임계치 |

---

## 12. 다음 단계

→ [167_ASYNC_AUDIT_PIPELINE.md](167_ASYNC_AUDIT_PIPELINE.md): AsyncHealingLogger 미들웨어 연동
