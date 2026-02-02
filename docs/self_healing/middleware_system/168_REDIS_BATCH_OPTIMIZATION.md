# 168. Redis 배치 처리 최적화

> **버전**: 2.0.0
> **작성일**: 2026-01-31
> **의존성**: [167_ASYNC_AUDIT_PIPELINE.md](167_ASYNC_AUDIT_PIPELINE.md), [166_RINGBUFFER_AUDIT_INTEGRATION.md](166_RINGBUFFER_AUDIT_INTEGRATION.md)
> **예상 소요**: 3-4일
> **리뷰 반영**: Q1~Q11 및 추가 보완사항 3건

---

## 1. 현재 문제점 (코드 근거)

### 1.1 RedisAuditBuffer의 개별 처리

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/audit/redis_buffer.py`
**라인**: 113-135

```python
class RedisAuditBuffer(BufferedAuditAdapter):
    def log(self, entry: AuditEntry) -> None:
        """단일 이벤트 로깅 - 매번 Redis 왕복!"""
        with self._lock:
            key = self._build_key(entry.source)

            try:
                pipe = self._redis.pipeline(transaction=True)

                # 단일 항목 LPUSH
                pipe.lpush(key, entry.to_json())
                pipe.ltrim(key, 0, self._max_entries - 1)

                # ❌ 매번 execute() 호출 = Redis RTT 발생!
                pipe.execute()

            except redis.RedisError as e:
                self._handle_failure(entry, e)
```

**문제점**:
- 이벤트 1개당 `pipeline.execute()` 1회
- Redis RTT (Round Trip Time): 0.5-2ms
- 초당 10,000개 = **5-20초 RTT 비용!**

---

### 1.2 이미 존재하는 flush_to_external()

**파일**: 동일 (`redis_buffer.py`)
**라인**: 230-280

```python
class RedisAuditBuffer(BufferedAuditAdapter):
    def flush_to_external(
        self,
        adapter: AuditAdapter,
        batch_size: int = 100,
        delete_after: bool = True,
    ) -> int:
        """
        Redis 버퍼 → 외부 어댑터로 플러시.
        Sidecar 패턴 또는 Celery Beat에서 호출 가능.

        Args:
            adapter: 외부 어댑터 (DB, Kafka 등)
            batch_size: 배치 크기
            delete_after: 전송 후 삭제 여부

        Returns:
            플러시된 이벤트 수
        """
        total_flushed = 0

        for key in self._redis.scan_iter(match=f"{self.KEY_PREFIX}:*"):
            while True:
                # LRANGE로 배치 읽기
                entries_json = self._redis.lrange(key, 0, batch_size - 1)
                if not entries_json:
                    break

                entries = [AuditEntry.from_json(e) for e in entries_json]

                # 외부 어댑터에 배치 전송
                if hasattr(adapter, "log_batch"):
                    adapter.log_batch(entries)
                else:
                    for entry in entries:
                        adapter.log(entry)

                if delete_after:
                    # LTRIM으로 전송된 항목 삭제
                    self._redis.ltrim(key, batch_size, -1)

                total_flushed += len(entries)

        return total_flushed
```

**활용 가능**:
- 이미 배치 처리 로직 구현됨
- Celery Beat에서 주기적 호출 가능

---

### 1.3 DjangoAuditAdapter의 log_batch()

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/audit/django_adapter.py`
**라인**: 180-210

```python
class DjangoAuditAdapter(AuditAdapter):
    def log_batch(self, entries: list[AuditEntry]) -> None:
        """
        배치 삽입 (bulk_create + ignore_conflicts).
        단일 DB 쿼리로 여러 엔트리 삽입.
        """
        if not entries:
            return

        models_to_create = []
        for entry in entries:
            models_to_create.append(
                AuditLog(
                    action=entry.action,
                    source=entry.source,
                    target_type=entry.target_type,
                    target_id=entry.target_id,
                    details=entry.details,
                    # ...
                )
            )

        # 단일 쿼리로 배치 삽입
        AuditLog.objects.bulk_create(
            models_to_create,
            batch_size=500,
            ignore_conflicts=True,  # 중복 무시
        )
```

---

## 2. 구현 계획

### 2.1 아키텍처

```
현재:
  Event → RedisAuditBuffer.log() → pipe.execute() (매번 RTT)
          ↓
        Redis

개선 후:
  Event → AsyncHealingLogger._queue → RedisAuditBuffer.log_batch()
          ↓                           ↓ (단일 pipeline)
        배치로 모음                  Redis (1회 RTT)
          ↓
        Celery Beat: flush_to_external() → DjangoAuditAdapter.log_batch()
          ↓                                ↓ (단일 bulk_create)
        10초마다                          PostgreSQL
```

### 2.2 수정 대상 파일

| 파일 | 변경 내용 |
|-----|----------|
| `adapters/audit/redis_buffer.py` | `log_batch()` 메서드 추가 |
| `shopping/tasks.py` (신규) | Celery Beat 플러시 태스크 |
| `shopping/celeryconfig.py` | Beat 스케줄 추가 |

---

## 3. 구현 상세

### 3.1 RedisAuditBuffer.log_batch() 추가

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/audit/redis_buffer.py`

```python
class RedisAuditBuffer(BufferedAuditAdapter):
    # 기존 log() 유지 (단일 이벤트용)

    def log_batch(self, entries: list[AuditEntry]) -> None:
        """
        배치 로깅 - 단일 Redis pipeline으로 여러 이벤트 저장.

        성능:
        - Before: N개 이벤트 × 1ms RTT = N ms
        - After: N개 이벤트 × 1회 RTT = 1ms

        Args:
            entries: 저장할 AuditEntry 리스트

        Raises:
            RedisError: Redis 연결 실패 시 (Fail-open 처리)
        """
        if not entries:
            return

        with self._lock:
            try:
                pipe = self._redis.pipeline(transaction=True)

                # 소스별로 그룹핑
                entries_by_source: dict[str, list[str]] = {}
                for entry in entries:
                    key = self._build_key(entry.source)
                    if key not in entries_by_source:
                        entries_by_source[key] = []
                    entries_by_source[key].append(entry.to_json())

                # 각 키에 대해 LPUSH + LTRIM
                for key, entry_jsons in entries_by_source.items():
                    # 여러 항목을 한번에 LPUSH
                    pipe.lpush(key, *entry_jsons)
                    pipe.ltrim(key, 0, self._max_entries - 1)

                # ✅ 단 1회 execute() = 단 1회 RTT!
                pipe.execute()

                self._metrics.increment(
                    "redis_batch_writes",
                    tags={"count": len(entries)}
                )

            except redis.RedisError as e:
                # Fail-open: 로깅 실패가 비즈니스 로직에 영향 없음
                logger.warning(
                    f"[RedisAuditBuffer] Batch log failed: {e}, "
                    f"entries_count={len(entries)}"
                )
                self._metrics.increment("redis_batch_errors")

                # 폴백: 메모리 버퍼에 임시 저장
                self._store_in_fallback_buffer(entries)

    def _store_in_fallback_buffer(self, entries: list[AuditEntry]) -> None:
        """Redis 실패 시 메모리 폴백 버퍼."""
        with self._fallback_lock:
            self._fallback_buffer.extend(entries)

            # 폴백 버퍼도 제한 (메모리 보호)
            if len(self._fallback_buffer) > self._max_fallback:
                # 오래된 항목 제거
                self._fallback_buffer = self._fallback_buffer[-self._max_fallback:]
```

---

### 3.2 Celery Beat 플러시 태스크

**파일**: `shopping/tasks.py` (신규 또는 기존 파일에 추가)

```python
from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


@shared_task(
    bind=True,
    name="shopping.tasks.flush_redis_audit_buffer",
    max_retries=3,
    default_retry_delay=30,
    autoretry_for=(Exception,),
    acks_late=True,  # 완료 후 ACK
)
def flush_redis_audit_buffer(self) -> dict:
    """
    Redis 감사 버퍼 → PostgreSQL 플러시.

    Celery Beat에서 10초마다 실행.
    대량 이벤트를 배치로 처리하여 DB 부하 분산.

    Returns:
        {"flushed_count": int, "duration_ms": float}
    """
    import time
    from selfhealing.adapters.audit.redis_buffer import RedisAuditBuffer
    from selfhealing.adapters.audit.django_adapter import DjangoAuditAdapter

    start = time.time()

    try:
        redis_buffer = RedisAuditBuffer.get_instance()
        db_adapter = DjangoAuditAdapter.get_instance()

        flushed_count = redis_buffer.flush_to_external(
            adapter=db_adapter,
            batch_size=500,  # DB 배치 크기
            delete_after=True,
        )

        duration_ms = (time.time() - start) * 1000

        logger.info(
            f"[flush_redis_audit_buffer] Flushed {flushed_count} entries "
            f"in {duration_ms:.1f}ms"
        )

        return {
            "flushed_count": flushed_count,
            "duration_ms": duration_ms,
        }

    except Exception as e:
        logger.error(f"[flush_redis_audit_buffer] Failed: {e}")
        raise self.retry(exc=e)


@shared_task(
    bind=True,
    name="shopping.tasks.flush_redis_audit_to_kafka",
    max_retries=3,
)
def flush_redis_audit_to_kafka(self) -> dict:
    """
    Redis 감사 버퍼 → Kafka 플러시 (고속 처리용).

    Kafka 사용 시 DB 대신 이 태스크 활성화.
    """
    import time
    from selfhealing.adapters.audit.redis_buffer import RedisAuditBuffer
    from selfhealing.adapters.audit.kafka_adapter import KafkaAuditAdapter

    start = time.time()

    try:
        redis_buffer = RedisAuditBuffer.get_instance()
        kafka_adapter = KafkaAuditAdapter.get_instance()

        flushed_count = redis_buffer.flush_to_external(
            adapter=kafka_adapter,
            batch_size=1000,  # Kafka는 더 큰 배치 가능
            delete_after=True,
        )

        duration_ms = (time.time() - start) * 1000

        return {
            "flushed_count": flushed_count,
            "duration_ms": duration_ms,
        }

    except Exception as e:
        logger.error(f"[flush_redis_audit_to_kafka] Failed: {e}")
        raise self.retry(exc=e)
```

---

### 3.3 Celery Beat 스케줄 추가

**파일**: `shopping/celeryconfig.py` 또는 Django `settings.py`

```python
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    # ... 기존 스케줄 ...

    # Redis Audit 버퍼 플러시 (10초마다)
    "flush-redis-audit-buffer": {
        "task": "shopping.tasks.flush_redis_audit_buffer",
        "schedule": 10.0,  # 10초마다
        "options": {
            "queue": "audit_flush",  # 전용 큐
            "expires": 30,  # 30초 후 만료 (중복 방지)
        },
    },

    # 또는 Kafka 사용 시 (선택)
    # "flush-redis-audit-to-kafka": {
    #     "task": "shopping.tasks.flush_redis_audit_to_kafka",
    #     "schedule": 5.0,  # Kafka는 더 자주 (5초)
    # },
}
```

---

### 3.4 전용 Celery 워커 큐

**파일**: `k8s/celery-audit-worker.yaml` (신규)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: celery-audit-worker
  labels:
    app: celery-audit-worker
spec:
  replicas: 2  # 플러시 전용 2개
  selector:
    matchLabels:
      app: celery-audit-worker
  template:
    metadata:
      labels:
        app: celery-audit-worker
    spec:
      containers:
        - name: celery-audit-worker
          image: myproject:latest
          command:
            - celery
            - -A
            - shopping
            - worker
            - --loglevel=info
            - --concurrency=4
            - --queues=audit_flush  # 전용 큐
          resources:
            requests:
              cpu: "100m"
              memory: "256Mi"
            limits:
              cpu: "500m"
              memory: "512Mi"
          env:
            - name: CELERY_BROKER_URL
              valueFrom:
                secretKeyRef:
                  name: celery-secrets
                  key: broker-url
```

---

## 4. 테스트 계획

### 4.1 단위 테스트

**파일**: `tests/unit/audit/test_redis_batch.py`

```python
import pytest
from unittest.mock import Mock, patch, MagicMock
import fakeredis


class TestRedisAuditBufferBatch:
    """RedisAuditBuffer 배치 처리 테스트."""

    @pytest.fixture
    def fake_redis(self):
        return fakeredis.FakeRedis()

    @pytest.fixture
    def buffer(self, fake_redis):
        from selfhealing.adapters.audit.redis_buffer import RedisAuditBuffer

        with patch.object(RedisAuditBuffer, "_redis", fake_redis):
            return RedisAuditBuffer()

    def test_log_batch_single_pipeline_call(self, buffer, fake_redis):
        """배치가 단일 pipeline으로 처리되는지 확인."""
        from selfhealing.interfaces.audit_adapter import AuditEntry

        entries = [
            AuditEntry(action="test", source="app", target_type="order", target_id="1"),
            AuditEntry(action="test", source="app", target_type="order", target_id="2"),
            AuditEntry(action="test", source="app", target_type="order", target_id="3"),
        ]

        # pipeline 호출 횟수 추적
        original_pipeline = fake_redis.pipeline
        call_count = [0]

        def tracked_pipeline(*args, **kwargs):
            call_count[0] += 1
            return original_pipeline(*args, **kwargs)

        fake_redis.pipeline = tracked_pipeline

        buffer.log_batch(entries)

        # 3개 이벤트 → 1회 pipeline 호출
        assert call_count[0] == 1

    def test_log_batch_performance(self, buffer, fake_redis):
        """배치 처리 성능 테스트."""
        from selfhealing.interfaces.audit_adapter import AuditEntry
        import time

        entries = [
            AuditEntry(action="test", source="app", target_type="order", target_id=str(i))
            for i in range(1000)
        ]

        start = time.time()
        buffer.log_batch(entries)
        elapsed = time.time() - start

        # 1000개 배치가 100ms 이내
        assert elapsed < 0.1, f"1000 entries took {elapsed}s (should be < 0.1s)"

    def test_log_batch_fallback_on_redis_error(self, buffer):
        """Redis 오류 시 폴백 버퍼 사용."""
        from selfhealing.interfaces.audit_adapter import AuditEntry

        entries = [
            AuditEntry(action="test", source="app", target_type="order", target_id="1"),
        ]

        # Redis 오류 시뮬레이션
        with patch.object(buffer._redis, "pipeline", side_effect=Exception("Redis down")):
            buffer.log_batch(entries)

        # 폴백 버퍼에 저장됨
        assert len(buffer._fallback_buffer) == 1
```

---

### 4.2 Celery 태스크 테스트

**파일**: `tests/unit/tasks/test_flush_redis_audit.py`

```python
import pytest
from unittest.mock import Mock, patch


class TestFlushRedisAuditTask:
    """Redis 플러시 Celery 태스크 테스트."""

    def test_flush_task_calls_flush_to_external(self):
        """flush_to_external() 호출 확인."""
        from shopping.tasks import flush_redis_audit_buffer

        mock_redis_buffer = Mock()
        mock_redis_buffer.flush_to_external.return_value = 100

        mock_db_adapter = Mock()

        with patch(
            "shopping.tasks.RedisAuditBuffer.get_instance",
            return_value=mock_redis_buffer,
        ), patch(
            "shopping.tasks.DjangoAuditAdapter.get_instance",
            return_value=mock_db_adapter,
        ):
            result = flush_redis_audit_buffer()

        assert result["flushed_count"] == 100
        mock_redis_buffer.flush_to_external.assert_called_once_with(
            adapter=mock_db_adapter,
            batch_size=500,
            delete_after=True,
        )

    def test_flush_task_retries_on_error(self):
        """오류 시 재시도 확인."""
        from shopping.tasks import flush_redis_audit_buffer

        with patch(
            "shopping.tasks.RedisAuditBuffer.get_instance",
            side_effect=Exception("Redis down"),
        ):
            with pytest.raises(Exception):
                flush_redis_audit_buffer()
```

---

## 5. 환경 변수

| 변수명 | 기본값 | 설명 | 동적 변경 |
|-------|-------|------|---------|
| `SELFHEALING_REDIS_BATCH_SIZE` | 500 | Redis → DB 배치 크기 | ✅ RuntimeConfig |
| `SELFHEALING_REDIS_FLUSH_INTERVAL` | 10 | 플러시 주기 (초) | ✅ RuntimeConfig |
| `SELFHEALING_REDIS_MAX_FALLBACK` | 10000 | 폴백 버퍼 최대 크기 | ❌ |
| `SELFHEALING_MAX_PIPELINE_CHUNK` | 1000 | 단일 파이프라인 청크 크기 (v2.0.0) | ❌ |
| `SELFHEALING_BUFFER_WARNING` | 10000 | 버퍼 경고 임계치 (v2.0.0) | ✅ RuntimeConfig |
| `SELFHEALING_BUFFER_CRITICAL` | 50000 | 버퍼 위험 임계치 (v2.0.0) | ✅ RuntimeConfig |
| `SELFHEALING_SAFETY_LTRIM` | 100000 | Safety LTRIM 임계치 (v2.0.0) | ❌ |
| `SELFHEALING_WAL_PATH` | /var/log/selfhealing/audit_wal | WAL 파일 경로 (v2.0.0) | ❌ |

---

## 6. 성능 비교

### 6.1 Before (개별 처리)

```
이벤트 1000개:
  - log() × 1000 = 1000회 Redis RTT
  - RTT 1ms × 1000 = 1000ms (1초)
  - 처리량: 1,000 events/sec
```

### 6.2 After (배치 처리)

```
이벤트 1000개:
  - log_batch(1000) = 1회 Redis RTT
  - RTT 1ms × 1 = 1ms
  - 처리량: 1,000,000 events/sec (이론상)
  - 실제: 50,000-100,000 events/sec (serialization 포함)
```

---

## 7. 마이그레이션 체크리스트

### 7.1 코드 변경

- [x] `redis_buffer.py`: `log_batch()` 메서드 추가
- [x] `redis_buffer.py`: 폴백 버퍼 로직 추가
- [x] `redis_buffer.py`: Processing Queue 패턴 (`flush_to_external_safe()`)
- [x] `redis_buffer.py`: ActiveKeySet O(1) 도메인 조회
- [x] `redis_buffer.py`: 청킹 구현 (`_log_batch_chunk()`)
- [x] `redis_buffer.py`: Safety LTRIM (`apply_safety_ltrim()`)
- [x] `redis_buffer.py`: Graceful Shutdown Hook
- [x] `audit/redis_batch_lua.py`: Lua 스크립트 모듈 추가
- [x] `metrics/audit_buffer_metrics.py`: Backpressure 메트릭
- [x] `celery_tasks/audit_flush_tasks.py`: Celery 플러시 태스크 추가

### 7.2 인프라

- [x] `k8s/celery-audit-worker.yaml`: 전용 워커 배포
- [ ] Redis 모니터링: 키 사이즈 추적
- [ ] Celery 모니터링: 태스크 실행 시간/실패율

### 7.3 테스트

- [x] 단위 테스트: 배치 처리, 폴백, v2.0 기능 (19개 통과)
- [ ] 통합 테스트: Redis → DB 플러시
- [ ] 부하 테스트: 10,000 events/sec 검증

---

## 8. 롤백 계획

문제 발생 시:

```python
# redis_buffer.py 수정
class RedisAuditBuffer:
    def log_batch(self, entries: list[AuditEntry]) -> None:
        # 배치 실패 시 개별 처리로 폴백
        try:
            self._log_batch_optimized(entries)
        except Exception as e:
            logger.warning(f"Batch failed, falling back to individual: {e}")
            for entry in entries:
                self.log(entry)  # 기존 방식
```

---

## 9. 보완 사항 (v2.0.0)

> **리뷰 반영**: 11건의 Q&A 리뷰 및 3건의 추가 보완사항

### 9.1 [P0] 원자적 Processing Queue 패턴 (Q1 & Q2 해결)

#### 9.1.1 문제

현재 `flush_to_external()` 구현에서 RPOP 후 실패 시 `rpush()` 복원이 **순서 역전** 문제를 일으킴:

```python
# 현재 문제점: 순서 역전
# 원래 순서: [1, 2, 3, 4, 5]
# RPOP: 5 가져옴
# DB 저장 실패
# rpush(5): [1, 2, 3, 4, 5]  # 끝에 추가됨
# 결과: 다음 RPOP 시 5가 다시 처리됨 (정상)
# BUT: 부분 실패 시 순서가 뒤섞임
```

#### 9.1.2 해결책: RPOPLPUSH + Processing Queue

**기존 ThrottleLuaScripts 패턴 활용** (`services/throttle/redis_lua.py` 참조)

**파일**: `packages/selfhealing-python/src/selfhealing/audit/redis_batch_lua.py`

```python
"""
Redis Audit 배치 처리용 Lua 스크립트.

원자적 Processing Queue 패턴으로 데이터 손실 방지.
ThrottleLuaScripts 패턴 참조: services/throttle/redis_lua.py

Reference:
    docs/self_healing/middleware_system/168_REDIS_BATCH_OPTIMIZATION.md#9.1
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis import Redis

logger = logging.getLogger(__name__)


class AuditBatchLuaScripts:
    """
    Audit 배치 처리용 Lua 스크립트.

    모든 스크립트는 원자적으로 실행되어 Race Condition과 데이터 손실을 방지.
    """

    # ==========================================================================
    # Lua Script: 원자적 배치 이동 (Buffer → Processing Queue)
    # ==========================================================================
    LUA_ATOMIC_BATCH_MOVE = """
    -- KEYS[1] = audit:buffer:{domain}
    -- KEYS[2] = audit:processing:{domain}
    -- ARGV[1] = batch_size
    -- ARGV[2] = worker_id (처리 워커 식별)

    local batch_size = tonumber(ARGV[1])
    local worker_id = ARGV[2]
    local moved = 0

    -- 원자적으로 batch_size만큼 이동
    for i = 1, batch_size do
        local item = redis.call('RPOPLPUSH', KEYS[1], KEYS[2])
        if not item then
            break
        end
        moved = moved + 1
    end

    -- 처리 워커 정보 저장 (타임아웃 복구용)
    if moved > 0 then
        redis.call('HSET', 'audit:processing:meta',
                   KEYS[2], worker_id .. ':' .. redis.call('TIME')[1])
    end

    return moved
    """

    # ==========================================================================
    # Lua Script: 처리 완료 후 Processing Queue 정리
    # ==========================================================================
    LUA_ATOMIC_BATCH_COMPLETE = """
    -- KEYS[1] = audit:processing:{domain}
    -- ARGV[1] = count (처리 완료된 항목 수)

    local count = tonumber(ARGV[1])
    local removed = 0

    for i = 1, count do
        local item = redis.call('RPOP', KEYS[1])
        if not item then
            break
        end
        removed = removed + 1
    end

    -- 메타 정보 정리 (Processing Queue가 비었으면)
    if redis.call('LLEN', KEYS[1]) == 0 then
        redis.call('HDEL', 'audit:processing:meta', KEYS[1])
    end

    return removed
    """

    # ==========================================================================
    # Lua Script: 실패 시 원래 큐로 복원 (순서 보존)
    # ==========================================================================
    LUA_ATOMIC_BATCH_RESTORE = """
    -- KEYS[1] = audit:processing:{domain}
    -- KEYS[2] = audit:buffer:{domain}
    -- 실패 시 processing → buffer 끝(오른쪽)으로 복원
    -- RPOPLPUSH 역순으로 순서 보존

    local restored = 0

    while true do
        local item = redis.call('LPOP', KEYS[1])  -- 왼쪽에서 꺼내서
        if not item then
            break
        end
        redis.call('RPUSH', KEYS[2], item)  -- 오른쪽에 추가 (순서 보존)
        restored = restored + 1
    end

    redis.call('HDEL', 'audit:processing:meta', KEYS[1])

    return restored
    """

    def __init__(self, redis_client: "Redis"):
        self._redis = redis_client
        self._scripts: dict[str, str] = {}
        self._register_scripts()

    def _register_scripts(self) -> None:
        """스크립트를 Redis에 등록하고 SHA 캐싱."""
        self._scripts["batch_move"] = self._redis.script_load(
            self.LUA_ATOMIC_BATCH_MOVE
        )
        self._scripts["batch_complete"] = self._redis.script_load(
            self.LUA_ATOMIC_BATCH_COMPLETE
        )
        self._scripts["batch_restore"] = self._redis.script_load(
            self.LUA_ATOMIC_BATCH_RESTORE
        )
        logger.info("Audit batch Lua scripts registered")

    def atomic_batch_move(
        self, domain: str, batch_size: int, worker_id: str
    ) -> int:
        """Buffer에서 Processing Queue로 원자적 이동."""
        buffer_key = f"audit:buffer:{domain}"
        processing_key = f"audit:processing:{domain}"

        return self._redis.evalsha(
            self._scripts["batch_move"],
            2,  # numkeys
            buffer_key,
            processing_key,
            batch_size,
            worker_id,
        )

    def atomic_batch_complete(self, domain: str, count: int) -> int:
        """처리 완료 후 Processing Queue 정리."""
        processing_key = f"audit:processing:{domain}"

        return self._redis.evalsha(
            self._scripts["batch_complete"],
            1,
            processing_key,
            count,
        )

    def atomic_batch_restore(self, domain: str) -> int:
        """실패 시 원래 Buffer로 복원."""
        processing_key = f"audit:processing:{domain}"
        buffer_key = f"audit:buffer:{domain}"

        return self._redis.evalsha(
            self._scripts["batch_restore"],
            2,
            processing_key,
            buffer_key,
        )
```

#### 9.1.3 flush_to_external() 개선

```python
# redis_buffer.py 개선
from selfhealing.audit.redis_batch_lua import AuditBatchLuaScripts

class RedisAuditBuffer(BufferedAuditAdapter):
    def __init__(self, ...):
        super().__init__(...)
        self._lua_scripts = AuditBatchLuaScripts(self._redis)
        self._worker_id = f"{socket.gethostname()}-{os.getpid()}"

    def flush_to_external(
        self,
        adapter: AuditAdapter,
        batch_size: int = 500,
        delete_after: bool = True,
    ) -> int:
        """Processing Queue 패턴 적용된 안전한 플러시."""
        total_flushed = 0

        for domain in self._get_active_domains():
            try:
                # 1. 원자적 이동: Buffer → Processing Queue
                moved = self._lua_scripts.atomic_batch_move(
                    domain=domain,
                    batch_size=batch_size,
                    worker_id=self._worker_id,
                )

                if moved == 0:
                    continue

                # 2. Processing Queue에서 데이터 읽기 (삭제 X)
                processing_key = f"audit:processing:{domain}"
                items = self._redis.lrange(processing_key, 0, moved - 1)

                entries = [AuditEntry.from_json(item) for item in items]

                # 3. 외부 어댑터로 저장
                adapter.log_batch(entries)

                # 4. 성공 시 Processing Queue 정리
                self._lua_scripts.atomic_batch_complete(domain, moved)
                total_flushed += moved

            except Exception as e:
                logger.error(f"Flush failed for {domain}: {e}")
                # 5. 실패 시 순서 보존하여 복원
                restored = self._lua_scripts.atomic_batch_restore(domain)
                logger.info(f"Restored {restored} items to buffer")

        return total_flushed
```

#### 9.1.4 데이터 흐름 다이어그램

```
┌──────────────────────────────────────────────────────────────┐
│                    Processing Queue 패턴                      │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  [Buffer Queue]        [Processing Queue]       [Database]  │
│  ┌─────────────┐      ┌─────────────────┐     ┌──────────┐ │
│  │ 1, 2, 3, 4  │──1──▶│    (empty)      │     │          │ │
│  └─────────────┘      └─────────────────┘     └──────────┘ │
│                                                              │
│  ┌─────────────┐      ┌─────────────────┐                   │
│  │    1, 2     │      │    3, 4 ◀──────────────────────┐   │
│  └─────────────┘      └─────────────────┘              │   │
│        │                      │                        │   │
│        │              RPOPLPUSH (Lua)                  │   │
│        │              원자적 이동                        │   │
│        │                      │                        │   │
│        ▼                      ▼                        │   │
│  ┌─────────────┐      ┌─────────────────┐     ┌──────────┐ │
│  │    1, 2     │──2──▶│     3, 4        │──3──▶│  3, 4   │ │
│  └─────────────┘      └─────────────────┘     └──────────┘ │
│                              │                     │       │
│                              │ Success             │       │
│                              ▼                     │       │
│                       ┌─────────────────┐          │       │
│                  ──4──▶│   (cleared)     │◀─────────┘       │
│                       └─────────────────┘                   │
│                                                              │
│  ※ 실패 시: Processing Queue → Buffer 끝으로 복원 (순서 보존) │
└──────────────────────────────────────────────────────────────┘
```

---

### 9.2 [P0] 분산 락 통합 (Q7 해결)

#### 9.2.1 문제

여러 Celery 워커가 동시에 `flush_redis_audit_buffer` 태스크를 실행하면 동일 데이터 중복 처리 가능.

#### 9.2.2 해결책: DistributedRecoveryLock 래핑

**기존 DistributedRecoveryLock 패턴 활용** (`services/coordination/distributed_recovery_lock.py` 참조)

```python
# shopping/tasks.py 개선
from selfhealing.services.coordination.distributed_recovery_lock import (
    DistributedRecoveryLock,
    RecoveryLockError,
)

@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    autoretry_for=(Exception,),
)
def flush_redis_audit_buffer(self):
    """
    분산 락으로 보호되는 Redis audit buffer 플러시 태스크.

    동시 실행 방지:
    - 동일 시점에 하나의 워커만 플러시 수행
    - 락 획득 실패 시 조기 종료 (재시도 X)
    """
    redis_buffer = RedisAuditBuffer.get_instance()
    db_adapter = DjangoAuditAdapter.get_instance()
    lock = DistributedRecoveryLock(redis_buffer._redis)

    lock_namespace = "audit-buffer-flush"
    session_id = f"celery-{self.request.id}"

    # 락 획득 시도 (non-blocking)
    acquired = lock.acquire(
        namespace=lock_namespace,
        session_id=session_id,
        timeout=timedelta(minutes=5),  # 5분 TTL
        blocking=False,
    )

    if not acquired:
        logger.info(
            "Flush task skipped - another worker is processing",
            extra={"task_id": self.request.id},
        )
        return {"status": "skipped", "reason": "lock_not_acquired"}

    try:
        flushed_count = redis_buffer.flush_to_external(
            adapter=db_adapter,
            batch_size=500,
            delete_after=True,
        )

        return {
            "status": "success",
            "flushed_count": flushed_count,
            "worker_id": session_id,
        }

    finally:
        # 락 해제 (원자적, Lua 스크립트)
        lock.release(namespace=lock_namespace, session_id=session_id)
```

#### 9.2.3 도메인별 락 (선택적 세분화)

대량 트래픽 환경에서는 도메인별로 락을 분리하여 병렬성 확보:

```python
def flush_redis_audit_buffer_by_domain(self, domain: str):
    """도메인별 분산 락."""
    lock_namespace = f"audit-buffer-flush:{domain}"
    # ... 도메인별 병렬 처리 가능
```

---

### 9.3 [P1] WAL 통합 (Q3 해결)

#### 9.3.1 문제

현재 `_fallback_buffer`는 순수 메모리 리스트로, 프로세스 재시작 시 손실됨.

#### 9.3.2 해결책: BufferedWAL 패턴 연동

**166번 문서의 WriteAheadLog 패턴 활용** (166_RINGBUFFER_AUDIT_INTEGRATION.md 참조)

```python
# redis_buffer.py 개선
from selfhealing.audit.wal import WriteAheadLog  # 166번 문서

class RedisAuditBuffer(BufferedAuditAdapter):
    def __init__(self, ...):
        super().__init__(...)

        # WAL 초기화 (fallback 영속성 보장)
        self._wal = WriteAheadLog(
            path=Path(os.getenv("SELFHEALING_WAL_PATH", "/var/log/selfhealing/audit_wal")),
            sync_on_write=True,  # 중요 데이터는 즉시 fsync
            max_size_mb=100,
        )

        # 시작 시 WAL에서 미처리 항목 복구
        self._recover_from_wal()

    def _store_in_fallback_buffer(self, entry: AuditEntry) -> None:
        """폴백 버퍼 저장 + WAL 영속화."""
        if len(self._fallback_buffer) >= self._max_fallback:
            logger.warning("Fallback buffer full, dropping oldest entry")
            dropped = self._fallback_buffer.popleft()
            self._wal.mark_committed(dropped.sequence_number)  # WAL 정리

        # 메모리 버퍼에 저장
        self._fallback_buffer.append(entry)

        # WAL에 영속화 (크래시 복구용)
        self._wal.append(entry.to_json())

    def _recover_from_wal(self) -> None:
        """WAL에서 미처리 항목 복구 (프로세스 재시작 시)."""
        uncommitted = self._wal.get_uncommitted_entries()

        if uncommitted:
            logger.info(f"Recovering {len(uncommitted)} entries from WAL")
            for entry_json in uncommitted:
                entry = AuditEntry.from_json(entry_json)
                self._fallback_buffer.append(entry)

    def flush_fallback_buffer(self, adapter: AuditAdapter) -> int:
        """폴백 버퍼 플러시 + WAL 정리."""
        entries = list(self._fallback_buffer)

        if not entries:
            return 0

        try:
            adapter.log_batch(entries)

            # WAL에서 커밋 마킹
            for entry in entries:
                self._wal.mark_committed(entry.sequence_number)

            self._fallback_buffer.clear()
            return len(entries)

        except Exception as e:
            logger.error(f"Fallback flush failed: {e}")
            # WAL 덕분에 재시작 후 복구 가능
            raise
```

---

### 9.4 [P1] ActiveKeySet 최적화 (Q4 해결)

#### 9.4.1 문제

`scan_iter("audit:buffer:*")`는 O(N) 복잡도로, 키가 많아지면 성능 저하.

#### 9.4.2 해결책: Redis SET으로 O(1) 키 조회

```python
# redis_buffer.py 개선
class RedisAuditBuffer(BufferedAuditAdapter):
    ACTIVE_KEYS_SET = "audit:active_domains"  # 활성 도메인 SET

    def log_batch(self, entries: list[AuditEntry]) -> None:
        """배치 로깅 시 ActiveKeySet 업데이트."""
        pipe = self._redis.pipeline(transaction=True)

        domains = set()
        for entry in entries:
            key = self._build_key(entry.source)
            pipe.lpush(key, entry.to_json())
            domains.add(entry.source)

        # 활성 도메인 SET에 추가
        if domains:
            pipe.sadd(self.ACTIVE_KEYS_SET, *domains)
            pipe.expire(self.ACTIVE_KEYS_SET, 86400)  # 24시간 TTL

        pipe.execute()

    def _get_active_domains(self) -> list[str]:
        """O(1) 복잡도의 활성 도메인 조회."""
        domains = self._redis.smembers(self.ACTIVE_KEYS_SET)

        # 빈 도메인 정리 (LLEN으로 확인)
        empty_domains = []
        for domain in domains:
            key = self._build_key(domain)
            if self._redis.llen(key) == 0:
                empty_domains.append(domain)

        if empty_domains:
            self._redis.srem(self.ACTIVE_KEYS_SET, *empty_domains)

        return [d for d in domains if d not in empty_domains]

    # 기존 scan_iter 방식 (fallback)
    def _get_active_domains_fallback(self) -> list[str]:
        """scan_iter 기반 fallback (ActiveKeySet 사용 불가 시)."""
        domains = set()
        for key in self._redis.scan_iter("audit:buffer:*"):
            # audit:buffer:{domain} 에서 domain 추출
            domain = key.decode().split(":", 2)[2]
            domains.add(domain)
        return list(domains)
```

---

### 9.5 [P1] 청킹 구현 (Q5 해결)

#### 9.5.1 문제

`entries[:batch_size]`가 매우 크면 단일 pipeline이 Redis 메모리/네트워크 부담.

#### 9.5.2 해결책: MAX_PIPELINE_CHUNK 분할

```python
# redis_buffer.py 개선
import os

class RedisAuditBuffer(BufferedAuditAdapter):
    MAX_PIPELINE_CHUNK = int(os.getenv("SELFHEALING_MAX_PIPELINE_CHUNK", "1000"))

    def log_batch(self, entries: list[AuditEntry]) -> None:
        """청킹 적용된 배치 로깅."""
        total = len(entries)

        for chunk_start in range(0, total, self.MAX_PIPELINE_CHUNK):
            chunk_end = min(chunk_start + self.MAX_PIPELINE_CHUNK, total)
            chunk = entries[chunk_start:chunk_end]

            try:
                self._log_batch_chunk(chunk)
            except Exception as e:
                logger.error(
                    f"Chunk {chunk_start}-{chunk_end} failed: {e}",
                    extra={"total": total, "chunk_size": len(chunk)},
                )
                # 실패한 청크만 폴백
                for entry in chunk:
                    self._store_in_fallback_buffer(entry)

    def _log_batch_chunk(self, entries: list[AuditEntry]) -> None:
        """단일 청크 처리 (최대 MAX_PIPELINE_CHUNK 개)."""
        pipe = self._redis.pipeline(transaction=True)
        domains = set()

        for entry in entries:
            key = self._build_key(entry.source)
            pipe.lpush(key, entry.to_json())
            domains.add(entry.source)

        if domains:
            pipe.sadd(self.ACTIVE_KEYS_SET, *domains)

        pipe.execute()
        logger.debug(f"Batch chunk logged: {len(entries)} entries")
```

---

### 9.6 [P1] 모니터링 알림 (Q9 해결)

#### 9.6.1 문제

LLEN 모니터링은 있지만 임계치 초과 시 자동 알림 없음.

#### 9.6.2 해결책: UnifiedNotificationManager 연동

```python
# redis_buffer.py 개선
from selfhealing.services.notification.unified_notification import (
    UnifiedNotificationManager,
    NotificationPriority,
    NotificationCategory,
)

class RedisAuditBuffer(BufferedAuditAdapter):
    BUFFER_WARNING_THRESHOLD = int(os.getenv("SELFHEALING_BUFFER_WARNING", "10000"))
    BUFFER_CRITICAL_THRESHOLD = int(os.getenv("SELFHEALING_BUFFER_CRITICAL", "50000"))

    def get_buffer_stats(self) -> dict[str, int]:
        """버퍼 통계 조회 + 알림 발송."""
        stats = {}
        total_size = 0
        notification_manager = UnifiedNotificationManager.get_instance()

        for domain in self._get_active_domains():
            key = self._build_key(domain)
            size = self._redis.llen(key)
            stats[domain] = size
            total_size += size

            # 도메인별 임계치 체크
            if size >= self.BUFFER_CRITICAL_THRESHOLD:
                notification_manager.notify(
                    title=f"[CRITICAL] Audit Buffer Overflow: {domain}",
                    message=f"도메인 '{domain}'의 버퍼 크기가 {size:,}개로 임계치({self.BUFFER_CRITICAL_THRESHOLD:,}) 초과",
                    priority=NotificationPriority.HIGH,
                    category=NotificationCategory.PERFORMANCE,
                    metadata={
                        "domain": domain,
                        "buffer_size": size,
                        "threshold": self.BUFFER_CRITICAL_THRESHOLD,
                        "action_required": "flush_task_확인_또는_batch_size_증가",
                    },
                )
            elif size >= self.BUFFER_WARNING_THRESHOLD:
                notification_manager.notify(
                    title=f"[WARNING] Audit Buffer High: {domain}",
                    message=f"도메인 '{domain}'의 버퍼 크기가 {size:,}개로 경고 수준",
                    priority=NotificationPriority.MEDIUM,
                    category=NotificationCategory.PERFORMANCE,
                    metadata={"domain": domain, "buffer_size": size},
                )

        stats["_total"] = total_size
        return stats
```

---

### 9.7 [P2] LTRIM Safety Net (Q6 해결)

#### 9.7.1 문제

TTL 만료 전에 버퍼가 과도하게 커질 수 있음.

#### 9.7.2 해결책: LLEN 기반 사후 LTRIM

```python
# redis_buffer.py 개선
class RedisAuditBuffer(BufferedAuditAdapter):
    SAFETY_LTRIM_THRESHOLD = int(os.getenv("SELFHEALING_SAFETY_LTRIM", "100000"))

    def _apply_safety_ltrim(self) -> None:
        """버퍼가 과도하게 커지면 사후 LTRIM 적용."""
        for domain in self._get_active_domains():
            key = self._build_key(domain)
            size = self._redis.llen(key)

            if size > self.SAFETY_LTRIM_THRESHOLD:
                # 최신 SAFETY_LTRIM_THRESHOLD개만 유지
                self._redis.ltrim(key, 0, self.SAFETY_LTRIM_THRESHOLD - 1)
                logger.warning(
                    f"Safety LTRIM applied to {domain}: {size} → {self.SAFETY_LTRIM_THRESHOLD}",
                    extra={
                        "domain": domain,
                        "original_size": size,
                        "trimmed_to": self.SAFETY_LTRIM_THRESHOLD,
                    },
                )

                # Prometheus 메트릭 기록
                self._metrics.increment(
                    "audit_buffer_safety_ltrim_total",
                    labels={"domain": domain},
                )
```

---

### 9.8 [P2] 시퀀스 번호 필드 (Q10 해결)

#### 9.8.1 문제

AuditEntry에 순서 보장 필드 없음.

#### 9.8.2 해결책: WAL sequence_number 재사용

```python
# interfaces/audit_adapter.py 개선
from selfhealing.audit.wal import WriteAheadLog

@dataclass
class AuditEntry:
    action: str
    source: str
    target_type: str
    target_id: str
    timestamp: datetime = field(default_factory=datetime.now)

    # 신규: 전역 순서 보장
    sequence_number: int | None = None  # WAL에서 할당

    def __post_init__(self):
        # WAL 시퀀스 번호 자동 할당
        if self.sequence_number is None:
            self.sequence_number = WriteAheadLog.next_sequence()

# wal.py 개선
class WriteAheadLog:
    _sequence_counter = 0
    _sequence_lock = threading.Lock()

    @classmethod
    def next_sequence(cls) -> int:
        """전역 시퀀스 번호 발급 (thread-safe)."""
        with cls._sequence_lock:
            cls._sequence_counter += 1
            return cls._sequence_counter
```

---

### 9.9 [P3] RuntimeConfig 통합 (Q11 해결)

#### 9.9.1 문제

환경변수는 정적으로, 운영 중 변경 불가.

#### 9.9.2 해결책: 운영값만 RuntimeConfigManager 연동

> **설계 결정**: 보안 관련 설정(Redis URL 등)은 환경변수 유지, 운영 튜닝값만 동적 설정

```python
# redis_buffer.py 개선
from selfhealing.settings.runtime_config import RuntimeConfigManager

class RedisAuditBuffer(BufferedAuditAdapter):
    def __init__(self, ...):
        super().__init__(...)
        self._runtime_config = RuntimeConfigManager.get_instance()

    @property
    def batch_size(self) -> int:
        """동적 배치 크기 조회."""
        return self._runtime_config.get(
            "audit.buffer.batch_size",
            default=int(os.getenv("SELFHEALING_REDIS_BATCH_SIZE", "500")),
        )

    @property
    def flush_interval(self) -> int:
        """동적 플러시 주기 조회."""
        return self._runtime_config.get(
            "audit.buffer.flush_interval",
            default=int(os.getenv("SELFHEALING_REDIS_FLUSH_INTERVAL", "10")),
        )

    @property
    def buffer_warning_threshold(self) -> int:
        """동적 경고 임계치 조회."""
        return self._runtime_config.get(
            "audit.buffer.warning_threshold",
            default=self.BUFFER_WARNING_THRESHOLD,
        )
```

---

### 9.10 Processing Queue 복구 태스크 (추가 보완 1)

#### 9.10.1 목적

Processing Queue에 남아있는 고아 항목(타임아웃된 워커) 복구.

#### 9.10.2 구현

```python
# shopping/tasks.py
@shared_task(bind=True)
def recover_orphaned_processing_queues(self):
    """
    Processing Queue 고아 항목 복구 태스크.

    Schedule: 5분마다 (Beat)
    """
    redis_client = RedisAuditBuffer.get_instance()._redis

    # Processing Queue 메타 정보 조회
    processing_meta = redis_client.hgetall("audit:processing:meta")

    recovered_total = 0
    timeout_threshold = 300  # 5분 이상 처리 중이면 고아로 판단

    for processing_key, worker_info in processing_meta.items():
        worker_id, timestamp = worker_info.decode().rsplit(":", 1)
        age = int(time.time()) - int(timestamp)

        if age > timeout_threshold:
            logger.warning(
                f"Orphaned processing queue detected: {processing_key}",
                extra={"worker_id": worker_id, "age_seconds": age},
            )

            # 도메인 추출 및 복원
            domain = processing_key.decode().split(":")[-1]
            lua_scripts = AuditBatchLuaScripts(redis_client)
            restored = lua_scripts.atomic_batch_restore(domain)

            recovered_total += restored
            logger.info(f"Recovered {restored} orphaned items from {domain}")

    return {"recovered_total": recovered_total}
```

```python
# Beat 스케줄 추가
CELERY_BEAT_SCHEDULE = {
    # ... 기존 ...
    "recover-orphaned-processing-queues": {
        "task": "shopping.tasks.recover_orphaned_processing_queues",
        "schedule": 300.0,  # 5분마다
    },
}
```

---

### 9.11 Backpressure 메트릭 (추가 보완 2)

#### 9.11.1 목적

버퍼 압력 수준을 Prometheus로 노출하여 Grafana 대시보드/알림 연동.

#### 9.11.2 구현

```python
# metrics/audit_buffer_metrics.py
from prometheus_client import Gauge, Counter

# Gauge: 현재 버퍼 크기
audit_buffer_size = Gauge(
    "audit_buffer_size",
    "Current size of audit buffer by domain",
    ["domain"],
)

# Gauge: 백프레셔 수준 (0.0 ~ 1.0)
audit_buffer_backpressure = Gauge(
    "audit_buffer_backpressure",
    "Backpressure level of audit buffer (0.0-1.0)",
    ["domain"],
)

# Counter: 드롭된 항목 수
audit_buffer_dropped_total = Counter(
    "audit_buffer_dropped_total",
    "Total dropped audit entries due to buffer overflow",
    ["domain"],
)

# redis_buffer.py 개선
class RedisAuditBuffer(BufferedAuditAdapter):
    def _update_backpressure_metrics(self) -> None:
        """백프레셔 메트릭 업데이트."""
        for domain in self._get_active_domains():
            key = self._build_key(domain)
            size = self._redis.llen(key)

            # 메트릭 업데이트
            audit_buffer_size.labels(domain=domain).set(size)

            # 백프레셔: 현재 크기 / 최대 크기
            backpressure = min(1.0, size / self._max_entries)
            audit_buffer_backpressure.labels(domain=domain).set(backpressure)
```

---

### 9.12 Graceful Shutdown Hook (추가 보완 3)

#### 9.12.1 목적

애플리케이션 종료 시 메모리 버퍼 손실 방지.

#### 9.12.2 구현

```python
# redis_buffer.py 개선
import atexit
import signal

class RedisAuditBuffer(BufferedAuditAdapter):
    def __init__(self, ...):
        super().__init__(...)
        self._register_shutdown_hooks()

    def _register_shutdown_hooks(self) -> None:
        """종료 훅 등록."""
        atexit.register(self._graceful_shutdown)
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum, frame) -> None:
        """시그널 핸들러."""
        logger.info(f"Received signal {signum}, initiating graceful shutdown")
        self._graceful_shutdown()

    def _graceful_shutdown(self) -> None:
        """
        Graceful shutdown 처리.

        1. 메모리 fallback 버퍼 → Redis로 이동
        2. WAL sync 보장
        """
        logger.info("RedisAuditBuffer graceful shutdown started")

        try:
            # 1. 메모리 버퍼 → Redis 저장 (가능하면)
            if self._fallback_buffer:
                entries = list(self._fallback_buffer)
                logger.info(f"Flushing {len(entries)} entries from fallback buffer")

                try:
                    self.log_batch(entries)
                    self._fallback_buffer.clear()
                except Exception as e:
                    # Redis 실패 시 WAL에만 의존
                    logger.warning(f"Redis flush failed during shutdown: {e}")
                    # WAL은 이미 _store_in_fallback_buffer에서 기록됨

            # 2. WAL sync 보장
            if hasattr(self, "_wal"):
                self._wal.sync()
                logger.info("WAL synced successfully")

        except Exception as e:
            logger.error(f"Error during graceful shutdown: {e}")

        logger.info("RedisAuditBuffer graceful shutdown completed")
```

---

## 10. 다음 단계

→ [169_SETTINGS_SCALE_LIMITS.md](169_SETTINGS_SCALE_LIMITS.md): 설정 한계값 증가

---

## 변경 이력

| 버전 | 날짜 | 변경 내용 |
|-----|------|----------|
| 1.0.0 | 2026-01-31 | 초기 작성: Redis 배치 처리 기본 구조 |
| 2.0.0 | 2026-01-31 | 리뷰 반영: Q1~Q11 해결 및 추가 보완사항 3건 |

### v2.0.0 주요 변경사항

**P0 (Critical)**
- 9.1: Processing Queue + Lua Script 패턴 (데이터 손실/순서 역전 방지)
- 9.2: DistributedRecoveryLock 통합 (중복 실행 방지)

**P1 (Important)**
- 9.3: WAL 통합 (fallback 영속성)
- 9.4: ActiveKeySet 최적화 (O(1) 키 조회)
- 9.5: 청킹 구현 (파이프라인 크기 제한)
- 9.6: 모니터링 알림 (UnifiedNotificationManager 연동)

**P2 (Nice-to-have)**
- 9.7: LTRIM Safety Net
- 9.8: 시퀀스 번호 필드

**P3 (Future)**
- 9.9: RuntimeConfig 통합

**추가 보완**
- 9.10: Processing Queue 복구 태스크
- 9.11: Backpressure 메트릭
- 9.12: Graceful Shutdown Hook
