"""
Kafka Event Bus - 통합 인터페이스.

Producer와 Consumer를 통합하여 Event-Driven 아키텍처를 지원합니다.

핵심 특징:
- Publisher/Subscriber 패턴
- 동기/비동기 발행 지원
- 다중 토픽 구독
- 자동 핸들러 관리

Usage:
    from selfhealing.adapters.kafka import KafkaEventBus

    # Event Bus 초기화
    bus = KafkaEventBus()

    # 이벤트 발행
    bus.publish("audit.events", {"action": "dlq_store"})

    # 이벤트 구독
    def handler(event):
        print(f"Received: {event.value}")
        return True

    bus.subscribe("audit.events", handler)
    bus.start()
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any

import structlog

from selfhealing.adapters.kafka.config import KafkaSettings, get_kafka_settings
from selfhealing.adapters.kafka.consumer import (
    ConsumedEvent,
    EventHandler,
    KafkaAuditConsumer,
)
from selfhealing.adapters.kafka.producer import DeliveryReport, KafkaAuditProducer

logger = structlog.get_logger()


class KafkaEventBus:
    """
    Kafka Event Bus.

    Publisher/Subscriber 패턴을 Kafka 기반으로 구현합니다.
    여러 토픽에 대한 발행/구독을 통합 관리합니다.
    """

    def __init__(self, settings: KafkaSettings | None = None):
        """
        KafkaEventBus 초기화.

        Args:
            settings: Kafka 설정 (None이면 기본값)
        """
        self._settings = settings or get_kafka_settings()
        self._producer: KafkaAuditProducer | None = None
        self._consumers: dict[str, KafkaAuditConsumer] = {}
        self._handlers: dict[str, list[EventHandler]] = {}
        self._lock = threading.Lock()
        self._running = False

    def _ensure_producer(self) -> KafkaAuditProducer:
        """Producer 인스턴스 생성/반환."""
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
        이벤트 발행 (동기).

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
        이벤트 발행 (비동기).

        전송 완료까지 대기하고 결과를 반환합니다.

        Args:
            topic: 토픽 이름
            event: 이벤트 데이터
            key: 파티션 키

        Returns:
            전송 결과

        Raises:
            RuntimeError: 발행 실패 시
        """
        loop = asyncio.get_event_loop()
        future: asyncio.Future[DeliveryReport] = loop.create_future()

        def on_delivery(report: DeliveryReport) -> None:
            if not future.done():
                loop.call_soon_threadsafe(future.set_result, report)

        producer = self._ensure_producer()
        success = producer.publish(
            topic=topic,
            event=event,
            key=key,
            on_delivery=on_delivery,
        )

        if not success:
            raise RuntimeError("이벤트 발행 실패")

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

        같은 토픽에 여러 핸들러를 등록할 수 있습니다.

        Args:
            topic: 토픽 이름 (프리픽스 제외)
            handler: 이벤트 처리 핸들러 (True 반환 시 성공)
        """
        with self._lock:
            if topic not in self._handlers:
                self._handlers[topic] = []
            self._handlers[topic].append(handler)

            logger.info(
                "kafka_event_bus.handler_registered",
                topic=topic,
            )

    def _create_consumer_for_topic(self, topic: str) -> KafkaAuditConsumer:
        """토픽용 Consumer 생성."""
        handlers = self._handlers.get(topic, [])

        def combined_handler(event: ConsumedEvent) -> bool:
            """등록된 모든 핸들러 호출."""
            results = []
            for handler in handlers:
                try:
                    results.append(handler(event))
                except Exception as e:
                    logger.exception(
                        "kafka_event_bus.handler_error",
                        error=e,
                    )
                    results.append(False)
            # 모든 핸들러가 성공해야 성공
            return all(results)

        return KafkaAuditConsumer(
            topics=[topic],
            settings=self._settings,
            event_handler=combined_handler,
        )

    def start(self) -> None:
        """Event Bus 시작."""
        if self._running:
            logger.warning("kafka_event_bus.already_running")
            return

        self._running = True

        # 구독된 토픽별 Consumer 시작
        with self._lock:
            for topic in self._handlers:
                if topic not in self._consumers:
                    consumer = self._create_consumer_for_topic(topic)
                    consumer.start_background()
                    self._consumers[topic] = consumer

        logger.info("kafka_event_bus.started")

    def stop(self) -> None:
        """Event Bus 정지."""
        self._running = False

        # 모든 Consumer 정지
        with self._lock:
            for consumer in self._consumers.values():
                consumer.stop()
            self._consumers.clear()

        logger.info("kafka_event_bus.stopped")

    def close(self) -> None:
        """Event Bus 종료."""
        self.stop()

        if self._producer:
            self._producer.close()
            self._producer = None

        logger.info("kafka_event_bus.closed")

    def flush(self, timeout: float = 10.0) -> None:
        """
        Producer 버퍼 플러시.

        Args:
            timeout: 플러시 타임아웃 (초)
        """
        if self._producer:
            self._producer.flush(timeout=timeout)

    def __enter__(self) -> KafkaEventBus:
        """Context manager 진입."""
        return self

    def __exit__(self, *args: Any) -> None:
        """Context manager 종료."""
        self.close()
