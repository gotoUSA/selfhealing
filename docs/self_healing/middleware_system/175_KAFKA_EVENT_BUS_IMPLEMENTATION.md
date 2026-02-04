# 175. Kafka Event Bus 실제 구현 가이드

> **버전**: 1.0.0
> **작성일**: 2026-02-04
> **의존성**: [170_KAFKA_AUDIT_ADAPTER.md](170_KAFKA_AUDIT_ADAPTER.md), [174_MISSING_SYSTEMS_MASTER_PLAN.md](174_MISSING_SYSTEMS_MASTER_PLAN.md)
> **예상 소요**: 5-7일
> **예상 코드량**: ~2,000줄

---

## 0. 문서 목적

이 문서는 현재 Header만 준비된 Kafka 연동을 **실제 Producer/Consumer**로 구현하여 Memory Buffer 휘발성 문제를 해결합니다.

**핵심 가치**: "Pod 재시작, Redis 장애에도 이벤트 손실 0%"

---

## 1. 현재 상황 분석 (코드 근거)

### 1.1 현재 존재하는 Kafka 관련 코드

| 파일 | 역할 | 상태 |
|------|------|------|
| `audit/kafka_checkpoint.py` | WAL-Kafka 오프셋 매핑 | ✅ 완료 |
| `audit/cascade_chain.py` | Kafka 헤더 전파 유틸리티 | ✅ 완료 |
| `audit/cascade_config.py` | `KAFKA_HEADER_PREFIX` 상수 | ✅ 완료 |
| **Kafka Producer** | 실제 Kafka 연결 | ❌ 없음 |
| **Kafka Consumer** | 실제 Kafka 연결 | ❌ 없음 |

### 1.2 Kafka Header 전파 유틸리티

**파일**: `packages/selfhealing-python/src/selfhealing/audit/cascade_chain.py`

```python
def get_causation_for_kafka() -> dict[str, bytes]:
    """현재 CausationContext를 Kafka 헤더 형식으로 반환."""
    ctx = CausationContext.get_current()
    if not ctx:
        return {}

    return {
        f"{KAFKA_HEADER_PREFIX}cascade_id": ctx.cascade_id.encode(),
        f"{KAFKA_HEADER_PREFIX}causation_id": ctx.causation_id.encode(),
        # ...
    }
```

**문제점**: 헤더만 준비되고, 실제 Kafka 전송 로직이 없음

### 1.3 KafkaCheckpointManager

**파일**: `packages/selfhealing-python/src/selfhealing/audit/kafka_checkpoint.py`
**라인**: 285-330

```python
def sync_wal_to_kafka_with_checkpoint(
    wal,
    adapter,
    checkpoint: KafkaCheckpointManager,
    namespace: str = "default",
) -> int:
    """WAL → Kafka 동기화 (체크포인트 기반)."""
    # ...
    for entry in entries:
        try:
            audit_entry = AuditEntry(**entry.data)
            adapter.log(audit_entry)  # ⚠️ adapter가 실제 Kafka 연결 없음
            adapter.flush(timeout=5.0)
```

**문제점**: `adapter.log()`를 호출하나 실제 Kafka 어댑터 구현이 없음

---

## 2. 구현 목표

### 2.1 핵심 컴포넌트

```
┌─────────────────────────────────────────────────────────────┐
│                    KafkaEventBus                             │
│  ┌─────────────────┐     ┌─────────────────────────────┐    │
│  │ KafkaProducer   │     │ KafkaConsumer               │    │
│  │ - Idempotent    │     │ - Consumer Group            │    │
│  │ - Acks=all      │     │ - Auto-commit=false         │    │
│  │ - Compression   │     │ - Manual offset management  │    │
│  └────────┬────────┘     └────────────┬────────────────┘    │
│           │                           │                      │
│           ▼                           ▼                      │
│  ┌─────────────────┐     ┌─────────────────────────────┐    │
│  │ DeliveryHandler │     │ EventHandler                │    │
│  │ - Ack callback  │     │ - Business logic            │    │
│  │ - Error retry   │     │ - Checkpoint update         │    │
│  └─────────────────┘     └─────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 기능 요구사항

| 기능 | 설명 | 우선순위 |
|------|------|----------|
| Idempotent Producer | 중복 메시지 방지 | P0 |
| Exactly-once 의미론 | 정확히 한 번 전송 보장 | P0 |
| 수동 오프셋 관리 | 처리 완료 후 커밋 | P0 |
| 배압 처리 | 브로커 과부하 시 조절 | P1 |
| 다중 토픽 지원 | 도메인별 토픽 분리 | P1 |
| Schema Registry | 스키마 진화 관리 | P2 |

---

## 3. 상세 구현 명세

### 3.1 파일 구조

```
packages/selfhealing-python/src/selfhealing/adapters/kafka/
├── __init__.py
├── config.py              # Kafka 설정
├── producer.py            # KafkaAuditProducer
├── consumer.py            # KafkaAuditConsumer
├── event_bus.py           # KafkaEventBus (통합 인터페이스)
├── delivery_handler.py    # 전송 결과 처리
└── schemas.py             # Avro/JSON 스키마
```

### 3.2 Kafka 설정 (config.py)

```python
# packages/selfhealing-python/src/selfhealing/adapters/kafka/config.py
"""
Kafka 설정 모듈.

기존 코드 참조:
- audit/kafka_checkpoint.py: REDIS_KEY_PREFIX 패턴
- settings/*.py: Pydantic Settings 패턴
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class KafkaSettings(BaseSettings):
    """
    Kafka 설정.

    환경변수 우선순위:
    1. SELFHEALING_KAFKA_* 환경변수
    2. 설정 파일 (.env)
    3. 기본값
    """

    # 브로커 설정
    bootstrap_servers: str = Field(
        default="localhost:9092",
        description="Kafka 브로커 주소 (쉼표 구분)",
    )

    # 토픽 설정
    topic_prefix: str = Field(
        default="selfhealing.",
        description="토픽 프리픽스",
    )
    audit_topic: str = Field(
        default="audit.events",
        description="Audit 이벤트 토픽",
    )
    dlq_topic: str = Field(
        default="dlq.events",
        description="DLQ 이벤트 토픽",
    )
    recovery_topic: str = Field(
        default="recovery.events",
        description="Recovery 이벤트 토픽",
    )

    # Producer 설정
    producer_acks: Literal["0", "1", "all"] = Field(
        default="all",
        description="Producer ACK 레벨 (all 권장)",
    )
    producer_retries: int = Field(
        default=3,
        description="Producer 재시도 횟수",
    )
    producer_batch_size: int = Field(
        default=16384,
        description="Producer 배치 크기 (bytes)",
    )
    producer_linger_ms: int = Field(
        default=10,
        description="Producer 배치 대기 시간 (ms)",
    )
    producer_compression_type: str = Field(
        default="zstd",
        description="압축 알고리즘 (zstd 권장)",
    )
    producer_idempotent: bool = Field(
        default=True,
        description="Idempotent Producer 활성화",
    )

    # Consumer 설정
    consumer_group_id: str = Field(
        default="selfhealing-audit-consumer",
        description="Consumer 그룹 ID",
    )
    consumer_auto_offset_reset: Literal["earliest", "latest"] = Field(
        default="earliest",
        description="오프셋 리셋 정책",
    )
    consumer_enable_auto_commit: bool = Field(
        default=False,
        description="자동 오프셋 커밋 (false 권장)",
    )
    consumer_max_poll_records: int = Field(
        default=500,
        description="한 번에 폴링할 최대 레코드 수",
    )
    consumer_session_timeout_ms: int = Field(
        default=30000,
        description="세션 타임아웃 (ms)",
    )

    # 보안 설정
    security_protocol: str = Field(
        default="PLAINTEXT",
        description="보안 프로토콜 (PLAINTEXT, SSL, SASL_SSL)",
    )
    sasl_mechanism: str | None = Field(
        default=None,
        description="SASL 메커니즘 (PLAIN, SCRAM-SHA-256)",
    )
    sasl_username: str | None = Field(
        default=None,
        description="SASL 사용자명",
    )
    sasl_password: str | None = Field(
        default=None,
        description="SASL 비밀번호",
    )

    # Schema Registry
    schema_registry_url: str | None = Field(
        default=None,
        description="Confluent Schema Registry URL",
    )

    class Config:
        env_prefix = "SELFHEALING_KAFKA_"
        env_file = ".env"

    @property
    def full_audit_topic(self) -> str:
        """전체 Audit 토픽 이름."""
        return f"{self.topic_prefix}{self.audit_topic}"

    @property
    def full_dlq_topic(self) -> str:
        """전체 DLQ 토픽 이름."""
        return f"{self.topic_prefix}{self.dlq_topic}"

    @property
    def full_recovery_topic(self) -> str:
        """전체 Recovery 토픽 이름."""
        return f"{self.topic_prefix}{self.recovery_topic}"


@lru_cache(maxsize=1)
def get_kafka_settings() -> KafkaSettings:
    """Kafka 설정 싱글톤 반환."""
    return KafkaSettings()


def reset_kafka_settings() -> None:
    """설정 캐시 리셋 (테스트용)."""
    get_kafka_settings.cache_clear()
```

### 3.3 Kafka Producer (producer.py)

```python
# packages/selfhealing-python/src/selfhealing/adapters/kafka/producer.py
"""
Kafka Audit Producer.

기존 코드 참조:
- utils/async_logger.py: 비동기 로깅 패턴
- audit/wal.py: WAL-First 프로토콜
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from confluent_kafka import Producer

from selfhealing.adapters.kafka.config import KafkaSettings, get_kafka_settings
from selfhealing.audit.cascade_chain import get_causation_for_kafka

logger = logging.getLogger(__name__)


@dataclass
class DeliveryReport:
    """전송 결과 리포트."""

    topic: str
    partition: int
    offset: int
    timestamp: float
    key: str | None
    error: str | None = None

    @property
    def success(self) -> bool:
        """전송 성공 여부."""
        return self.error is None


class KafkaAuditProducer:
    """
    Kafka Audit 이벤트 Producer.

    핵심 특징:
    - Idempotent Producer: 중복 메시지 방지
    - WAL-First: Kafka 전송 전 WAL 기록
    - Delivery Callback: 비동기 전송 결과 추적
    - Graceful Shutdown: 미전송 메시지 처리

    기존 코드 패턴:
    - utils/async_logger.py#L140-200: 비동기 이벤트 처리
    - audit/wal.py#L200-250: WAL 쓰기 패턴
    """

    def __init__(
        self,
        settings: KafkaSettings | None = None,
        on_delivery: Callable[[DeliveryReport], None] | None = None,
        wal: Any | None = None,
    ):
        """
        KafkaAuditProducer 초기화.

        Args:
            settings: Kafka 설정 (None이면 기본값)
            on_delivery: 전송 결과 콜백
            wal: WriteAheadLog 인스턴스 (WAL-First 프로토콜)
        """
        self._settings = settings or get_kafka_settings()
        self._on_delivery = on_delivery
        self._wal = wal

        self._producer: Producer | None = None
        self._lock = threading.Lock()
        self._stats = {
            "messages_sent": 0,
            "messages_delivered": 0,
            "messages_failed": 0,
            "last_error": None,
        }

        self._init_producer()

    def _init_producer(self) -> None:
        """Producer 초기화."""
        try:
            from confluent_kafka import Producer

            config = self._build_config()
            self._producer = Producer(config)

            logger.info(
                f"[KafkaProducer] Initialized with "
                f"bootstrap_servers={self._settings.bootstrap_servers}"
            )
        except ImportError:
            logger.error(
                "[KafkaProducer] confluent-kafka not installed. "
                "Install with: pip install 'selfhealing[kafka]'"
            )
            raise
        except Exception as e:
            logger.error(f"[KafkaProducer] Initialization failed: {e}")
            raise

    def _build_config(self) -> dict[str, Any]:
        """Producer 설정 빌드."""
        config = {
            "bootstrap.servers": self._settings.bootstrap_servers,
            "acks": self._settings.producer_acks,
            "retries": self._settings.producer_retries,
            "batch.size": self._settings.producer_batch_size,
            "linger.ms": self._settings.producer_linger_ms,
            "compression.type": self._settings.producer_compression_type,
            "enable.idempotence": self._settings.producer_idempotent,
        }

        # 보안 설정
        if self._settings.security_protocol != "PLAINTEXT":
            config["security.protocol"] = self._settings.security_protocol
            if self._settings.sasl_mechanism:
                config["sasl.mechanism"] = self._settings.sasl_mechanism
                config["sasl.username"] = self._settings.sasl_username
                config["sasl.password"] = self._settings.sasl_password

        return config

    def _delivery_callback(
        self,
        err: Any,
        msg: Any,
        user_callback: Callable[[DeliveryReport], None] | None = None,
    ) -> None:
        """내부 전송 결과 콜백."""
        report = DeliveryReport(
            topic=msg.topic(),
            partition=msg.partition(),
            offset=msg.offset() if msg.offset() is not None else -1,
            timestamp=time.time(),
            key=msg.key().decode() if msg.key() else None,
            error=str(err) if err else None,
        )

        with self._lock:
            if err:
                self._stats["messages_failed"] += 1
                self._stats["last_error"] = str(err)
                logger.error(f"[KafkaProducer] Delivery failed: {err}")
            else:
                self._stats["messages_delivered"] += 1
                logger.debug(
                    f"[KafkaProducer] Delivered to {report.topic}[{report.partition}]@{report.offset}"
                )

        # 사용자 콜백 호출
        if user_callback:
            try:
                user_callback(report)
            except Exception as e:
                logger.error(f"[KafkaProducer] User callback error: {e}")

        # 기본 콜백 호출
        if self._on_delivery:
            try:
                self._on_delivery(report)
            except Exception as e:
                logger.error(f"[KafkaProducer] on_delivery callback error: {e}")

    def publish(
        self,
        topic: str,
        event: dict[str, Any],
        key: str | None = None,
        headers: dict[str, bytes] | None = None,
        partition: int | None = None,
        on_delivery: Callable[[DeliveryReport], None] | None = None,
    ) -> bool:
        """
        이벤트 발행.

        WAL-First 프로토콜:
        1. WAL에 기록 (선택적)
        2. Kafka로 전송
        3. 전송 결과 콜백

        Args:
            topic: 토픽 이름 (프리픽스 제외)
            event: 이벤트 데이터
            key: 파티션 키 (None이면 라운드로빈)
            headers: 추가 헤더
            partition: 특정 파티션 (None이면 자동)
            on_delivery: 전송 결과 콜백

        Returns:
            전송 시작 성공 여부
        """
        if not self._producer:
            logger.error("[KafkaProducer] Producer not initialized")
            return False

        try:
            # 1. WAL-First (선택적)
            if self._wal:
                self._wal.write({
                    "kafka_topic": topic,
                    "kafka_key": key,
                    "event": event,
                    "timestamp": time.time(),
                })

            # 2. 메시지 준비
            full_topic = f"{self._settings.topic_prefix}{topic}"
            value = json.dumps(event, default=str, ensure_ascii=False).encode("utf-8")

            # Causation Context 헤더 추가
            all_headers = get_causation_for_kafka()
            if headers:
                all_headers.update(headers)

            # Header를 리스트 형식으로 변환 (confluent-kafka 요구사항)
            header_list = [(k, v) for k, v in all_headers.items()] if all_headers else None

            # 3. 전송
            self._producer.produce(
                topic=full_topic,
                value=value,
                key=key.encode("utf-8") if key else None,
                headers=header_list,
                partition=partition if partition is not None else -1,
                callback=lambda err, msg: self._delivery_callback(err, msg, on_delivery),
            )

            with self._lock:
                self._stats["messages_sent"] += 1

            return True

        except Exception as e:
            logger.error(f"[KafkaProducer] Publish error: {e}")
            with self._lock:
                self._stats["messages_failed"] += 1
                self._stats["last_error"] = str(e)
            return False

    def publish_audit_event(
        self,
        event: dict[str, Any],
        domain: str | None = None,
        on_delivery: Callable[[DeliveryReport], None] | None = None,
    ) -> bool:
        """
        Audit 이벤트 발행 (편의 메서드).

        Args:
            event: Audit 이벤트 데이터
            domain: 도메인 (파티션 키로 사용)
            on_delivery: 전송 결과 콜백

        Returns:
            전송 시작 성공 여부
        """
        # 메타데이터 추가
        event.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        event.setdefault("schema_version", 1)

        return self.publish(
            topic=self._settings.audit_topic,
            event=event,
            key=domain,
            on_delivery=on_delivery,
        )

    def flush(self, timeout: float = 10.0) -> int:
        """
        버퍼된 메시지 플러시.

        Args:
            timeout: 타임아웃 (초)

        Returns:
            플러시 후 남은 메시지 수
        """
        if not self._producer:
            return 0

        remaining = self._producer.flush(timeout)
        if remaining > 0:
            logger.warning(
                f"[KafkaProducer] {remaining} messages still in buffer after flush"
            )
        return remaining

    def poll(self, timeout: float = 0) -> int:
        """
        콜백 처리를 위한 폴링.

        Args:
            timeout: 타임아웃 (초)

        Returns:
            처리된 이벤트 수
        """
        if not self._producer:
            return 0
        return self._producer.poll(timeout)

    def close(self) -> None:
        """Producer 종료."""
        if self._producer:
            # Graceful shutdown: 버퍼된 메시지 전송
            remaining = self.flush(timeout=30.0)
            if remaining > 0:
                logger.warning(
                    f"[KafkaProducer] Closed with {remaining} undelivered messages"
                )
            self._producer = None
            logger.info("[KafkaProducer] Closed")

    def get_stats(self) -> dict[str, Any]:
        """통계 반환."""
        with self._lock:
            return dict(self._stats)

    def __enter__(self) -> "KafkaAuditProducer":
        """Context manager 진입."""
        return self

    def __exit__(self, *args) -> None:
        """Context manager 종료."""
        self.close()


# =============================================================================
# Singleton
# =============================================================================

_producer: KafkaAuditProducer | None = None
_producer_lock = threading.Lock()


def get_kafka_producer() -> KafkaAuditProducer:
    """Kafka Producer 싱글톤 반환."""
    global _producer
    if _producer is None:
        with _producer_lock:
            if _producer is None:
                _producer = KafkaAuditProducer()
    return _producer


def reset_kafka_producer() -> None:
    """Producer 리셋 (테스트용)."""
    global _producer
    with _producer_lock:
        if _producer is not None:
            _producer.close()
            _producer = None
```

### 3.4 Kafka Consumer (consumer.py)

```python
# packages/selfhealing-python/src/selfhealing/adapters/kafka/consumer.py
"""
Kafka Audit Consumer.

기존 코드 참조:
- audit/kafka_checkpoint.py: 체크포인트 관리
- adapters/celery/tasks/persistence.py: 비동기 처리 패턴
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from confluent_kafka import Consumer, Message

from selfhealing.adapters.kafka.config import KafkaSettings, get_kafka_settings
from selfhealing.audit.cascade_chain import restore_causation_from_kafka
from selfhealing.audit.kafka_checkpoint import KafkaCheckpointManager

logger = logging.getLogger(__name__)


@dataclass
class ConsumedEvent:
    """소비된 이벤트."""

    topic: str
    partition: int
    offset: int
    key: str | None
    value: dict[str, Any]
    headers: dict[str, bytes]
    timestamp: float


EventHandler = Callable[[ConsumedEvent], bool]


class KafkaAuditConsumer:
    """
    Kafka Audit 이벤트 Consumer.

    핵심 특징:
    - 수동 오프셋 관리: 처리 완료 후 커밋
    - Checkpoint 통합: KafkaCheckpointManager 연동
    - Causation 복원: Kafka 헤더에서 컨텍스트 복원
    - Graceful Shutdown: 진행 중인 처리 완료 대기

    기존 코드 패턴:
    - audit/kafka_checkpoint.py: 체크포인트 관리
    - adapters/celery/tasks/persistence.py: 비동기 처리
    """

    def __init__(
        self,
        topics: list[str] | None = None,
        settings: KafkaSettings | None = None,
        checkpoint_manager: KafkaCheckpointManager | None = None,
        event_handler: EventHandler | None = None,
    ):
        """
        KafkaAuditConsumer 초기화.

        Args:
            topics: 구독할 토픽 목록 (프리픽스 제외)
            settings: Kafka 설정
            checkpoint_manager: 체크포인트 관리자
            event_handler: 이벤트 처리 핸들러
        """
        self._settings = settings or get_kafka_settings()
        self._topics = topics or [self._settings.audit_topic]
        self._checkpoint_manager = checkpoint_manager
        self._event_handler = event_handler

        self._consumer: Consumer | None = None
        self._running = False
        self._lock = threading.Lock()
        self._worker_thread: threading.Thread | None = None

        self._stats = {
            "messages_consumed": 0,
            "messages_processed": 0,
            "messages_failed": 0,
            "commits": 0,
            "last_error": None,
        }

        self._init_consumer()

    def _init_consumer(self) -> None:
        """Consumer 초기화."""
        try:
            from confluent_kafka import Consumer

            config = self._build_config()
            self._consumer = Consumer(config)

            # 토픽 구독
            full_topics = [
                f"{self._settings.topic_prefix}{t}" for t in self._topics
            ]
            self._consumer.subscribe(full_topics)

            logger.info(
                f"[KafkaConsumer] Subscribed to topics: {full_topics}"
            )
        except ImportError:
            logger.error(
                "[KafkaConsumer] confluent-kafka not installed. "
                "Install with: pip install 'selfhealing[kafka]'"
            )
            raise
        except Exception as e:
            logger.error(f"[KafkaConsumer] Initialization failed: {e}")
            raise

    def _build_config(self) -> dict[str, Any]:
        """Consumer 설정 빌드."""
        config = {
            "bootstrap.servers": self._settings.bootstrap_servers,
            "group.id": self._settings.consumer_group_id,
            "auto.offset.reset": self._settings.consumer_auto_offset_reset,
            "enable.auto.commit": self._settings.consumer_enable_auto_commit,
            "max.poll.records": self._settings.consumer_max_poll_records,
            "session.timeout.ms": self._settings.consumer_session_timeout_ms,
        }

        # 보안 설정
        if self._settings.security_protocol != "PLAINTEXT":
            config["security.protocol"] = self._settings.security_protocol
            if self._settings.sasl_mechanism:
                config["sasl.mechanism"] = self._settings.sasl_mechanism
                config["sasl.username"] = self._settings.sasl_username
                config["sasl.password"] = self._settings.sasl_password

        return config

    def _parse_message(self, msg: Message) -> ConsumedEvent | None:
        """메시지 파싱."""
        try:
            value = json.loads(msg.value().decode("utf-8"))
            headers = {}
            if msg.headers():
                for key, val in msg.headers():
                    headers[key] = val

            return ConsumedEvent(
                topic=msg.topic(),
                partition=msg.partition(),
                offset=msg.offset(),
                key=msg.key().decode("utf-8") if msg.key() else None,
                value=value,
                headers=headers,
                timestamp=msg.timestamp()[1] / 1000.0 if msg.timestamp()[0] != 0 else time.time(),
            )
        except Exception as e:
            logger.error(f"[KafkaConsumer] Message parse error: {e}")
            return None

    def _process_message(self, event: ConsumedEvent) -> bool:
        """
        메시지 처리.

        Causation Context 복원 후 핸들러 호출.
        """
        # Causation Context 복원
        header_list = [(k, v) for k, v in event.headers.items()]

        with restore_causation_from_kafka(header_list):
            if self._event_handler:
                try:
                    return self._event_handler(event)
                except Exception as e:
                    logger.error(f"[KafkaConsumer] Handler error: {e}")
                    return False
            else:
                # 기본: 로깅만
                logger.debug(
                    f"[KafkaConsumer] Received: {event.topic}[{event.partition}]@{event.offset}"
                )
                return True

    def _commit_offset(self, event: ConsumedEvent) -> None:
        """오프셋 커밋."""
        if not self._consumer:
            return

        try:
            self._consumer.commit(asynchronous=False)

            with self._lock:
                self._stats["commits"] += 1

            # Checkpoint 저장
            if self._checkpoint_manager:
                self._checkpoint_manager.save_checkpoint(
                    namespace=event.topic.replace(self._settings.topic_prefix, ""),
                    wal_sequence=0,  # Kafka 전용 모드에서는 미사용
                    kafka_topic=event.topic,
                    kafka_partition=event.partition,
                    kafka_offset=event.offset,
                    checksum="",
                )

            logger.debug(
                f"[KafkaConsumer] Committed: {event.topic}[{event.partition}]@{event.offset}"
            )
        except Exception as e:
            logger.error(f"[KafkaConsumer] Commit error: {e}")

    def poll_once(self, timeout: float = 1.0) -> ConsumedEvent | None:
        """
        단일 메시지 폴링.

        Args:
            timeout: 타임아웃 (초)

        Returns:
            소비된 이벤트 또는 None
        """
        if not self._consumer:
            return None

        msg = self._consumer.poll(timeout)
        if msg is None:
            return None

        if msg.error():
            from confluent_kafka import KafkaError

            if msg.error().code() == KafkaError._PARTITION_EOF:
                return None
            else:
                logger.error(f"[KafkaConsumer] Poll error: {msg.error()}")
                return None

        event = self._parse_message(msg)
        if event:
            with self._lock:
                self._stats["messages_consumed"] += 1

        return event

    def consume_batch(
        self,
        batch_size: int = 100,
        timeout: float = 1.0,
    ) -> list[ConsumedEvent]:
        """
        배치 소비.

        Args:
            batch_size: 배치 크기
            timeout: 타임아웃 (초)

        Returns:
            소비된 이벤트 목록
        """
        events = []
        for _ in range(batch_size):
            event = self.poll_once(timeout=timeout / batch_size)
            if event:
                events.append(event)
            else:
                break
        return events

    def run(self) -> None:
        """
        Consumer 루프 시작 (블로킹).

        Graceful shutdown을 위해 stop() 호출 필요.
        """
        self._running = True
        logger.info("[KafkaConsumer] Starting consume loop")

        while self._running:
            try:
                event = self.poll_once(timeout=1.0)
                if event is None:
                    continue

                success = self._process_message(event)

                with self._lock:
                    if success:
                        self._stats["messages_processed"] += 1
                    else:
                        self._stats["messages_failed"] += 1

                # 처리 성공 시에만 커밋
                if success:
                    self._commit_offset(event)

            except Exception as e:
                logger.error(f"[KafkaConsumer] Loop error: {e}")
                with self._lock:
                    self._stats["last_error"] = str(e)
                time.sleep(1.0)  # 에러 시 잠시 대기

        logger.info("[KafkaConsumer] Consume loop stopped")

    def start_background(self) -> None:
        """백그라운드 스레드에서 Consumer 시작."""
        if self._worker_thread and self._worker_thread.is_alive():
            logger.warning("[KafkaConsumer] Already running")
            return

        self._worker_thread = threading.Thread(
            target=self.run,
            name="KafkaAuditConsumer",
            daemon=True,
        )
        self._worker_thread.start()
        logger.info("[KafkaConsumer] Background thread started")

    def stop(self, timeout: float = 10.0) -> None:
        """Consumer 정지."""
        self._running = False

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=timeout)
            if self._worker_thread.is_alive():
                logger.warning("[KafkaConsumer] Thread did not stop in time")

        logger.info("[KafkaConsumer] Stopped")

    def close(self) -> None:
        """Consumer 종료."""
        self.stop()

        if self._consumer:
            self._consumer.close()
            self._consumer = None

        logger.info("[KafkaConsumer] Closed")

    def get_stats(self) -> dict[str, Any]:
        """통계 반환."""
        with self._lock:
            return dict(self._stats)

    def __enter__(self) -> "KafkaAuditConsumer":
        """Context manager 진입."""
        return self

    def __exit__(self, *args) -> None:
        """Context manager 종료."""
        self.close()
```

---

## 4. 통합 Event Bus (event_bus.py)

```python
# packages/selfhealing-python/src/selfhealing/adapters/kafka/event_bus.py
"""
Kafka Event Bus - 통합 인터페이스.

Producer와 Consumer를 통합하여 Event-Driven 아키텍처 지원.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from selfhealing.adapters.kafka.config import KafkaSettings, get_kafka_settings
from selfhealing.adapters.kafka.consumer import (
    ConsumedEvent,
    EventHandler,
    KafkaAuditConsumer,
)
from selfhealing.adapters.kafka.producer import DeliveryReport, KafkaAuditProducer

logger = logging.getLogger(__name__)


class KafkaEventBus:
    """
    Kafka Event Bus.

    Publisher/Subscriber 패턴을 Kafka 기반으로 구현.

    사용 예:
        bus = KafkaEventBus()

        # 이벤트 발행
        await bus.publish("audit.events", {"action": "dlq_store"})

        # 이벤트 구독
        def handler(event: ConsumedEvent) -> bool:
            print(f"Received: {event.value}")
            return True

        bus.subscribe("audit.events", handler)
        bus.start()
    """

    def __init__(self, settings: KafkaSettings | None = None):
        """
        KafkaEventBus 초기화.

        Args:
            settings: Kafka 설정
        """
        self._settings = settings or get_kafka_settings()
        self._producer: KafkaAuditProducer | None = None
        self._consumers: dict[str, KafkaAuditConsumer] = {}
        self._handlers: dict[str, list[EventHandler]] = {}
        self._lock = threading.Lock()
        self._running = False

    def _ensure_producer(self) -> KafkaAuditProducer:
        """Producer 초기화 보장."""
        if self._producer is None:
            self._producer = KafkaAuditProducer(settings=self._settings)
        return self._producer

    def publish(
        self,
        topic: str,
        event: dict[str, Any],
        key: str | None = None,
        on_delivery: Callable[[DeliveryReport], None] | None = None,
    ) -> bool:
        """
        이벤트 발행.

        Args:
            topic: 토픽 이름 (프리픽스 제외)
            event: 이벤트 데이터
            key: 파티션 키
            on_delivery: 전송 결과 콜백

        Returns:
            전송 시작 성공 여부
        """
        producer = self._ensure_producer()
        return producer.publish(
            topic=topic,
            event=event,
            key=key,
            on_delivery=on_delivery,
        )

    async def publish_async(
        self,
        topic: str,
        event: dict[str, Any],
        key: str | None = None,
    ) -> DeliveryReport:
        """
        비동기 이벤트 발행.

        Args:
            topic: 토픽 이름
            event: 이벤트 데이터
            key: 파티션 키

        Returns:
            전송 결과
        """
        import asyncio

        future: asyncio.Future[DeliveryReport] = asyncio.get_event_loop().create_future()

        def on_delivery(report: DeliveryReport) -> None:
            if not future.done():
                future.set_result(report)

        producer = self._ensure_producer()
        success = producer.publish(
            topic=topic,
            event=event,
            key=key,
            on_delivery=on_delivery,
        )

        if not success:
            raise RuntimeError("Failed to publish event")

        # 폴링으로 콜백 처리
        while not future.done():
            producer.poll(timeout=0.1)
            await asyncio.sleep(0.01)

        return await future

    def subscribe(
        self,
        topic: str,
        handler: EventHandler,
    ) -> None:
        """
        토픽 구독.

        Args:
            topic: 토픽 이름 (프리픽스 제외)
            handler: 이벤트 처리 핸들러
        """
        with self._lock:
            if topic not in self._handlers:
                self._handlers[topic] = []
            self._handlers[topic].append(handler)

            logger.info(f"[KafkaEventBus] Subscribed handler to topic: {topic}")

    def _create_consumer_for_topic(self, topic: str) -> KafkaAuditConsumer:
        """토픽용 Consumer 생성."""
        handlers = self._handlers.get(topic, [])

        def combined_handler(event: ConsumedEvent) -> bool:
            """모든 핸들러 호출."""
            results = []
            for handler in handlers:
                try:
                    results.append(handler(event))
                except Exception as e:
                    logger.error(f"[KafkaEventBus] Handler error: {e}")
                    results.append(False)
            return all(results)

        return KafkaAuditConsumer(
            topics=[topic],
            settings=self._settings,
            event_handler=combined_handler,
        )

    def start(self) -> None:
        """Event Bus 시작."""
        if self._running:
            logger.warning("[KafkaEventBus] Already running")
            return

        self._running = True

        # 구독된 토픽별 Consumer 시작
        with self._lock:
            for topic in self._handlers:
                if topic not in self._consumers:
                    consumer = self._create_consumer_for_topic(topic)
                    consumer.start_background()
                    self._consumers[topic] = consumer

        logger.info("[KafkaEventBus] Started")

    def stop(self) -> None:
        """Event Bus 정지."""
        self._running = False

        # 모든 Consumer 정지
        with self._lock:
            for consumer in self._consumers.values():
                consumer.stop()
            self._consumers.clear()

        logger.info("[KafkaEventBus] Stopped")

    def close(self) -> None:
        """Event Bus 종료."""
        self.stop()

        if self._producer:
            self._producer.close()
            self._producer = None

        logger.info("[KafkaEventBus] Closed")

    def flush(self, timeout: float = 10.0) -> None:
        """Producer 플러시."""
        if self._producer:
            self._producer.flush(timeout=timeout)

    def __enter__(self) -> "KafkaEventBus":
        """Context manager 진입."""
        return self

    def __exit__(self, *args) -> None:
        """Context manager 종료."""
        self.close()
```

---

## 5. 기존 코드와의 통합

### 5.1 KafkaCheckpointManager 연동

**파일**: `packages/selfhealing-python/src/selfhealing/audit/kafka_checkpoint.py`

**상태**: ✅ 구현 완료

`sync_wal_to_kafka_with_checkpoint` 함수가 **KafkaAuditProducer**와 기존 **KafkaAuditAdapter** 모두 지원하도록 수정됨:

```python
# kafka_checkpoint.py - 구현 완료

def sync_wal_to_kafka_with_checkpoint(
    wal,
    producer,  # KafkaAuditProducer 또는 KafkaAuditAdapter
    checkpoint: KafkaCheckpointManager,
    namespace: str = "default",
) -> int:
    """
    WAL → Kafka 동기화 (체크포인트 기반).
    KafkaAuditProducer 또는 기존 KafkaAuditAdapter 모두 지원.
    """
    last_cp = checkpoint.get_last_checkpoint(namespace)
    last_seq = last_cp.wal_sequence if last_cp else 0

    entries = wal.recover_unprocessed(last_processed_seq=last_seq)
    synced = 0

    # Producer 타입 감지: KafkaAuditProducer vs 기존 Adapter
    is_new_producer = hasattr(producer, "publish_audit_event")

    for entry in entries:
        try:
            if is_new_producer:
                # 새로운 KafkaAuditProducer 사용
                success = producer.publish_audit_event(
                    event=entry.data,
                    domain=namespace,
                )
                if not success:
                    break
                producer.flush(timeout=5.0)
                kafka_topic = producer._settings.full_audit_topic
            else:
                # 기존 KafkaAuditAdapter 사용 (하위 호환성)
                from selfhealing.interfaces.audit_adapter import AuditEntry
                audit_entry = AuditEntry(**entry.data)
                producer.log(audit_entry)
                producer.flush(timeout=5.0)
                kafka_topic = producer._settings.topic

            # 체크포인트 저장
            checkpoint.save_checkpoint(
                namespace=namespace,
                wal_sequence=entry.sequence,
                kafka_topic=kafka_topic,
                kafka_partition=0,
                kafka_offset=0,
                checksum=entry.checksum,
            )
            synced += 1
        except Exception as e:
            logger.error(f"[WAL→Kafka] Sync failed at seq={entry.sequence}: {e}")
            break

    return synced
```

**테스트**: `tests/unit/audit/test_kafka_adapter.py::TestSyncWalToKafkaWithCheckpoint` (6개 테스트 통과)

### 5.2 Fallback Chain 확장

**파일**: `packages/selfhealing-python/src/selfhealing/audit/graceful_degradation/fallback.py`

Kafka를 Fallback Tier에 추가:

```python
# 확장 제안: HashChainFallbackChain

class HashChainFallbackChain:
    """
    확장된 Multi-tier fallback chain.

    Fallback order:
    1. Redis Primary - Full distributed functionality
    2. Redis Replica - Read-only, degraded writes to local
    3. **Kafka** - Persistent queue (신규 추가)
    4. Local File - Persistent but not distributed
    5. Memory Buffer - Last resort, volatile (→ Disk-Persistent로 대체 예정)
    """
```

---

## 6. 설정 예시

### 6.1 환경변수

```bash
# .env 또는 시스템 환경변수

# Kafka 브로커
SELFHEALING_KAFKA_BOOTSTRAP_SERVERS=kafka-broker-1:9092,kafka-broker-2:9092,kafka-broker-3:9092

# 토픽 설정
SELFHEALING_KAFKA_TOPIC_PREFIX=selfhealing.
SELFHEALING_KAFKA_AUDIT_TOPIC=audit.events

# Producer 설정
SELFHEALING_KAFKA_PRODUCER_ACKS=all
SELFHEALING_KAFKA_PRODUCER_IDEMPOTENT=true
SELFHEALING_KAFKA_PRODUCER_COMPRESSION_TYPE=zstd

# Consumer 설정
SELFHEALING_KAFKA_CONSUMER_GROUP_ID=selfhealing-audit-consumer
SELFHEALING_KAFKA_CONSUMER_ENABLE_AUTO_COMMIT=false

# 보안 (프로덕션)
SELFHEALING_KAFKA_SECURITY_PROTOCOL=SASL_SSL
SELFHEALING_KAFKA_SASL_MECHANISM=SCRAM-SHA-256
SELFHEALING_KAFKA_SASL_USERNAME=selfhealing
SELFHEALING_KAFKA_SASL_PASSWORD=<secret>
```

### 6.2 pyproject.toml

```toml
# packages/selfhealing-python/pyproject.toml

[project.optional-dependencies]
kafka = [
    "confluent-kafka>=2.3.0",
]

# 전체 설치
all = [
    "selfhealing[kafka]",
]
```

---

## 7. 테스트 계획

### 7.1 단위 테스트

```python
# tests/unit/adapters/kafka/test_producer.py

class TestKafkaAuditProducer:
    """KafkaAuditProducer 단위 테스트."""

    def test_publish_success(self, mock_producer):
        """정상 발행 테스트."""
        pass

    def test_publish_with_wal(self, mock_producer, mock_wal):
        """WAL-First 프로토콜 테스트."""
        pass

    def test_delivery_callback(self, mock_producer):
        """전송 결과 콜백 테스트."""
        pass

    def test_graceful_shutdown(self, mock_producer):
        """Graceful shutdown 테스트."""
        pass
```

### 7.2 통합 테스트

```python
# tests/integration/kafka/test_kafka_event_bus.py

@pytest.mark.integration
@pytest.mark.kafka
class TestKafkaEventBusIntegration:
    """KafkaEventBus 통합 테스트."""

    def test_publish_and_consume(self, kafka_cluster):
        """발행-소비 E2E 테스트."""
        pass

    def test_checkpoint_persistence(self, kafka_cluster, redis):
        """체크포인트 영속성 테스트."""
        pass

    def test_broker_failure_recovery(self, kafka_cluster):
        """브로커 장애 복구 테스트."""
        pass
```

---

## 8. 구현 체크리스트

- [x] `adapters/kafka/__init__.py` 생성
- [x] `adapters/kafka/config.py` 구현
- [x] `adapters/kafka/producer.py` 구현
- [x] `adapters/kafka/consumer.py` 구현
- [x] `adapters/kafka/event_bus.py` 구현
- [x] `adapters/kafka/schemas.py` 구현
- [x] `adapters/kafka/metrics.py` 구현
- [x] `adapters/kafka/retry.py` 구현
- [x] `pyproject.toml`에 `confluent-kafka` 의존성 추가 (기존 optional dependency)
- [x] `kafka_checkpoint.py` 통합 수정 (KafkaAuditProducer 지원 추가)
- [x] 단위 테스트 작성 (114개 통과)
- [ ] 통합 테스트 작성 (Testcontainers 기반 - 별도 이슈)
- [x] 문서 업데이트

---

## 9. 운영 고도화 가이드라인

### 9.1 거버넌스 및 소유권 (CCoE/아키텍처 리뷰 보드)

> **코드 근거**: 현재 거버넌스 정책 코드 없음, 조직 정책 문서 추가 필요

**Topic Ownership Matrix**

| 토픽 패턴 | 소유팀 | 리뷰어 | SLA | 설명 |
|-----------|--------|--------|-----|------|
| `selfhealing.audit.*` | Platform | @arch-review | 99.9% | Audit 이벤트 |
| `selfhealing.dlq.*` | Platform | @arch-review | 99.5% | Dead Letter Queue |
| `selfhealing.recovery.*` | SRE | @oncall | 99.0% | 복구 이벤트 |

**아키텍처 리뷰 프로세스**

```
1. 신규 토픽 생성 요청
   └─→ Architecture Review Board (ARB) 리뷰
       └─→ CCoE 승인
           └─→ 토픽 생성 (Terraform)

2. 스키마 변경 요청
   └─→ Schema Registry 호환성 검사 (CI/CD)
       └─→ ARB 리뷰 (Breaking Change 시)
           └─→ 배포
```

**역할 정의**

| 역할 | 책임 | 권한 |
|------|------|------|
| **Topic Owner** | 토픽 스키마 관리, SLA 보장 | 토픽 설정 변경, ACL 요청 |
| **CCoE** | 표준 정책 수립, 기술 가이드라인 | 아키텍처 승인/거부 |
| **SRE** | 운영 모니터링, 장애 대응 | 긴급 스케일링, 토픽 삭제 |

---

### 9.2 명령과 이벤트 구분 (Command vs Event 명명 규칙)

> **코드 근거**: `packages/selfhealing-python/src/selfhealing/audit/event_buffer.py`

```python
# event_buffer.py - AuditEventType Enum (현재 상태)
class AuditEventType(Enum):
    """모든 값이 과거형 이벤트 (사실)로 명명됨 - 올바른 패턴"""

    # ✅ 이벤트 (과거형 - 사실 기록)
    DLQ_STORE = "dlq_store"              # DLQ에 저장되었음
    DLQ_REPLAY = "dlq_replay"            # DLQ가 재생되었음
    CB_STATE_CHANGE = "circuit_breaker_state_change"  # CB 상태가 변경됨
    GOVERNANCE_BLOCKED = "governance_blocked"  # 거버넌스에 의해 차단됨
    API_EXCEPTION = "api_exception"      # API 예외가 발생됨

    # ❌ 명령 (명령형) - 현재 사용 안 함 (올바름)
    # CREATE_ORDER = "order.create"      # 안티패턴 - 사용 금지
```

**명명 규칙 (필수)**

| 구분 | 패턴 | 예시 (✅ 허용) | 예시 (❌ 금지) |
|------|------|---------------|---------------|
| **Event** | `{domain}.{past_tense}` | `order.created`, `dlq.stored` | `order.create` |
| **Command** | - | 사용하지 않음 | `order.create`, `user.delete` |

**안티패턴 방지 체크리스트**

```python
# CI/CD에서 검증할 규칙
FORBIDDEN_PATTERNS = [
    r"\.create$",      # ❌ 명령형
    r"\.delete$",      # ❌ 명령형
    r"\.update$",      # ❌ 명령형
    r"\.execute$",     # ❌ 명령형
]

RECOMMENDED_PATTERNS = [
    r"\.created$",     # ✅ 과거형
    r"\.deleted$",     # ✅ 과거형
    r"\.updated$",     # ✅ 과거형
    r"\.executed$",    # ✅ 과거형
    r"_store$",        # ✅ 현재 코드 패턴
    r"_replay$",       # ✅ 현재 코드 패턴
]
```

---

### 9.3 스키마 레지스트리 실질 통합 (CI/CD 호환성 검사)

> **코드 근거**: `settings/kafka.py`에 `schema_registry_url` 설정만 존재, 실제 직렬화 코드 없음

**현재 상태 (설정만 존재)**

```python
# packages/selfhealing-python/src/selfhealing/settings/kafka.py
class KafkaAuditSettings(BaseSettings):
    schema_registry_url: str | None = Field(
        default=None,
        description="Confluent Schema Registry URL",
    )
    schema_compatibility: str = Field(
        default="BACKWARD",
        description="스키마 호환성 정책: BACKWARD, FORWARD, FULL",
    )
```

**구현 필요 코드 (TODO)**

```python
# adapters/kafka/schemas.py - 신규 구현 필요
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer, AvroDeserializer

class AuditEventSchemaRegistry:
    """
    Audit 이벤트 스키마 레지스트리 통합.

    TODO: 실제 구현 필요
    """

    def __init__(self, settings: KafkaAuditSettings):
        if not settings.schema_registry_url:
            raise ValueError("schema_registry_url is required")

        self._client = SchemaRegistryClient({
            "url": settings.schema_registry_url
        })

    def get_serializer(self, schema_str: str) -> AvroSerializer:
        """Avro 직렬화기 반환."""
        return AvroSerializer(
            self._client,
            schema_str,
            conf={"auto.register.schemas": True}
        )

    def check_compatibility(self, subject: str, schema_str: str) -> bool:
        """스키마 호환성 검사."""
        return self._client.test_compatibility(subject, schema_str)
```

**CI/CD 호환성 검사 파이프라인**

```yaml
# .github/workflows/schema-compatibility.yml
name: Schema Compatibility Check

on:
  pull_request:
    paths:
      - 'schemas/**/*.avsc'

jobs:
  compatibility:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Check Schema Compatibility
        run: |
          for schema in schemas/*.avsc; do
            subject=$(basename "$schema" .avsc)
            curl -X POST \
              -H "Content-Type: application/json" \
              --data @"$schema" \
              "$SCHEMA_REGISTRY_URL/compatibility/subjects/${subject}-value/versions/latest"
          done
```

---

### 9.4 인프라 토폴로지 및 파티셔닝 (클러스터 분리, 파티션 산정)

> **코드 근거**: 현재 파티션 산정 공식 없음

**파티션 산정 공식**

```python
# 파티션 수 산정 공식
def calculate_partition_count(
    expected_tps: int,
    consumer_count: int,
    throughput_per_partition: int = 1000  # 기본 1,000 TPS/파티션
) -> int:
    """
    파티션 수 계산.

    공식: max(expected_TPS / throughput_per_partition, consumer_count * 2)
    """
    by_throughput = expected_tps // throughput_per_partition
    by_consumers = consumer_count * 2  # 2배 여유
    return max(by_throughput, by_consumers, 3)  # 최소 3개
```

**토픽별 파티션 설정**

| 토픽 | 예상 TPS | Consumer 수 | 파티션 수 | 근거 |
|------|----------|-------------|-----------|------|
| `selfhealing.audit.events` | 10,000 | 4 | **12** | max(10, 8) |
| `selfhealing.dlq.events` | 1,000 | 2 | **4** | max(1, 4) |
| `selfhealing.recovery.events` | 500 | 2 | **4** | max(1, 4) |

**클러스터 토폴로지**

```
┌─────────────────────────────────────────────────────────────┐
│                    Production Cluster                        │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐                      │
│  │Broker-1 │  │Broker-2 │  │Broker-3 │  (min 3 for HA)      │
│  │ AZ-a    │  │ AZ-b    │  │ AZ-c    │                      │
│  └─────────┘  └─────────┘  └─────────┘                      │
│                                                              │
│  replication.factor = 3                                      │
│  min.insync.replicas = 2                                     │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                    Development Cluster                       │
│  ┌─────────┐                                                 │
│  │Broker-1 │  (단일 브로커, 개발용)                          │
│  └─────────┘                                                 │
│                                                              │
│  replication.factor = 1                                      │
│  min.insync.replicas = 1                                     │
└─────────────────────────────────────────────────────────────┘
```

---

### 9.5 시간 기반 지연 모니터링 (Time Lag 메트릭)

> **코드 근거**: 현재 Time Lag 모니터링 구현 없음

**Time Lag vs Offset Lag**

| 메트릭 | 설명 | 용도 |
|--------|------|------|
| **Offset Lag** | 미처리 메시지 수 | 처리량 모니터링 |
| **Time Lag** | 가장 오래된 미처리 메시지 시간 | SLA 모니터링 |

**구현 필요 코드**

```python
# adapters/kafka/metrics.py - 신규 구현 필요
from prometheus_client import Gauge, Histogram

# Time Lag 메트릭 정의
kafka_consumer_time_lag_seconds = Gauge(
    "kafka_consumer_time_lag_seconds",
    "Time difference between message timestamp and processing time",
    ["topic", "partition", "consumer_group"]
)

kafka_message_processing_latency = Histogram(
    "kafka_message_processing_latency_seconds",
    "Message processing latency from produce to consume",
    ["topic"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0]
)


class TimeLagTracker:
    """Consumer Time Lag 추적기."""

    def record_message_processed(
        self,
        topic: str,
        partition: int,
        consumer_group: str,
        message_timestamp: float,
    ) -> None:
        """메시지 처리 시 Time Lag 기록."""
        import time
        current_time = time.time()
        time_lag = current_time - message_timestamp

        kafka_consumer_time_lag_seconds.labels(
            topic=topic,
            partition=partition,
            consumer_group=consumer_group
        ).set(time_lag)

        kafka_message_processing_latency.labels(
            topic=topic
        ).observe(time_lag)
```

**알림 규칙 (Prometheus)**

```yaml
# prometheus/rules/kafka-time-lag.yml
groups:
  - name: kafka_time_lag
    rules:
      - alert: KafkaConsumerTimeLagHigh
        expr: kafka_consumer_time_lag_seconds > 60
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Kafka consumer time lag is high"
          description: "Consumer group {{ $labels.consumer_group }} has time lag of {{ $value }}s"

      - alert: KafkaConsumerTimeLagCritical
        expr: kafka_consumer_time_lag_seconds > 300
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Kafka consumer time lag is critical"
          description: "Consumer group {{ $labels.consumer_group }} has time lag of {{ $value }}s (> 5 minutes)"
```

---

### 9.6 ACL 정책 (최소 권한 원칙)

> **코드 근거**: 현재 ACL 구성 코드 없음

**ACL 정책 매트릭스**

| Principal | Resource Type | Resource Pattern | Operation | Permission |
|-----------|---------------|------------------|-----------|------------|
| `User:audit-producer` | Topic | `selfhealing.audit.*` | Write | Allow |
| `User:audit-producer` | Topic | `selfhealing.audit.*` | Describe | Allow |
| `User:audit-consumer` | Topic | `selfhealing.audit.*` | Read | Allow |
| `User:audit-consumer` | Group | `selfhealing-audit-*` | Read | Allow |
| `User:dlq-processor` | Topic | `selfhealing.dlq.*` | All | Allow |
| `User:admin` | Cluster | `kafka-cluster` | All | Allow |

**Kafka ACL 설정 스크립트**

```bash
#!/bin/bash
# scripts/kafka/setup-acls.sh

BOOTSTRAP_SERVER="${KAFKA_BOOTSTRAP_SERVERS:-localhost:9092}"

# Audit Producer ACL
kafka-acls --bootstrap-server $BOOTSTRAP_SERVER \
  --add \
  --allow-principal User:audit-producer \
  --operation Write \
  --operation Describe \
  --topic selfhealing.audit. \
  --resource-pattern-type prefixed

# Audit Consumer ACL
kafka-acls --bootstrap-server $BOOTSTRAP_SERVER \
  --add \
  --allow-principal User:audit-consumer \
  --operation Read \
  --topic selfhealing.audit. \
  --resource-pattern-type prefixed

kafka-acls --bootstrap-server $BOOTSTRAP_SERVER \
  --add \
  --allow-principal User:audit-consumer \
  --operation Read \
  --group selfhealing-audit- \
  --resource-pattern-type prefixed

# DLQ Processor ACL (Read + Write for replay)
kafka-acls --bootstrap-server $BOOTSTRAP_SERVER \
  --add \
  --allow-principal User:dlq-processor \
  --operation All \
  --topic selfhealing.dlq. \
  --resource-pattern-type prefixed
```

**환경별 인증 설정**

```python
# 환경별 보안 프로토콜 설정
ENVIRONMENT_SECURITY = {
    "development": {
        "security_protocol": "PLAINTEXT",  # 개발환경만 허용
        "acl_enabled": False,
    },
    "staging": {
        "security_protocol": "SASL_SSL",
        "acl_enabled": True,
    },
    "production": {
        "security_protocol": "SASL_SSL",  # 필수
        "acl_enabled": True,              # 필수
    },
}
```

---

### 9.7 DR 및 용량 계획 (MirrorMaker 2, RPO/RTO)

> **코드 근거**: `174_MISSING_SYSTEMS_MASTER_PLAN.md#L411` - `replication.factor=3` 언급

**RPO/RTO 목표**

| 시나리오 | RPO (Data Loss) | RTO (Downtime) | 복구 전략 |
|----------|-----------------|----------------|-----------|
| 단일 브로커 장애 | 0 | 0 | 자동 페일오버 (ISR) |
| AZ 장애 | 0 | < 1분 | 멀티 AZ 복제 |
| 리전 장애 | < 1분 | < 5분 | MirrorMaker 2 |
| 전체 클러스터 손실 | < 5분 | < 30분 | 백업 복구 |

**MirrorMaker 2 구성 (DR)**

```properties
# mm2.properties
clusters = primary, dr

primary.bootstrap.servers = kafka-primary:9092
dr.bootstrap.servers = kafka-dr:9092

# 복제 설정
primary->dr.enabled = true
primary->dr.topics = selfhealing\..*

# 동기화 설정
replication.factor = 3
sync.topic.configs.enabled = true
sync.topic.acls.enabled = true

# 오프셋 동기화 (Consumer 페일오버용)
emit.checkpoints.enabled = true
emit.checkpoints.interval.seconds = 10
```

**용량 계획**

```python
# 용량 산정 공식
def calculate_storage_capacity(
    daily_events: int,
    avg_event_size_bytes: int,
    retention_days: int,
    replication_factor: int = 3,
    compression_ratio: float = 0.3,  # zstd 압축
) -> int:
    """
    필요 스토리지 용량 계산 (bytes).

    공식: daily_events * avg_size * retention * replication * compression
    """
    raw_size = daily_events * avg_event_size_bytes * retention_days
    replicated_size = raw_size * replication_factor
    compressed_size = int(replicated_size * compression_ratio)

    # 20% 여유 추가
    return int(compressed_size * 1.2)


# 예시 계산
storage_needed = calculate_storage_capacity(
    daily_events=10_000_000,      # 1000만 이벤트/일
    avg_event_size_bytes=500,     # 평균 500 bytes
    retention_days=7,             # 7일 보관
    replication_factor=3,
    compression_ratio=0.3,
)
# 결과: 약 37.8 GB
```

---

### 9.8 테스트 환경 고도화 (Testcontainers)

> **코드 근거**: 현재 Testcontainers 미사용

**Testcontainers 통합 테스트**

```python
# tests/integration/kafka/conftest.py
import pytest
from testcontainers.kafka import KafkaContainer
from testcontainers.compose import DockerCompose


@pytest.fixture(scope="session")
def kafka_container():
    """단일 Kafka 컨테이너 (단위 테스트용)."""
    with KafkaContainer("confluentinc/cp-kafka:7.5.0") as kafka:
        yield {
            "bootstrap_servers": kafka.get_bootstrap_server(),
        }


@pytest.fixture(scope="session")
def kafka_cluster():
    """Kafka 클러스터 + Schema Registry (통합 테스트용)."""
    compose = DockerCompose(
        "tests/fixtures/docker",
        compose_file_name="docker-compose.kafka.yml",
    )
    compose.start()

    yield {
        "bootstrap_servers": "localhost:9092",
        "schema_registry_url": "http://localhost:8081",
    }

    compose.stop()


# tests/integration/kafka/test_kafka_producer.py
@pytest.mark.integration
class TestKafkaProducerIntegration:
    """KafkaAuditProducer 통합 테스트."""

    def test_produce_and_consume(self, kafka_container):
        """발행-소비 E2E 테스트."""
        from selfhealing.adapters.kafka.producer import KafkaAuditProducer
        from selfhealing.adapters.kafka.consumer import KafkaAuditConsumer

        # Producer 설정
        producer = KafkaAuditProducer(
            bootstrap_servers=kafka_container["bootstrap_servers"]
        )

        # 메시지 발행
        producer.publish(
            topic="test.events",
            key="test-key",
            value={"event": "test_event", "data": "test_data"}
        )
        producer.flush()

        # Consumer 설정 및 소비
        consumer = KafkaAuditConsumer(
            bootstrap_servers=kafka_container["bootstrap_servers"],
            group_id="test-group",
            topics=["test.events"]
        )

        messages = list(consumer.poll(timeout=10.0))
        assert len(messages) == 1
        assert messages[0].value["event"] == "test_event"
```

**docker-compose.kafka.yml (테스트용)**

```yaml
# tests/fixtures/docker/docker-compose.kafka.yml
version: '3.8'
services:
  zookeeper:
    image: confluentinc/cp-zookeeper:7.5.0
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181

  kafka:
    image: confluentinc/cp-kafka:7.5.0
    depends_on:
      - zookeeper
    ports:
      - "9092:9092"
    environment:
      KAFKA_BROKER_ID: 1
      KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://localhost:9092
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1

  schema-registry:
    image: confluentinc/cp-schema-registry:7.5.0
    depends_on:
      - kafka
    ports:
      - "8081:8081"
    environment:
      SCHEMA_REGISTRY_HOST_NAME: schema-registry
      SCHEMA_REGISTRY_KAFKASTORE_BOOTSTRAP_SERVERS: kafka:9092
```

---

### 9.9 재시도 전략 및 순서 보장 (비블로킹 재시도)

> **코드 근거**: DLQ는 존재하나 retry topic 패턴 미구현

**Non-Blocking Retry 토폴로지**

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Non-Blocking Retry Pattern                       │
│                                                                      │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐       │
│  │  Main    │───▶│ Retry-1  │───▶│ Retry-2  │───▶│   DLQ    │       │
│  │  Topic   │    │ (1분후)  │    │ (5분후)  │    │ (최종)   │       │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘       │
│       │               │               │               │              │
│       ▼               ▼               ▼               ▼              │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐       │
│  │ Consumer │    │ Consumer │    │ Consumer │    │ Consumer │       │
│  │  Group   │    │  Group   │    │  Group   │    │  Group   │       │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘       │
│                                                                      │
│  순서 보장: 같은 파티션 키 → 같은 파티션 → 순서 유지                    │
└─────────────────────────────────────────────────────────────────────┘
```

**Retry Topic 구성**

```python
# adapters/kafka/retry.py - 신규 구현 필요
from dataclasses import dataclass
from typing import Callable

@dataclass
class RetryTopicConfig:
    """Retry Topic 설정."""
    main_topic: str
    retry_delays: list[int] = field(default_factory=lambda: [60, 300, 900])  # 1분, 5분, 15분
    dlq_topic: str | None = None

    @property
    def retry_topics(self) -> list[str]:
        """Retry 토픽 목록 생성."""
        return [
            f"{self.main_topic}.retry.{i+1}"
            for i in range(len(self.retry_delays))
        ]

    @property
    def final_dlq_topic(self) -> str:
        """최종 DLQ 토픽."""
        return self.dlq_topic or f"{self.main_topic}.dlq"


class NonBlockingRetryHandler:
    """Non-Blocking Retry 핸들러."""

    def __init__(self, config: RetryTopicConfig, producer: KafkaAuditProducer):
        self._config = config
        self._producer = producer

    def handle_failure(self, message: dict, retry_count: int, error: Exception) -> None:
        """실패 메시지 처리."""
        if retry_count >= len(self._config.retry_delays):
            # 최대 재시도 초과 → DLQ
            self._send_to_dlq(message, error)
        else:
            # Retry 토픽으로 전송
            self._send_to_retry(message, retry_count)

    def _send_to_retry(self, message: dict, retry_count: int) -> None:
        """Retry 토픽으로 전송."""
        retry_topic = self._config.retry_topics[retry_count]
        delay_ms = self._config.retry_delays[retry_count] * 1000

        # 헤더에 재시도 정보 추가
        headers = message.get("headers", {})
        headers["x-retry-count"] = str(retry_count + 1)
        headers["x-retry-delay-ms"] = str(delay_ms)
        headers["x-original-topic"] = self._config.main_topic

        self._producer.publish(
            topic=retry_topic,
            key=message.get("key"),
            value=message.get("value"),
            headers=headers,
        )

    def _send_to_dlq(self, message: dict, error: Exception) -> None:
        """DLQ로 전송."""
        headers = message.get("headers", {})
        headers["x-dlq-reason"] = str(error)
        headers["x-original-topic"] = self._config.main_topic

        self._producer.publish(
            topic=self._config.final_dlq_topic,
            key=message.get("key"),
            value=message.get("value"),
            headers=headers,
        )
```

---

### 9.10 보안 설정 강제화 (SASL_SSL 기본값)

> **코드 근거**: `settings/kafka.py#L173-175` - `security_protocol` 기본값이 `PLAINTEXT`

**현재 상태 (⚠️ 보안 취약)**

```python
# packages/selfhealing-python/src/selfhealing/settings/kafka.py (현재)
security_protocol: str = Field(
    default="PLAINTEXT",  # ⚠️ 프로덕션에서 위험
    description="보안 프로토콜: PLAINTEXT, SSL, SASL_PLAINTEXT, SASL_SSL",
)
```

**권장 변경사항**

```python
# 권장 변경 (환경별 분기)
import os

def get_default_security_protocol() -> str:
    """환경에 따른 기본 보안 프로토콜."""
    env = os.getenv("ENVIRONMENT", "development")
    if env in ("production", "staging"):
        return "SASL_SSL"  # 프로덕션/스테이징은 필수
    return "PLAINTEXT"  # 개발환경만 허용


security_protocol: str = Field(
    default_factory=get_default_security_protocol,
    description="보안 프로토콜: PLAINTEXT(개발만), SASL_SSL(프로덕션)",
)
```

**프로덕션 체크리스트**

| 항목 | 개발 | 스테이징 | 프로덕션 |
|------|------|----------|----------|
| `security_protocol` | PLAINTEXT | SASL_SSL | **SASL_SSL** |
| `sasl_mechanism` | - | SCRAM-SHA-512 | **SCRAM-SHA-512** |
| `ssl_cafile` | - | 설정 | **필수** |
| ACL 활성화 | ❌ | ✅ | **✅ 필수** |
| 암호 복잡도 | - | 중 | **강** (16자+) |

**환경변수 예시 (프로덕션)**

```bash
# .env.production
SELFHEALING_KAFKA_SECURITY_PROTOCOL=SASL_SSL
SELFHEALING_KAFKA_SASL_MECHANISM=SCRAM-SHA-512
SELFHEALING_KAFKA_SASL_USERNAME=selfhealing-prod
SELFHEALING_KAFKA_SASL_PASSWORD=${KAFKA_PASSWORD}  # Vault에서 주입
SELFHEALING_KAFKA_SSL_CAFILE=/etc/kafka/certs/ca.crt
```

---

### 9.11 DLQ 운영 체계화 (SOP 추가)

> **코드 근거**: `api/django/urls.py` - DLQListView, DLQRetryView, DLQReplayView 존재, 운영 절차서 없음

**DLQ API 현황**

```python
# packages/selfhealing-python/src/selfhealing/api/django/urls.py
path("dlq/list/", DLQListView.as_view(), name="dlq-list"),
path("dlq/replay/", DLQReplayView.as_view(), name="dlq-replay"),
path("dlq/retry/", DLQRetryView.as_view(), name="dlq-retry"),
```

**DLQ 운영 SOP (Standard Operating Procedure)**

```
┌─────────────────────────────────────────────────────────────────────┐
│                     DLQ 운영 절차서 (SOP)                            │
└─────────────────────────────────────────────────────────────────────┘

1. 알림 수신
   └─→ PagerDuty/Slack 알림: "DLQ count > threshold"
   └─→ 담당자: On-Call SRE

2. 초기 진단 (5분 이내)
   ├─→ DLQ 대시보드 확인: GET /api/v1/dlq/list/
   ├─→ 실패 원인 분류:
   │   ├─ Transient Error (일시적): 네트워크, 타임아웃, 리소스 부족
   │   └─ Permanent Error (영구적): 스키마 불일치, 데이터 오류, 버그
   └─→ 영향 범위 파악: 도메인, 개수, 시간대

3. 대응 (원인별)
   ├─ Transient Error:
   │   ├─→ 자동 재시도 대기 (3회, 지수 백오프)
   │   ├─→ 수동 재시도: POST /api/v1/dlq/retry/ {"dlq_ids": [...]}
   │   └─→ 인프라 이슈 시: 스케일업/장애 복구 후 재시도
   │
   └─ Permanent Error:
       ├─→ 데이터 검토: 원본 메시지 분석
       ├─→ 옵션 1: 데이터 수정 후 재투입
       │   └─→ POST /api/v1/dlq/replay/ {"dlq_ids": [...], "transform": {...}}
       ├─→ 옵션 2: 폐기 (비즈니스 승인 필요)
       │   └─→ DELETE /api/v1/dlq/{id}/ + 폐기 사유 기록
       └─→ 옵션 3: 버그 수정 후 재처리
           └─→ 핫픽스 배포 → 재시도

4. 사후 조치
   ├─→ 인시던트 리포트 작성
   ├─→ RCA (Root Cause Analysis) 수행
   └─→ 재발 방지 조치 (코드/인프라 개선)

5. 에스컬레이션 기준
   ├─ L1 → L2: DLQ 100개 이상 또는 30분 이상 미해결
   ├─ L2 → L3: DLQ 1,000개 이상 또는 핵심 비즈니스 영향
   └─ L3 → Incident Commander: 전체 시스템 영향
```

**DLQ 모니터링 대시보드 항목**

| 메트릭 | 쿼리 | 알림 임계값 |
|--------|------|-------------|
| DLQ 총 개수 | `dlq_total_count` | > 100 (warning), > 1000 (critical) |
| DLQ 증가율 | `rate(dlq_total_count[5m])` | > 10/min |
| 도메인별 분포 | `dlq_count_by_domain` | - |
| 평균 체류 시간 | `dlq_avg_age_seconds` | > 3600 (1시간) |
| 재시도 성공률 | `dlq_retry_success_rate` | < 90% |

---

### 9.12 인프라 가용성 설정 (min.insync.replicas)

> **코드 근거**: `174_MISSING_SYSTEMS_MASTER_PLAN.md#L411` - `replication.factor=3` 언급, 애플리케이션 코드에 broker 설정 없음

**권장 Kafka 브로커 설정**

| 설정 | 개발 | 스테이징 | 프로덕션 | 설명 |
|------|------|----------|----------|------|
| `replication.factor` | 1 | 2 | **3** | 복제본 수 |
| `min.insync.replicas` | 1 | 1 | **2** | 최소 동기화 복제본 |
| `acks` (Producer) | 1 | all | **all** | ACK 정책 |
| `unclean.leader.election.enable` | true | false | **false** | 비동기 리더 선출 |

**Kafka 브로커 설정 (server.properties)**

```properties
# config/server.properties (프로덕션)

# 복제 설정
default.replication.factor=3
min.insync.replicas=2
unclean.leader.election.enable=false

# 토픽별 설정 (selfhealing.* 토픽)
# kafka-configs.sh로 적용
# kafka-configs --alter --entity-type topics --entity-name selfhealing.audit.events \
#   --add-config min.insync.replicas=2,replication.factor=3
```

**Producer 설정과의 관계**

```python
# Producer acks=all + min.insync.replicas=2 조합
#
# 동작 방식:
# 1. Producer가 메시지 전송 (acks=all)
# 2. Leader가 메시지 수신
# 3. min.insync.replicas(2개) 이상의 ISR이 복제 완료
# 4. Leader가 Producer에게 ACK 전송
#
# 장점:
# - 1개 브로커 장애 시에도 데이터 손실 없음
# - Leader 장애 시 ISR 중 하나가 새 Leader로 승격
#
# 트레이드오프:
# - ISR < min.insync.replicas 시 Producer 쓰기 불가
# - 네트워크 지연 증가 (복제 대기)

# settings/kafka.py에서 보장
producer_acks: Literal["0", "1", "all"] = Field(
    default="all",  # min.insync.replicas와 함께 사용 시 데이터 무손실 보장
    description="Producer ACK 레벨 (all 권장)",
)
producer_idempotent: bool = Field(
    default=True,  # Exactly-once 의미론
    description="Idempotent Producer 활성화",
)
```

**가용성 시나리오**

```
Scenario 1: 정상 운영 (3 브로커, ISR=3)
├─ 쓰기: ✅ 가능
├─ 읽기: ✅ 가능
└─ 데이터 손실: 없음

Scenario 2: 1 브로커 장애 (3 브로커, ISR=2)
├─ 쓰기: ✅ 가능 (min.insync.replicas=2 충족)
├─ 읽기: ✅ 가능
└─ 데이터 손실: 없음

Scenario 3: 2 브로커 장애 (3 브로커, ISR=1)
├─ 쓰기: ❌ 불가 (min.insync.replicas=2 미충족)
├─ 읽기: ✅ 가능
└─ 데이터 손실: 없음 (쓰기 거부로 보호)

Scenario 4: 전체 브로커 장애
├─ 쓰기: ❌ 불가
├─ 읽기: ❌ 불가
└─ 데이터 손실: 없음 (디스크 복구 필요)
```

---

## 10. 관련 문서

- [170_KAFKA_AUDIT_ADAPTER.md](170_KAFKA_AUDIT_ADAPTER.md) - Kafka 어댑터 설계
- [173_UNIFIED_CHECKPOINT_STRATEGY.md](173_UNIFIED_CHECKPOINT_STRATEGY.md) - Checkpoint 통합
- [174_MISSING_SYSTEMS_MASTER_PLAN.md](174_MISSING_SYSTEMS_MASTER_PLAN.md) - 마스터 플랜

---

## 11. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0.0 | 2026-02-04 | 초안 작성 |
| 1.1.0 | 2026-02-04 | 운영 고도화 가이드라인 추가 (9.1~9.12) |
