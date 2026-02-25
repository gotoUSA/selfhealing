"""
Kafka Audit Consumer.

Kafka에서 감사 이벤트를 소비하여 다양한 저장소로 전달하는 Consumer 구현.

주요 컴포넌트:
- BaseAuditConsumer: Consumer 기본 클래스
- IdempotentAuditConsumer: 중복 메시지 필터링 Consumer
- RebalanceAwareConsumer: 리밸런싱 안전 Consumer
- PostgreSQLSinkConsumer: PostgreSQL 저장 Consumer

Usage:
    from selfhealing.adapters.audit.kafka_consumer import PostgreSQLSinkConsumer

    consumer = PostgreSQLSinkConsumer(
        bootstrap_servers=["kafka:9092"],
        group_id="audit-db-sink",
        db_url="postgresql://...",
    )
    consumer.run()
"""

from __future__ import annotations

import json
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from confluent_kafka import Consumer, Message

logger = structlog.get_logger()


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class KafkaConsumerConfig:
    """Kafka Consumer 설정."""

    bootstrap_servers: list[str] = field(default_factory=lambda: ["localhost:9092"])
    group_id: str = "selfhealing-audit-consumer"
    topic: str = "selfhealing.audit.events"
    auto_offset_reset: str = "earliest"
    enable_auto_commit: bool = False
    session_timeout_ms: int = 45000
    heartbeat_interval_ms: int = 15000
    max_poll_interval_ms: int = 300000
    batch_size: int = 500
    poll_timeout_seconds: float = 1.0

    # 리밸런싱 전략 (cooperative-sticky 권장)
    partition_assignment_strategy: str = "cooperative-sticky"

    # Security
    security_protocol: str = "PLAINTEXT"
    sasl_mechanism: str | None = None
    sasl_username: str | None = None
    sasl_password: str | None = None

    def get_consumer_config(self) -> dict[str, Any]:
        """confluent-kafka Consumer 설정 딕셔너리 반환."""
        config = {
            "bootstrap.servers": ",".join(self.bootstrap_servers),
            "group.id": self.group_id,
            "auto.offset.reset": self.auto_offset_reset,
            "enable.auto.commit": self.enable_auto_commit,
            "session.timeout.ms": self.session_timeout_ms,
            "heartbeat.interval.ms": self.heartbeat_interval_ms,
            "max.poll.interval.ms": self.max_poll_interval_ms,
            "partition.assignment.strategy": self.partition_assignment_strategy,
        }

        if self.security_protocol != "PLAINTEXT":
            config["security.protocol"] = self.security_protocol
            if self.sasl_mechanism:
                config["sasl.mechanism"] = self.sasl_mechanism
            if self.sasl_username:
                config["sasl.username"] = self.sasl_username
            if self.sasl_password:
                config["sasl.password"] = self.sasl_password

        return config


# =============================================================================
# Base Consumer
# =============================================================================


class BaseAuditConsumer(ABC):
    """
    Kafka Audit Consumer 기본 클래스.

    Consumer 구현체는 이 클래스를 상속하여 process_message()를 구현합니다.
    """

    def __init__(
        self,
        config: KafkaConsumerConfig | None = None,
        consumer: Consumer | None = None,
    ):
        """
        BaseAuditConsumer 초기화.

        Args:
            config: Consumer 설정
            consumer: 외부 주입 Consumer (테스트용)
        """
        self._config = config or KafkaConsumerConfig()
        self._consumer = consumer
        self._consumer_initialized = consumer is not None
        self._running = False
        self._lock = threading.Lock()

        # 통계
        self._processed_count = 0
        self._error_count = 0
        self._skipped_count = 0

    def _ensure_consumer(self) -> Consumer:
        """Consumer 지연 초기화."""
        if not self._consumer_initialized:
            with self._lock:
                if not self._consumer_initialized:
                    self._consumer = self._create_consumer()
                    self._consumer_initialized = True
        return self._consumer  # type: ignore

    def _create_consumer(self) -> Consumer:
        """confluent-kafka Consumer 생성."""
        try:
            from confluent_kafka import Consumer
        except ImportError as e:
            raise ImportError(
                "confluent-kafka is required for KafkaAuditConsumer. " "Install it with: pip install 'selfhealing[kafka]'"
            ) from e

        consumer = Consumer(self._config.get_consumer_config())
        consumer.subscribe(
            [self._config.topic],
            on_assign=self._on_assign,
            on_revoke=self._on_revoke,
        )
        return consumer

    def _on_assign(self, consumer: Consumer, partitions: list) -> None:
        """파티션 할당 콜백 (서브클래스에서 오버라이드 가능)."""
        partition_info = [(p.topic, p.partition, p.offset) for p in partitions]
        logger.info(
            "consumer.partitions_assigned",
            partition_info=partition_info,
        )

    def _on_revoke(self, consumer: Consumer, partitions: list) -> None:
        """파티션 해제 콜백 (서브클래스에서 오버라이드 가능)."""
        partition_info = [(p.topic, p.partition) for p in partitions]
        logger.info(
            "consumer.partitions_revoked",
            partition_info=partition_info,
        )

    @abstractmethod
    def process_message(self, message: Message) -> bool:
        """
        메시지 처리 (서브클래스에서 구현).

        Args:
            message: Kafka 메시지

        Returns:
            True: 처리 성공
            False: 처리 실패 (재시도 필요)
        """
        pass

    def run(self) -> None:
        """Consumer 실행 (메인 루프)."""
        self._running = True
        consumer = self._ensure_consumer()

        logger.info(
            "consumer.starting_consumer_topic",
            _self=self._config.topic,
        )

        try:
            while self._running:
                msg = consumer.poll(timeout=self._config.poll_timeout_seconds)

                if msg is None:
                    continue

                if msg.error():
                    logger.error(
                        "consumer.message_error",
                        msg=msg.error(),
                    )
                    self._error_count += 1
                    continue

                try:
                    success = self.process_message(msg)
                    if success:
                        self._processed_count += 1
                        # 수동 커밋
                        consumer.commit(message=msg, asynchronous=False)
                    else:
                        self._error_count += 1

                except Exception as e:
                    logger.exception(
                        "consumer.process_error",
                        error=e,
                    )
                    self._error_count += 1

        except KeyboardInterrupt:
            logger.info("consumer")
        finally:
            self.close()

    def stop(self) -> None:
        """Consumer 중지 요청."""
        self._running = False

    def close(self) -> None:
        """리소스 정리."""
        self._running = False
        if self._consumer_initialized and self._consumer:
            try:
                self._consumer.close()
            except Exception as e:
                logger.warning(
                    "consumer.close_error",
                    error=e,
                )

        logger.info(
            "consumer.closed_processed_errors_skipped",
            _self=self._processed_count,
            error_count=self._error_count,
            skipped_count=self._skipped_count,
        )

    def get_stats(self) -> dict[str, Any]:
        """통계 반환."""
        return {
            "processed_count": self._processed_count,
            "error_count": self._error_count,
            "skipped_count": self._skipped_count,
            "running": self._running,
        }


# =============================================================================
# Idempotent Consumer (중복 메시지 필터링)
# =============================================================================


class IdempotentAuditConsumer(BaseAuditConsumer):
    """
    멱등성 보장 Consumer.

    이미 처리된 메시지를 식별하여 중복 처리를 방지합니다.
    Redis 또는 인메모리 캐시를 사용하여 처리 이력을 관리합니다.
    """

    def __init__(
        self,
        config: KafkaConsumerConfig | None = None,
        consumer: Consumer | None = None,
        redis_client=None,
        idempotency_ttl_seconds: int = 86400 * 7,  # 7일
        cache_max_size: int = 100000,
    ):
        """
        IdempotentAuditConsumer 초기화.

        Args:
            config: Consumer 설정
            consumer: 외부 주입 Consumer (테스트용)
            redis_client: Redis 클라이언트 (None이면 인메모리 캐시 사용)
            idempotency_ttl_seconds: 멱등성 키 TTL (초)
            cache_max_size: 인메모리 캐시 최대 크기
        """
        super().__init__(config, consumer)
        self._redis = redis_client
        self._ttl_seconds = idempotency_ttl_seconds
        self._cache_max_size = cache_max_size
        self._memory_cache: dict[str, float] = {}  # key -> timestamp

    def _get_idempotency_key(self, message: Message) -> str:
        """메시지에서 멱등성 키 추출."""
        try:
            value = json.loads(message.value().decode("utf-8"))
            event_id = value.get("id")
            if event_id:
                return f"audit:consumer:{event_id}"

            # ID가 없으면 action + timestamp 조합
            action = value.get("action", "unknown")
            timestamp = value.get("timestamp", time.time())
            return f"audit:consumer:{action}:{timestamp}"
        except Exception:
            # 파싱 실패 시 오프셋 기반 키
            return f"audit:consumer:{message.topic()}:{message.partition()}:{message.offset()}"

    def _is_processed(self, key: str) -> bool:
        """이미 처리된 메시지인지 확인."""
        if self._redis:
            return self._redis.exists(key) > 0

        # 인메모리 캐시
        if key in self._memory_cache:
            return True

        return False

    def _mark_processed(self, key: str) -> None:
        """메시지 처리 완료 표시."""
        if self._redis:
            self._redis.setex(key, self._ttl_seconds, "1")
            return

        # 인메모리 캐시 (크기 제한)
        if len(self._memory_cache) >= self._cache_max_size:
            # 가장 오래된 10% 삭제
            sorted_keys = sorted(self._memory_cache.items(), key=lambda x: x[1])
            keys_to_remove = sorted_keys[: self._cache_max_size // 10]
            for k, _ in keys_to_remove:
                del self._memory_cache[k]

        self._memory_cache[key] = time.time()

    def process_message(self, message: Message) -> bool:
        """
        멱등성 보장 메시지 처리.

        이미 처리된 메시지는 스킵합니다.
        """
        key = self._get_idempotency_key(message)

        if self._is_processed(key):
            logger.debug(
                "idempotent_consumer.skipping_duplicate",
                key=key,
            )
            self._skipped_count += 1
            return True  # 스킵해도 성공으로 간주 (커밋 진행)

        try:
            success = self._do_process(message)
            if success:
                self._mark_processed(key)
            return success
        except Exception as e:
            logger.exception(
                "idempotent_consumer.process_error",
                error=e,
            )
            return False

    def _do_process(self, message: Message) -> bool:
        """
        실제 메시지 처리 (서브클래스에서 오버라이드).

        기본 구현은 로깅만 수행.
        """
        try:
            value = json.loads(message.value().decode("utf-8"))
            logger.info(
                "idempotent_consumer.processed",
                value=value.get("action"),
            )
            return True
        except Exception as e:
            logger.exception(
                "idempotent_consumer.parse_error",
                error=e,
            )
            return False


# =============================================================================
# Rebalance-Aware Consumer (리밸런싱 안전)
# =============================================================================


class RebalanceAwareConsumer(BaseAuditConsumer):
    """
    리밸런싱 시 데이터 처리 지연을 최소화하는 Consumer.

    특징:
    - cooperative-sticky 리밸런싱 전략 사용
    - 파티션 해제 전 현재 배치 커밋
    - 파티션 할당 시 오프셋 복구
    """

    def __init__(
        self,
        config: KafkaConsumerConfig | None = None,
        consumer: Consumer | None = None,
        on_rebalance: Callable[[str, list], None] | None = None,
    ):
        """
        RebalanceAwareConsumer 초기화.

        Args:
            config: Consumer 설정
            consumer: 외부 주입 Consumer (테스트용)
            on_rebalance: 리밸런싱 시 호출되는 콜백
        """
        super().__init__(config, consumer)
        self._on_rebalance_callback = on_rebalance
        self._pending_offsets: dict[tuple, Any] = {}  # (topic, partition) -> offset

    def _on_assign(self, consumer: Consumer, partitions: list) -> None:
        """파티션 할당 시 오프셋 복구."""
        for p in partitions:
            # 마지막 커밋된 오프셋에서 시작
            try:
                committed = consumer.committed([p])
                if committed and committed[0] and committed[0].offset >= 0:
                    p.offset = committed[0].offset
                    logger.debug(
                        "rebalance_consumer.partition_resuming_offset",
                        p=p.partition,
                        offset=p.offset,
                    )
            except Exception as e:
                logger.warning(
                    "rebalance_consumer.failed_get_committed_offset",
                    error=e,
                )

        consumer.assign(partitions)
        logger.info(
            "rebalance_consumer.assigned_partitions",
            count=len(partitions),
        )

        if self._on_rebalance_callback:
            try:
                self._on_rebalance_callback("assign", partitions)
            except Exception as e:
                logger.exception(
                    "rebalance_consumer.rebalance_callback_error",
                    error=e,
                )

    def _on_revoke(self, consumer: Consumer, partitions: list) -> None:
        """파티션 해제 전 현재 배치 커밋."""
        if self._pending_offsets:
            try:
                offsets_to_commit = list(self._pending_offsets.values())
                consumer.commit(offsets=offsets_to_commit, asynchronous=False)
                logger.info(
                    "rebalance_consumer.committed_pending_offsets_before",
                    count=len(offsets_to_commit),
                )
            except Exception as e:
                logger.exception(
                    "rebalance_consumer.failed_commit_revoke",
                    error=e,
                )
            finally:
                self._pending_offsets.clear()

        logger.info(
            "rebalance_consumer.revoked_partitions",
            count=len(partitions),
        )

        if self._on_rebalance_callback:
            try:
                self._on_rebalance_callback("revoke", partitions)
            except Exception as e:
                logger.exception(
                    "rebalance_consumer.rebalance_callback_error",
                    error=e,
                )

    def process_message(self, message: Message) -> bool:
        """
        메시지 처리 (서브클래스에서 오버라이드).

        기본 구현은 로깅만 수행.
        """
        try:
            value = json.loads(message.value().decode("utf-8"))
            logger.info(
                "rebalance_consumer.processed",
                value=value.get("action"),
            )

            # pending offset 기록 (리밸런싱 시 커밋용)
            try:
                from confluent_kafka import TopicPartition

                key = (message.topic(), message.partition())
                self._pending_offsets[key] = TopicPartition(
                    message.topic(),
                    message.partition(),
                    message.offset() + 1,
                )
            except ImportError:
                pass

            return True
        except Exception as e:
            logger.exception(
                "rebalance_consumer.parse_error",
                error=e,
            )
            return False


# =============================================================================
# PostgreSQL Sink Consumer
# =============================================================================


@dataclass
class PostgreSQLSinkConfig:
    """PostgreSQL Sink 설정."""

    db_url: str = "postgresql://localhost/audit"
    table_name: str = "audit_log"
    batch_size: int = 500
    upsert_on_conflict: bool = True  # ON CONFLICT DO NOTHING


class PostgreSQLSinkConsumer(IdempotentAuditConsumer):
    """
    Kafka 메시지를 PostgreSQL에 저장하는 Sink Consumer.

    특징:
    - 배치 INSERT로 성능 최적화
    - ON CONFLICT DO NOTHING으로 멱등성 보장
    - psycopg2 execute_values 사용
    """

    def __init__(
        self,
        kafka_config: KafkaConsumerConfig | None = None,
        sink_config: PostgreSQLSinkConfig | None = None,
        consumer: Consumer | None = None,
        db_connection=None,
    ):
        """
        PostgreSQLSinkConsumer 초기화.

        Args:
            kafka_config: Kafka Consumer 설정
            sink_config: PostgreSQL Sink 설정
            consumer: 외부 주입 Consumer (테스트용)
            db_connection: 외부 주입 DB 연결 (테스트용)
        """
        super().__init__(kafka_config, consumer)
        self._sink_config = sink_config or PostgreSQLSinkConfig()
        self._db_connection = db_connection
        self._batch: list[tuple] = []

    def _ensure_db_connection(self):
        """DB 연결 지연 초기화."""
        if self._db_connection is None:
            try:
                import psycopg2

                self._db_connection = psycopg2.connect(self._sink_config.db_url)
            except ImportError as e:
                raise ImportError(
                    "psycopg2 is required for PostgreSQLSinkConsumer. " "Install it with: pip install psycopg2-binary"
                ) from e
        return self._db_connection

    def _do_process(self, message: Message) -> bool:
        """메시지를 배치에 추가하고, 배치가 차면 DB에 저장."""
        try:
            value = json.loads(message.value().decode("utf-8"))

            # 배치에 추가
            self._batch.append(
                (
                    value.get("id"),
                    value.get("action"),
                    value.get("target_type"),
                    value.get("target_id"),
                    value.get("actor_id"),
                    json.dumps(value.get("details", {})),
                    value.get("timestamp"),
                    value.get("service_name"),
                    value.get("success", True),
                    value.get("error_message"),
                )
            )

            # 배치가 차면 DB에 저장
            if len(self._batch) >= self._sink_config.batch_size:
                return self._flush_batch()

            return True

        except Exception as e:
            logger.exception(
                "postgre_sql_sink.parse_error",
                error=e,
            )
            return False

    def _flush_batch(self) -> bool:
        """배치를 DB에 저장."""
        if not self._batch:
            return True

        try:
            from psycopg2.extras import execute_values

            conn = self._ensure_db_connection()

            with conn.cursor() as cur:
                sql = f"""
                    INSERT INTO {self._sink_config.table_name}
                    (id, action, target_type, target_id, actor_id,
                     details, timestamp, service_name, success, error_message)
                    VALUES %s
                """
                if self._sink_config.upsert_on_conflict:
                    sql += " ON CONFLICT (id) DO NOTHING"

                execute_values(cur, sql, self._batch)
                conn.commit()

            logger.info(
                "postgre_sql_sink.flushed_records_db",
                count=len(self._batch),
            )
            self._batch.clear()
            return True

        except Exception as e:
            logger.exception(
                "postgre_sql_sink.db_error",
                error=e,
            )
            try:
                self._db_connection.rollback()
            except Exception:
                pass
            return False

    def close(self) -> None:
        """리소스 정리."""
        # 남은 배치 플러시
        if self._batch:
            self._flush_batch()

        # DB 연결 닫기
        if self._db_connection:
            try:
                self._db_connection.close()
            except Exception:
                pass

        super().close()
