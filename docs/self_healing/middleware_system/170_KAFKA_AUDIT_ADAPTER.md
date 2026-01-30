# 170. Kafka Audit 어댑터 구현

> **버전**: 1.0.0
> **작성일**: 2026-01-31
> **의존성**: [169_SETTINGS_SCALE_LIMITS.md](169_SETTINGS_SCALE_LIMITS.md)
> **예상 소요**: 3-4일

---

## 1. 현재 상황 (코드 근거)

### 1.1 이미 존재하는 Kafka 헤더 지원

**파일**: `packages/selfhealing-python/src/selfhealing/context/causation_context.py`
**라인**: 130-160

```python
# Kafka 헤더 접두사 정의
KAFKA_HEADER_PREFIX = "x-causation-"


def get_causation_for_kafka(
    context: CausationContext | None = None,
) -> list[tuple[str, bytes]]:
    """
    Kafka 메시지 헤더용 causation 정보 생성.

    Returns:
        [(header_name, header_value), ...] 형태의 헤더 리스트

    Example:
        >>> headers = get_causation_for_kafka()
        >>> producer.send("topic", value=msg, headers=headers)
    """
    ctx = context or get_causation_context()
    if ctx is None:
        return []

    headers = []

    if ctx.root_trace_id:
        headers.append((
            f"{KAFKA_HEADER_PREFIX}root-trace-id",
            ctx.root_trace_id.encode("utf-8"),
        ))

    if ctx.trace_id:
        headers.append((
            f"{KAFKA_HEADER_PREFIX}trace-id",
            ctx.trace_id.encode("utf-8"),
        ))

    if ctx.span_id:
        headers.append((
            f"{KAFKA_HEADER_PREFIX}span-id",
            ctx.span_id.encode("utf-8"),
        ))

    if ctx.operation_name:
        headers.append((
            f"{KAFKA_HEADER_PREFIX}operation",
            ctx.operation_name.encode("utf-8"),
        ))

    return headers
```

**활용 가능**:
- Kafka 헤더 생성 로직 이미 존재
- `AuditEntry`를 Kafka로 전송할 때 활용 가능

---

### 1.2 AuditAdapter 인터페이스

**파일**: `packages/selfhealing-python/src/selfhealing/interfaces/audit_adapter.py`
**라인**: 30-60

```python
class AuditAdapter(ABC):
    """
    감사 로그 어댑터 추상 클래스.

    구현체:
    - DjangoAuditAdapter: Django ORM 사용
    - RedisAuditBuffer: Redis 버퍼링
    - (신규) KafkaAuditAdapter: Kafka 스트리밍
    """

    @abstractmethod
    def log(self, entry: AuditEntry) -> None:
        """단일 감사 엔트리 기록."""
        pass

    def log_batch(self, entries: list[AuditEntry]) -> None:
        """배치 감사 엔트리 기록 (기본: 개별 호출)."""
        for entry in entries:
            self.log(entry)

    def close(self) -> None:
        """리소스 정리."""
        pass
```

---

### 1.3 현재 없는 것

- `KafkaAuditAdapter` 클래스
- Kafka 토픽 스키마 정의
- Kafka 커넥션 관리

---

## 2. 구현 계획

### 2.1 아키텍처

```
현재:
  AuditMiddleware → DjangoAuditAdapter → PostgreSQL
                                         ↓
                                   느림, 병목

개선 후:
  AuditMiddleware → KafkaAuditAdapter → Kafka Cluster
                                         ↓
                              Kafka Connect / Consumer
                                         ↓
                              PostgreSQL, Elasticsearch, S3
                              (비동기, 확장 가능)
```

### 2.2 파일 구조

```
selfhealing/adapters/audit/
├── __init__.py
├── base.py
├── django_adapter.py
├── redis_buffer.py
└── kafka_adapter.py  ← 신규!
```

---

## 3. 구현 상세

### 3.1 KafkaAuditAdapter 구현

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/audit/kafka_adapter.py` (신규)

```python
"""
Kafka Audit Adapter.

고처리량 감사 이벤트를 Kafka로 스트리밍.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from kafka import KafkaProducer
from kafka.errors import KafkaError

from selfhealing.interfaces.audit_adapter import AuditAdapter, AuditEntry
from selfhealing.context.causation_context import get_causation_for_kafka
from selfhealing.settings.kafka import KafkaAuditSettings

logger = logging.getLogger(__name__)


class KafkaAuditAdapter(AuditAdapter):
    """
    Kafka 기반 감사 로그 어댑터.

    특징:
    - 비동기 전송 (Non-blocking)
    - 자동 배치 (linger_ms)
    - 압축 지원 (snappy/lz4)
    - 실패 시 콜백 처리

    Usage:
        adapter = KafkaAuditAdapter()
        adapter.log(AuditEntry(...))
        adapter.close()  # 종료 시 플러시
    """

    def __init__(
        self,
        settings: KafkaAuditSettings | None = None,
        producer: KafkaProducer | None = None,
    ):
        """
        Args:
            settings: Kafka 설정 (None이면 환경변수에서 로드)
            producer: 외부 주입 프로듀서 (테스트용)
        """
        self._settings = settings or KafkaAuditSettings()
        self._producer = producer or self._create_producer()
        self._lock = threading.Lock()
        self._closed = False

        # 통계
        self._sent_count = 0
        self._error_count = 0

    def _create_producer(self) -> KafkaProducer:
        """KafkaProducer 생성."""
        return KafkaProducer(
            bootstrap_servers=self._settings.bootstrap_servers,

            # 직렬화
            key_serializer=lambda k: k.encode("utf-8") if k else None,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),

            # 배치 설정 (성능 최적화)
            batch_size=self._settings.batch_size_bytes,  # 16KB
            linger_ms=self._settings.linger_ms,  # 10ms 대기 후 배치 전송

            # 압축
            compression_type=self._settings.compression_type,  # snappy

            # 신뢰성
            acks=self._settings.acks,  # 1 (리더만) 또는 "all"
            retries=self._settings.retries,  # 3
            retry_backoff_ms=self._settings.retry_backoff_ms,  # 100ms

            # 버퍼
            buffer_memory=self._settings.buffer_memory,  # 32MB
            max_block_ms=self._settings.max_block_ms,  # 1000ms

            # TLS (선택)
            security_protocol=self._settings.security_protocol,
            ssl_cafile=self._settings.ssl_cafile,
            ssl_certfile=self._settings.ssl_certfile,
            ssl_keyfile=self._settings.ssl_keyfile,
        )

    def log(self, entry: AuditEntry) -> None:
        """
        단일 감사 이벤트 Kafka 전송.

        Non-blocking: send()는 즉시 반환, 실제 전송은 배치로.
        """
        if self._closed:
            logger.warning("[KafkaAuditAdapter] Adapter closed, ignoring log")
            return

        try:
            # AuditEntry → dict
            value = entry.to_dict() if hasattr(entry, "to_dict") else {
                "action": entry.action,
                "source": entry.source,
                "target_type": entry.target_type,
                "target_id": entry.target_id,
                "actor": entry.actor,
                "details": entry.details,
                "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
            }

            # 파티션 키: target_type:target_id (같은 대상은 같은 파티션)
            key = f"{entry.target_type}:{entry.target_id}" if entry.target_id else None

            # Causation 헤더 추가
            headers = get_causation_for_kafka()
            headers.append((
                "x-audit-action",
                entry.action.encode("utf-8"),
            ))

            # 비동기 전송
            future = self._producer.send(
                topic=self._settings.topic,
                key=key,
                value=value,
                headers=headers,
            )

            # 콜백 등록 (성공/실패 추적)
            future.add_callback(self._on_send_success)
            future.add_errback(self._on_send_error)

        except KafkaError as e:
            logger.error(f"[KafkaAuditAdapter] Send failed: {e}")
            self._error_count += 1
            # Fail-open: 예외 발생시켜도 비즈니스 로직에 영향 없음

    def log_batch(self, entries: list[AuditEntry]) -> None:
        """
        배치 감사 이벤트 Kafka 전송.

        linger_ms 덕분에 자동으로 배치됨.
        """
        for entry in entries:
            self.log(entry)

    def _on_send_success(self, record_metadata: Any) -> None:
        """전송 성공 콜백."""
        with self._lock:
            self._sent_count += 1

    def _on_send_error(self, exception: Exception) -> None:
        """전송 실패 콜백."""
        logger.warning(f"[KafkaAuditAdapter] Async send failed: {exception}")
        with self._lock:
            self._error_count += 1

    def flush(self, timeout: float | None = None) -> None:
        """버퍼 플러시 (동기)."""
        if self._producer:
            self._producer.flush(timeout=timeout)

    def close(self) -> None:
        """리소스 정리."""
        with self._lock:
            if self._closed:
                return
            self._closed = True

        if self._producer:
            try:
                self._producer.flush(timeout=5.0)
                self._producer.close(timeout=5.0)
            except Exception as e:
                logger.warning(f"[KafkaAuditAdapter] Close error: {e}")

        logger.info(
            f"[KafkaAuditAdapter] Closed. "
            f"Sent: {self._sent_count}, Errors: {self._error_count}"
        )

    def get_stats(self) -> dict[str, Any]:
        """통계 반환."""
        with self._lock:
            return {
                "sent_count": self._sent_count,
                "error_count": self._error_count,
                "closed": self._closed,
            }


# Singleton
_kafka_adapter: KafkaAuditAdapter | None = None


def get_kafka_audit_adapter() -> KafkaAuditAdapter:
    """KafkaAuditAdapter 싱글톤 반환."""
    global _kafka_adapter
    if _kafka_adapter is None:
        _kafka_adapter = KafkaAuditAdapter()
    return _kafka_adapter
```

---

### 3.2 KafkaAuditSettings 구현

**파일**: `packages/selfhealing-python/src/selfhealing/settings/kafka.py` (신규 또는 기존 확장)

```python
"""
Kafka Audit Settings.
"""
from pydantic import Field
from pydantic_settings import BaseSettings


class KafkaAuditSettings(BaseSettings):
    """Kafka 감사 로그 설정."""

    model_config = {"env_prefix": "SELFHEALING_KAFKA_AUDIT_"}

    # Connection
    bootstrap_servers: list[str] = Field(
        default=["localhost:9092"],
        description="Kafka 브로커 주소 목록",
    )
    topic: str = Field(
        default="selfhealing.audit.events",
        description="감사 이벤트 토픽명",
    )

    # Batching
    batch_size_bytes: int = Field(
        default=16384,  # 16KB
        ge=1024,
        le=1048576,  # 1MB
        description="배치 크기 (bytes)",
    )
    linger_ms: int = Field(
        default=10,  # 10ms
        ge=0,
        le=1000,
        description="배치 대기 시간 (ms). 0이면 즉시 전송.",
    )

    # Compression
    compression_type: str = Field(
        default="snappy",
        description="압축 타입: none, gzip, snappy, lz4, zstd",
    )

    # Reliability
    acks: str | int = Field(
        default=1,
        description="ACK 레벨: 0(없음), 1(리더), all(모든 레플리카)",
    )
    retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="재시도 횟수",
    )
    retry_backoff_ms: int = Field(
        default=100,
        ge=10,
        le=5000,
        description="재시도 대기 시간 (ms)",
    )

    # Buffer
    buffer_memory: int = Field(
        default=33554432,  # 32MB
        ge=1048576,  # 1MB
        le=1073741824,  # 1GB
        description="프로듀서 버퍼 메모리 (bytes)",
    )
    max_block_ms: int = Field(
        default=1000,  # 1초
        ge=100,
        le=60000,
        description="버퍼 풀 대기 최대 시간 (ms)",
    )

    # Security (Optional)
    security_protocol: str = Field(
        default="PLAINTEXT",
        description="보안 프로토콜: PLAINTEXT, SSL, SASL_PLAINTEXT, SASL_SSL",
    )
    ssl_cafile: str | None = Field(
        default=None,
        description="CA 인증서 경로",
    )
    ssl_certfile: str | None = Field(
        default=None,
        description="클라이언트 인증서 경로",
    )
    ssl_keyfile: str | None = Field(
        default=None,
        description="클라이언트 키 경로",
    )
```

---

### 3.3 Kafka 토픽 스키마 (Avro)

**파일**: `schemas/audit_event.avsc`

```json
{
  "type": "record",
  "name": "AuditEvent",
  "namespace": "com.selfhealing.audit",
  "doc": "Self-healing 시스템 감사 이벤트",
  "fields": [
    {
      "name": "id",
      "type": "string",
      "doc": "이벤트 고유 ID (UUID)"
    },
    {
      "name": "action",
      "type": "string",
      "doc": "수행된 액션 (e.g., CB_OPENED, CACHE_INVALIDATED)"
    },
    {
      "name": "source",
      "type": "string",
      "doc": "이벤트 발생 소스 (e.g., shopping-service)"
    },
    {
      "name": "target_type",
      "type": ["null", "string"],
      "default": null,
      "doc": "대상 엔티티 타입 (e.g., order, product)"
    },
    {
      "name": "target_id",
      "type": ["null", "string"],
      "default": null,
      "doc": "대상 엔티티 ID"
    },
    {
      "name": "actor",
      "type": ["null", "string"],
      "default": null,
      "doc": "액터 ID (사용자 또는 시스템)"
    },
    {
      "name": "details",
      "type": ["null", {
        "type": "map",
        "values": "string"
      }],
      "default": null,
      "doc": "추가 상세 정보"
    },
    {
      "name": "timestamp",
      "type": {
        "type": "long",
        "logicalType": "timestamp-millis"
      },
      "doc": "이벤트 발생 시간 (Unix ms)"
    },
    {
      "name": "causation",
      "type": ["null", {
        "type": "record",
        "name": "Causation",
        "fields": [
          {"name": "root_trace_id", "type": ["null", "string"], "default": null},
          {"name": "trace_id", "type": ["null", "string"], "default": null},
          {"name": "span_id", "type": ["null", "string"], "default": null},
          {"name": "operation", "type": ["null", "string"], "default": null}
        ]
      }],
      "default": null,
      "doc": "인과관계 추적 정보"
    }
  ]
}
```

---

## 4. Kafka Consumer 예제

### 4.1 PostgreSQL Sink (Python)

```python
"""
Kafka → PostgreSQL Audit Consumer.

별도 프로세스로 실행하여 Kafka 이벤트를 DB에 저장.
"""
from kafka import KafkaConsumer
import json
import psycopg2
from psycopg2.extras import execute_values


def run_consumer():
    consumer = KafkaConsumer(
        "selfhealing.audit.events",
        bootstrap_servers=["kafka:9092"],
        group_id="audit-db-sink",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
    )

    conn = psycopg2.connect("postgresql://...")
    batch = []
    batch_size = 500

    for message in consumer:
        event = message.value
        batch.append((
            event.get("id"),
            event.get("action"),
            event.get("source"),
            event.get("target_type"),
            event.get("target_id"),
            json.dumps(event.get("details", {})),
            event.get("timestamp"),
        ))

        if len(batch) >= batch_size:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO audit_log (id, action, source, target_type, target_id, details, timestamp)
                    VALUES %s
                    ON CONFLICT (id) DO NOTHING
                    """,
                    batch,
                )
                conn.commit()

            consumer.commit()
            batch = []

    consumer.close()
    conn.close()


if __name__ == "__main__":
    run_consumer()
```

---

## 5. 테스트 계획

### 5.1 단위 테스트

**파일**: `tests/unit/audit/test_kafka_adapter.py`

```python
import pytest
from unittest.mock import Mock, patch, MagicMock


class TestKafkaAuditAdapter:
    """KafkaAuditAdapter 테스트."""

    @pytest.fixture
    def mock_producer(self):
        producer = Mock()
        producer.send.return_value = MagicMock()
        return producer

    @pytest.fixture
    def adapter(self, mock_producer):
        from selfhealing.adapters.audit.kafka_adapter import KafkaAuditAdapter
        return KafkaAuditAdapter(producer=mock_producer)

    def test_log_sends_to_kafka(self, adapter, mock_producer):
        """log()가 Kafka로 전송하는지 확인."""
        from selfhealing.interfaces.audit_adapter import AuditEntry

        entry = AuditEntry(
            action="TEST_ACTION",
            source="test-service",
            target_type="order",
            target_id="123",
        )

        adapter.log(entry)

        mock_producer.send.assert_called_once()
        call_args = mock_producer.send.call_args
        assert call_args[1]["topic"] == "selfhealing.audit.events"
        assert "order:123" in call_args[1]["key"]

    def test_log_is_non_blocking(self, adapter):
        """log()가 Non-blocking인지 확인."""
        from selfhealing.interfaces.audit_adapter import AuditEntry
        import time

        entry = AuditEntry(action="TEST", source="test")

        start = time.time()
        for _ in range(1000):
            adapter.log(entry)
        elapsed = time.time() - start

        # 1000개 전송이 100ms 이내 (send()는 non-blocking)
        assert elapsed < 0.1

    def test_close_flushes_buffer(self, adapter, mock_producer):
        """close()가 버퍼를 플러시하는지 확인."""
        adapter.close()

        mock_producer.flush.assert_called()
        mock_producer.close.assert_called()

    def test_causation_headers_added(self, adapter, mock_producer):
        """Causation 헤더가 추가되는지 확인."""
        from selfhealing.interfaces.audit_adapter import AuditEntry
        from selfhealing.context.causation_context import (
            CausationContext,
            set_causation_context,
        )

        # Causation 설정
        ctx = CausationContext(
            root_trace_id="root-123",
            trace_id="trace-456",
            operation_name="test_op",
        )
        set_causation_context(ctx)

        try:
            entry = AuditEntry(action="TEST", source="test")
            adapter.log(entry)

            call_args = mock_producer.send.call_args
            headers = call_args[1]["headers"]

            # Causation 헤더 확인
            header_names = [h[0] for h in headers]
            assert "x-causation-root-trace-id" in header_names
            assert "x-causation-trace-id" in header_names
        finally:
            set_causation_context(None)
```

---

## 6. 환경 변수

| 변수명 | 기본값 | 설명 |
|-------|-------|------|
| `SELFHEALING_KAFKA_AUDIT_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka 브로커 주소 |
| `SELFHEALING_KAFKA_AUDIT_TOPIC` | `selfhealing.audit.events` | 토픽명 |
| `SELFHEALING_KAFKA_AUDIT_BATCH_SIZE_BYTES` | 16384 | 배치 크기 (bytes) |
| `SELFHEALING_KAFKA_AUDIT_LINGER_MS` | 10 | 배치 대기 (ms) |
| `SELFHEALING_KAFKA_AUDIT_COMPRESSION_TYPE` | snappy | 압축 타입 |
| `SELFHEALING_KAFKA_AUDIT_ACKS` | 1 | ACK 레벨 |

---

## 7. 성능 비교

| 지표 | PostgreSQL 직접 | Kafka 경유 |
|-----|----------------|-----------|
| 쓰기 지연 | 5-50ms | ~1ms (async) |
| 처리량 | 1,000/sec | 100,000+/sec |
| 장애 격리 | DB 다운 = 서비스 영향 | Kafka 버퍼링 |
| 확장성 | 수직 확장 | 수평 확장 (파티션) |

---

## 8. 마이그레이션 체크리스트

### 8.1 인프라

- [ ] Kafka 클러스터 구축 (3+ 브로커)
- [ ] 토픽 생성: `selfhealing.audit.events` (파티션 12+)
- [ ] Kafka Connect 또는 Consumer 배포

### 8.2 코드

- [ ] `kafka_adapter.py` 구현
- [ ] `settings/kafka.py` 설정 클래스
- [ ] `adapters/audit/__init__.py` 내보내기

### 8.3 전환

- [ ] 듀얼 라이트 모드: Kafka + PostgreSQL 동시 기록
- [ ] Kafka Consumer로 PostgreSQL 동기화 확인
- [ ] PostgreSQL 직접 쓰기 비활성화

---

## 9. WAL과 Kafka의 관계 ⭐ 중요

### 9.1 상호 보완적 역할

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                        WAL + Kafka 통합 아키텍처                               │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    │
│  │   Event     │───▶│    WAL     │───▶│   Kafka    │───▶│   중앙      │    │
│  │   발생      │    │  (로컬)    │    │  (분산)    │    │   저장소    │    │
│  └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘    │
│                           │                  │                               │
│                           ▼                  ▼                               │
│                    ┌─────────────┐    ┌─────────────┐                        │
│                    │ 로컬 디스크 │    │  복제/     │                        │
│                    │ 영속화      │    │  분산 저장 │                        │
│                    └─────────────┘    └─────────────┘                        │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 9.2 역할 구분

| 구성요소 | 역할 | 장애 시나리오 |
|---------|------|-------------|
| **WAL** | 로컬 디스크 영속화 | Kafka 다운 → WAL에 보관 → Kafka 복구 시 재전송 |
| **Kafka** | 분산 메시지 전달 | WAL에서 at-least-once 보장 |
| **CheckpointManager** | 마지막 처리 위치 기록 | 재시작 시 중복 방지 |

### 9.3 통합 흐름

```python
# 이벤트 발생 시
def log_audit_event(entry: AuditEntry) -> None:
    """
    1. WAL 먼저 기록 (로컬 디스크 영속화)
    2. Kafka 전송 시도
    3. 성공 시 WAL 항목 완료 표시
    4. 실패 시 WAL에 남아 있음 → 재시도
    """
    # 1. WAL 기록 (디스크 영속화)
    wal = get_wal()
    seq = wal.write(entry.to_dict())

    # 2. Kafka 전송 시도
    try:
        kafka_adapter.log(entry)

        # 3. 성공 시 체크포인트 업데이트
        checkpoint_manager.update(seq)

    except KafkaError:
        # 4. 실패해도 WAL에 남아 있음!
        logger.warning("Kafka 전송 실패, WAL에서 재시도 예정")
```

### 9.4 장애 복구 흐름

```
장애 시나리오: Kafka 30분 다운

1. [0:00] Kafka 다운
2. [0:00 ~ 0:30] 이벤트 → WAL에 기록 (로컬 보관)
3. [0:30] Kafka 복구
4. [0:30 ~ 0:35] AuditSyncWorker가 WAL → Kafka 재전송
5. [0:35] 정상 상태 복귀

결과: 데이터 유실 0%
```

### 9.5 왜 둘 다 필요한가?

| WAL만 사용 | Kafka만 사용 | WAL + Kafka |
|-----------|-------------|-------------|
| ❌ 분산 환경 미지원 | ❌ Kafka 다운 시 유실 | ✅ 둘의 장점 결합 |
| ❌ 중앙 집계 어려움 | ❌ 네트워크 장애 취약 | ✅ 로컬 + 분산 보장 |
| ✅ 로컬 영속화 | ✅ 고처리량 분산 | ✅ 데이터 유실 0% |

### 9.6 설정 예시

```python
# settings.py
SELFHEALING_AUDIT_PIPELINE = {
    # WAL 설정 (로컬 영속화)
    "WAL_ENABLED": True,
    "WAL_DIR": "/var/log/audit/wal",
    "WAL_SYNC_INTERVAL_SEC": 5.0,

    # Kafka 설정 (분산 전달)
    "KAFKA_ENABLED": True,
    "KAFKA_BOOTSTRAP_SERVERS": "kafka1:9092,kafka2:9092,kafka3:9092",
    "KAFKA_TOPIC": "selfhealing.audit.events",

    # 통합 설정
    "PIPELINE_MODE": "wal_then_kafka",  # WAL 먼저, 그 다음 Kafka
    "RETRY_ON_KAFKA_FAILURE": True,
    "MAX_WAL_RETRY_COUNT": 10,
}
```

---

## 10. 다음 단계

→ [171_KUBERNETES_AUTOSCALING.md](171_KUBERNETES_AUTOSCALING.md): Kubernetes HPA 구현
