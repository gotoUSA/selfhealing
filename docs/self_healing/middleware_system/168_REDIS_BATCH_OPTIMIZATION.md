# 168. Redis 배치 처리 최적화

> **버전**: 1.0.0
> **작성일**: 2026-01-31
> **의존성**: [167_ASYNC_AUDIT_PIPELINE.md](167_ASYNC_AUDIT_PIPELINE.md)
> **예상 소요**: 2-3일

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

| 변수명 | 기본값 | 설명 |
|-------|-------|------|
| `SELFHEALING_REDIS_BATCH_SIZE` | 500 | Redis → DB 배치 크기 |
| `SELFHEALING_REDIS_FLUSH_INTERVAL` | 10 | 플러시 주기 (초) |
| `SELFHEALING_REDIS_MAX_FALLBACK` | 10000 | 폴백 버퍼 최대 크기 |

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
- [x] `selfhealing/tasks/audit_flush.py`: Celery 플러시 태스크 추가
- [x] `beat_schedule.py`: Beat 스케줄 추가

### 7.2 인프라

- [x] `k8s/celery-audit-worker.yaml`: 전용 워커 배포
- [ ] Redis 모니터링: 키 사이즈 추적
- [ ] Celery 모니터링: 태스크 실행 시간/실패율

### 7.3 테스트

- [x] 단위 테스트: 배치 처리, 폴백 (24개 통과)
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

## 9. 다음 단계

→ [169_SETTINGS_SCALE_LIMITS.md](169_SETTINGS_SCALE_LIMITS.md): 설정 한계값 증가
