"""
Kafka Audit Consumer.

수동 오프셋 관리를 통해 메시지 손실 없이 Audit 이벤트를 소비합니다.

핵심 특징:
- 수동 오프셋 관리: 처리 완료 후 커밋
- Checkpoint 통합: KafkaCheckpointManager 연동
- Causation 복원: Kafka 헤더에서 컨텍스트 복원
- Graceful Shutdown: 진행 중인 처리 완료 대기

Usage:
    from selfhealing.adapters.kafka.consumer import KafkaAuditConsumer

    def handler(event):
        print(f"Received: {event.value}")
        return True  # 처리 성공

    with KafkaAuditConsumer(
        topics=["audit.events"],
        event_handler=handler,
    ) as consumer:
        consumer.run()  # 블로킹 루프
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from confluent_kafka import Consumer, Message

    from selfhealing.audit.kafka_checkpoint import KafkaCheckpointManager

from selfhealing.adapters.kafka.config import KafkaSettings, get_kafka_settings

logger = structlog.get_logger()


@dataclass
class ConsumedEvent:
    """
    Kafka에서 소비된 이벤트.

    Consumer에서 폴링한 메시지를 파싱하여 생성합니다.
    """

    topic: str
    """토픽 이름."""

    partition: int
    """파티션 번호."""

    offset: int
    """오프셋."""

    key: str | None
    """메시지 키."""

    value: dict[str, Any]
    """메시지 값 (JSON 파싱됨)."""

    headers: dict[str, bytes]
    """메시지 헤더."""

    timestamp: float
    """메시지 타임스탬프 (Unix timestamp)."""


# 이벤트 핸들러 타입 정의
EventHandler = Callable[[ConsumedEvent], bool]


class KafkaAuditConsumer:
    """
    Kafka Audit 이벤트 Consumer.

    수동 오프셋 관리를 통해 처리가 완료된 메시지만 커밋합니다.
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
            topics: 구독할 토픽 목록 (프리픽스 제외, 자동 추가됨)
            settings: Kafka 설정 (None이면 기본값)
            checkpoint_manager: Checkpoint 관리자 (오프셋 영속화용)
            event_handler: 이벤트 처리 핸들러 (True 반환 시 커밋)
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
        """Kafka Consumer 초기화."""
        try:
            from confluent_kafka import Consumer

            config = self._build_consumer_config()
            self._consumer = Consumer(config)

            # 토픽 구독
            full_topics = [f"{self._settings.topic_prefix}{t}" for t in self._topics]
            self._consumer.subscribe(full_topics)

            logger.info(
                "kafka_consumer.토픽_구독_완료",
                full_topics=full_topics,
            )
        except ImportError:
            logger.exception(
                "[KafkaConsumer] confluent-kafka 패키지가 설치되지 않았습니다. " "설치: pip install 'selfhealing[kafka]'"
            )
            raise
        except Exception as e:
            logger.exception(
                "kafka_consumer.초기화_실패",
                error=e,
            )
            raise

    def _build_consumer_config(self) -> dict[str, Any]:
        """Consumer 설정 딕셔너리 생성."""
        config: dict[str, Any] = {
            "bootstrap.servers": self._settings.bootstrap_servers,
            "group.id": self._settings.consumer_group_id,
            "auto.offset.reset": self._settings.consumer_auto_offset_reset,
            "enable.auto.commit": self._settings.consumer_enable_auto_commit,
            # Note: max.poll.records is not supported in confluent-kafka Python
            # Use batch_size parameter in consume() method instead
            "session.timeout.ms": self._settings.consumer_session_timeout_ms,
        }

        # 보안 설정 적용
        if self._settings.security_protocol != "PLAINTEXT":
            config["security.protocol"] = self._settings.security_protocol
            if self._settings.sasl_mechanism:
                config["sasl.mechanism"] = self._settings.sasl_mechanism
                config["sasl.username"] = self._settings.sasl_username
                config["sasl.password"] = self._settings.sasl_password
            if self._settings.ssl_cafile:
                config["ssl.ca.location"] = self._settings.ssl_cafile

        return config

    def _parse_message(self, msg: Message) -> ConsumedEvent | None:
        """Kafka 메시지를 ConsumedEvent로 변환."""
        try:
            # 값 파싱
            value = json.loads(msg.value().decode("utf-8"))

            # 헤더 파싱
            headers: dict[str, bytes] = {}
            if msg.headers():
                for key, val in msg.headers():
                    headers[key] = val if val else b""

            # 타임스탬프 추출 (ms -> s)
            timestamp_tuple = msg.timestamp()
            timestamp = timestamp_tuple[1] / 1000.0 if timestamp_tuple[0] != 0 else time.time()

            return ConsumedEvent(
                topic=msg.topic(),
                partition=msg.partition(),
                offset=msg.offset(),
                key=msg.key().decode("utf-8") if msg.key() else None,
                value=value,
                headers=headers,
                timestamp=timestamp,
            )
        except Exception as e:
            logger.exception(
                "kafka_consumer.메시지_파싱_오류",
                error=e,
            )
            return None

    def _process_message(self, event: ConsumedEvent) -> bool:
        """
        메시지 처리.

        Causation Context를 복원한 후 핸들러를 호출합니다.
        """
        from selfhealing.context.causation_context import restore_causation_from_kafka

        # Kafka 헤더를 리스트 형식으로 변환
        header_list = [(k, v) for k, v in event.headers.items()]

        with restore_causation_from_kafka(header_list):
            if self._event_handler:
                try:
                    return self._event_handler(event)
                except Exception as e:
                    logger.exception(
                        "kafka_consumer.핸들러_오류",
                        error=e,
                    )
                    return False
            else:
                # 핸들러 없으면 로깅만
                logger.debug(
                    "kafka_consumer.수신",
                    event_topic=event.topic,
                    partition=event.partition,
                    offset=event.offset,
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

            # Checkpoint 저장 (있는 경우)
            if self._checkpoint_manager:
                namespace = event.topic.replace(self._settings.topic_prefix, "")
                self._checkpoint_manager.save_checkpoint(
                    namespace=namespace,
                    wal_sequence=0,  # Kafka 전용 모드에서는 미사용
                    kafka_topic=event.topic,
                    kafka_partition=event.partition,
                    kafka_offset=event.offset,
                    checksum="",
                )

            logger.debug(
                "kafka_consumer.커밋_완료",
                event_topic=event.topic,
                partition=event.partition,
                offset=event.offset,
            )
        except Exception as e:
            logger.exception(
                "kafka_consumer.커밋_오류",
                error=e,
            )

    def poll_once(self, timeout: float = 1.0) -> ConsumedEvent | None:
        """
        단일 메시지 폴링.

        Args:
            timeout: 폴링 타임아웃 (초)

        Returns:
            소비된 이벤트 또는 None (메시지 없음)
        """
        if not self._consumer:
            return None

        msg = self._consumer.poll(timeout)
        if msg is None:
            return None

        if msg.error():
            from confluent_kafka import KafkaError

            if msg.error().code() == KafkaError._PARTITION_EOF:
                # 파티션 끝에 도달 - 정상 상황
                return None
            else:
                logger.error(
                    "kafka_consumer.폴링_오류",
                    kafka_error=msg.error(),
                )
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

        여러 메시지를 한 번에 폴링합니다.

        Args:
            batch_size: 최대 배치 크기
            timeout: 총 타임아웃 (초)

        Returns:
            소비된 이벤트 목록
        """
        events = []
        start_time = time.time()

        while len(events) < batch_size:
            remaining_timeout = max(0.01, timeout - (time.time() - start_time))
            if remaining_timeout <= 0:
                break

            event = self.poll_once(timeout=remaining_timeout / batch_size)
            if event:
                events.append(event)
            else:
                break

        return events

    def run(self) -> None:
        """
        Consumer 루프 시작 (블로킹).

        stop() 호출 전까지 무한 루프로 메시지를 소비합니다.
        """
        self._running = True
        logger.info("kafka_consumer.consumer_루프_시작")

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
                logger.exception(
                    "kafka_consumer.루프_오류",
                    error=e,
                )
                with self._lock:
                    self._stats["last_error"] = str(e)
                time.sleep(1.0)  # 에러 시 잠시 대기

        logger.info("kafka_consumer.consumer_루프_종료")

    def start_background(self) -> None:
        """백그라운드 스레드에서 Consumer 시작."""
        if self._worker_thread and self._worker_thread.is_alive():
            logger.warning("kafka_consumer.이미_실행")
            return

        self._worker_thread = threading.Thread(
            target=self.run,
            name="KafkaAuditConsumer",
            daemon=True,
        )
        self._worker_thread.start()
        logger.info("kafka_consumer.백그라운드_스레드_시작")

    def stop(self, timeout: float = 10.0) -> None:
        """
        Consumer 정지.

        Args:
            timeout: 스레드 종료 대기 시간 (초)
        """
        self._running = False

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=timeout)
            if self._worker_thread.is_alive():
                logger.warning("kafka_consumer.스레드가_시간_종료되지_않음")

        logger.info("kafka_consumer.정지됨")

    def close(self) -> None:
        """Consumer 종료."""
        self.stop()

        if self._consumer:
            self._consumer.close()
            self._consumer = None

        logger.info("kafka_consumer.종료됨")

    def get_stats(self) -> dict[str, Any]:
        """소비 통계 반환."""
        with self._lock:
            return dict(self._stats)

    def __enter__(self) -> KafkaAuditConsumer:
        """Context manager 진입."""
        return self

    def __exit__(self, *args: Any) -> None:
        """Context manager 종료."""
        self.close()
