"""
KafkaEventBus 단위 테스트.

Event Bus의 발행/구독, 다중 핸들러, 비동기 발행을 테스트합니다.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from selfhealing.adapters.kafka.config import KafkaSettings
from selfhealing.adapters.kafka.consumer import ConsumedEvent
from selfhealing.adapters.kafka.event_bus import KafkaEventBus


@pytest.fixture
def kafka_settings() -> KafkaSettings:
    """테스트용 Kafka 설정."""
    return KafkaSettings(
        bootstrap_servers="localhost:9092",
        topic_prefix="test.",
    )


@pytest.fixture
def mock_producer():
    """KafkaAuditProducer Mock."""
    producer = MagicMock()
    producer.publish = MagicMock(return_value=True)
    producer.flush = MagicMock(return_value=0)
    producer.poll = MagicMock(return_value=0)
    producer.close = MagicMock()
    return producer


@pytest.fixture
def mock_consumer():
    """KafkaAuditConsumer Mock."""
    consumer = MagicMock()
    consumer.start_background = MagicMock()
    consumer.stop = MagicMock()
    consumer.close = MagicMock()
    return consumer


class TestKafkaEventBus:
    """KafkaEventBus 단위 테스트."""

    def test_publish_creates_producer(self, kafka_settings, mock_producer) -> None:
        """publish 호출 시 Producer 생성."""
        with patch(
            "selfhealing.adapters.kafka.event_bus.KafkaAuditProducer",
            return_value=mock_producer,
        ):
            bus = KafkaEventBus(settings=kafka_settings)

            success = bus.publish(
                topic="audit.events",
                event={"action": "test"},
            )

            assert success is True
            mock_producer.publish.assert_called_once()

            bus.close()

    def test_publish_with_key(self, kafka_settings, mock_producer) -> None:
        """키와 함께 발행."""
        with patch(
            "selfhealing.adapters.kafka.event_bus.KafkaAuditProducer",
            return_value=mock_producer,
        ):
            bus = KafkaEventBus(settings=kafka_settings)

            bus.publish(
                topic="audit.events",
                event={"action": "test"},
                key="order-123",
            )

            call_kwargs = mock_producer.publish.call_args[1]
            assert call_kwargs["key"] == "order-123"

            bus.close()

    def test_subscribe_registers_handler(self, kafka_settings) -> None:
        """subscribe가 핸들러 등록."""
        bus = KafkaEventBus(settings=kafka_settings)

        def handler(event: ConsumedEvent) -> bool:
            return True

        bus.subscribe("audit.events", handler)

        assert "audit.events" in bus._handlers
        assert len(bus._handlers["audit.events"]) == 1

        bus.close()

    def test_subscribe_multiple_handlers(self, kafka_settings) -> None:
        """같은 토픽에 여러 핸들러 등록."""
        bus = KafkaEventBus(settings=kafka_settings)

        def handler1(event: ConsumedEvent) -> bool:
            return True

        def handler2(event: ConsumedEvent) -> bool:
            return True

        bus.subscribe("audit.events", handler1)
        bus.subscribe("audit.events", handler2)

        assert len(bus._handlers["audit.events"]) == 2

        bus.close()

    def test_start_creates_consumers(self, kafka_settings, mock_consumer) -> None:
        """start가 구독된 토픽에 대해 Consumer 생성."""
        with patch(
            "selfhealing.adapters.kafka.event_bus.KafkaAuditConsumer",
            return_value=mock_consumer,
        ):
            bus = KafkaEventBus(settings=kafka_settings)

            def handler(event: ConsumedEvent) -> bool:
                return True

            bus.subscribe("audit.events", handler)
            bus.subscribe("dlq.events", handler)

            bus.start()

            assert bus._running is True
            assert len(bus._consumers) == 2
            assert mock_consumer.start_background.call_count == 2

            bus.stop()

    def test_stop_stops_consumers(self, kafka_settings, mock_consumer) -> None:
        """stop이 모든 Consumer 정지."""
        with patch(
            "selfhealing.adapters.kafka.event_bus.KafkaAuditConsumer",
            return_value=mock_consumer,
        ):
            bus = KafkaEventBus(settings=kafka_settings)

            def handler(event: ConsumedEvent) -> bool:
                return True

            bus.subscribe("audit.events", handler)
            bus.start()
            bus.stop()

            assert bus._running is False
            assert len(bus._consumers) == 0
            mock_consumer.stop.assert_called()

    def test_close_closes_producer(self, kafka_settings, mock_producer) -> None:
        """close가 Producer 종료."""
        with patch(
            "selfhealing.adapters.kafka.event_bus.KafkaAuditProducer",
            return_value=mock_producer,
        ):
            bus = KafkaEventBus(settings=kafka_settings)
            bus.publish(topic="test", event={})
            bus.close()

            mock_producer.close.assert_called_once()

    def test_context_manager(self, kafka_settings, mock_producer) -> None:
        """Context manager 동작."""
        with patch(
            "selfhealing.adapters.kafka.event_bus.KafkaAuditProducer",
            return_value=mock_producer,
        ):
            with KafkaEventBus(settings=kafka_settings) as bus:
                bus.publish(topic="test", event={})

            mock_producer.close.assert_called()

    def test_flush(self, kafka_settings, mock_producer) -> None:
        """flush 호출 확인."""
        with patch(
            "selfhealing.adapters.kafka.event_bus.KafkaAuditProducer",
            return_value=mock_producer,
        ):
            bus = KafkaEventBus(settings=kafka_settings)
            bus.publish(topic="test", event={})
            bus.flush(timeout=5.0)

            mock_producer.flush.assert_called_with(timeout=5.0)

            bus.close()

    def test_start_already_running(self, kafka_settings, mock_consumer) -> None:
        """이미 실행 중일 때 start 호출."""
        with patch(
            "selfhealing.adapters.kafka.event_bus.KafkaAuditConsumer",
            return_value=mock_consumer,
        ):
            bus = KafkaEventBus(settings=kafka_settings)

            def handler(event: ConsumedEvent) -> bool:
                return True

            bus.subscribe("audit.events", handler)
            bus.start()
            bus.start()  # 두 번째 호출

            # Consumer는 한 번만 생성됨
            assert len(bus._consumers) == 1

            bus.stop()

    def test_combined_handler_all_succeed(self, kafka_settings) -> None:
        """모든 핸들러 성공 시 combined handler가 True 반환."""
        bus = KafkaEventBus(settings=kafka_settings)

        results = []

        def handler1(event: ConsumedEvent) -> bool:
            results.append("handler1")
            return True

        def handler2(event: ConsumedEvent) -> bool:
            results.append("handler2")
            return True

        bus.subscribe("audit.events", handler1)
        bus.subscribe("audit.events", handler2)

        # combined handler 생성
        with patch("selfhealing.adapters.kafka.event_bus.KafkaAuditConsumer") as MockConsumer:
            bus.start()

            # Consumer 생성 시 전달된 event_handler 확인
            call_kwargs = MockConsumer.call_args[1]
            combined_handler = call_kwargs["event_handler"]

            # Mock 이벤트로 테스트
            mock_event = ConsumedEvent(
                topic="test.audit.events",
                partition=0,
                offset=0,
                key=None,
                value={},
                headers={},
                timestamp=0.0,
            )

            result = combined_handler(mock_event)

            assert result is True
            assert len(results) == 2

            bus.stop()

    def test_combined_handler_one_fails(self, kafka_settings) -> None:
        """하나의 핸들러 실패 시 combined handler가 False 반환."""
        bus = KafkaEventBus(settings=kafka_settings)

        def handler1(event: ConsumedEvent) -> bool:
            return True

        def handler2(event: ConsumedEvent) -> bool:
            return False  # 실패

        bus.subscribe("audit.events", handler1)
        bus.subscribe("audit.events", handler2)

        with patch("selfhealing.adapters.kafka.event_bus.KafkaAuditConsumer") as MockConsumer:
            bus.start()

            call_kwargs = MockConsumer.call_args[1]
            combined_handler = call_kwargs["event_handler"]

            mock_event = ConsumedEvent(
                topic="test.audit.events",
                partition=0,
                offset=0,
                key=None,
                value={},
                headers={},
                timestamp=0.0,
            )

            result = combined_handler(mock_event)

            assert result is False

            bus.stop()

    def test_no_subscriptions_no_consumers(self, kafka_settings) -> None:
        """구독 없으면 Consumer 생성 안 함."""
        bus = KafkaEventBus(settings=kafka_settings)

        bus.start()

        assert len(bus._consumers) == 0

        bus.stop()
