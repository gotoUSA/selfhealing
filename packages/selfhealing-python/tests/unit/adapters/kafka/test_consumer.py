"""
KafkaAuditConsumer 단위 테스트.

Kafka Consumer의 메시지 소비, 오프셋 커밋, 핸들러 호출을 테스트합니다.
confluent-kafka를 Mock하여 실제 Kafka 없이 테스트합니다.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.adapters.kafka.config import KafkaSettings
from selfhealing.adapters.kafka.consumer import (
    ConsumedEvent,
    KafkaAuditConsumer,
)


class TestConsumedEvent:
    """ConsumedEvent 단위 테스트."""

    def test_consumed_event_creation(self) -> None:
        """ConsumedEvent 생성."""
        event = ConsumedEvent(
            topic="selfhealing.audit.events",
            partition=0,
            offset=100,
            key="test-key",
            value={"action": "dlq_store"},
            headers={"x-header": b"value"},
            timestamp=1704067200.0,
        )

        assert event.topic == "selfhealing.audit.events"
        assert event.partition == 0
        assert event.offset == 100
        assert event.key == "test-key"
        assert event.value["action"] == "dlq_store"
        assert event.headers["x-header"] == b"value"

    def test_consumed_event_without_key(self) -> None:
        """키 없는 ConsumedEvent."""
        event = ConsumedEvent(
            topic="test.topic",
            partition=0,
            offset=0,
            key=None,
            value={},
            headers={},
            timestamp=1704067200.0,
        )

        assert event.key is None


@pytest.fixture
def mock_confluent_kafka_consumer():
    """confluent_kafka Consumer Mock."""
    mock_consumer = MagicMock()
    mock_consumer.subscribe = MagicMock()
    mock_consumer.poll = MagicMock()
    mock_consumer.commit = MagicMock()
    mock_consumer.close = MagicMock()

    # confluent_kafka 모듈 Mock
    mock_module = MagicMock()
    mock_module.Consumer = MagicMock(return_value=mock_consumer)
    mock_module.KafkaError = MagicMock()

    with patch.dict("sys.modules", {"confluent_kafka": mock_module}):
        yield {
            "Consumer": mock_module.Consumer,
            "consumer_instance": mock_consumer,
        }


@pytest.fixture
def kafka_settings() -> KafkaSettings:
    """테스트용 Kafka 설정."""
    return KafkaSettings(
        bootstrap_servers="localhost:9092",
        topic_prefix="test.",
        consumer_group_id="test-consumer-group",
        consumer_enable_auto_commit=False,
    )


@pytest.fixture
def mock_kafka_message():
    """Mock Kafka 메시지 생성."""

    def create_message(
        topic: str = "test.audit.events",
        partition: int = 0,
        offset: int = 100,
        key: str | None = "test-key",
        value: dict | None = None,
        headers: list | None = None,
    ):
        msg = MagicMock()
        msg.topic.return_value = topic
        msg.partition.return_value = partition
        msg.offset.return_value = offset
        msg.key.return_value = key.encode() if key else None
        msg.value.return_value = json.dumps(value or {"action": "test"}).encode()
        msg.headers.return_value = headers or []
        msg.timestamp.return_value = (1, 1704067200000)  # (type, timestamp_ms)
        msg.error.return_value = None
        return msg

    return create_message


class TestKafkaAuditConsumer:
    """KafkaAuditConsumer 단위 테스트."""

    def test_consumer_initialization(self, mock_confluent_kafka_consumer, kafka_settings) -> None:
        """Consumer 초기화 확인."""
        consumer = KafkaAuditConsumer(
            topics=["audit.events"],
            settings=kafka_settings,
        )

        # Consumer 클래스가 올바른 설정으로 호출됨
        mock_confluent_kafka_consumer["Consumer"].assert_called_once()
        call_args = mock_confluent_kafka_consumer["Consumer"].call_args[0][0]

        assert call_args["bootstrap.servers"] == "localhost:9092"
        assert call_args["group.id"] == "test-consumer-group"
        assert call_args["enable.auto.commit"] is False

        # subscribe가 호출됨
        mock_confluent_kafka_consumer["consumer_instance"].subscribe.assert_called_once_with(["test.audit.events"])

        consumer.close()

    def test_poll_once_returns_event(self, mock_confluent_kafka_consumer, kafka_settings, mock_kafka_message) -> None:
        """단일 메시지 폴링."""
        consumer = KafkaAuditConsumer(
            topics=["audit.events"],
            settings=kafka_settings,
        )
        mock_consumer = mock_confluent_kafka_consumer["consumer_instance"]
        mock_consumer.poll.return_value = mock_kafka_message()

        event = consumer.poll_once(timeout=1.0)

        assert event is not None
        assert event.topic == "test.audit.events"
        assert event.partition == 0
        assert event.offset == 100
        assert event.key == "test-key"
        assert event.value["action"] == "test"

        consumer.close()

    def test_poll_once_returns_none_when_no_message(self, mock_confluent_kafka_consumer, kafka_settings) -> None:
        """메시지 없을 때 None 반환."""
        consumer = KafkaAuditConsumer(
            topics=["audit.events"],
            settings=kafka_settings,
        )
        mock_consumer = mock_confluent_kafka_consumer["consumer_instance"]
        mock_consumer.poll.return_value = None

        event = consumer.poll_once(timeout=1.0)

        assert event is None

        consumer.close()

    def test_consume_batch(self, mock_confluent_kafka_consumer, kafka_settings, mock_kafka_message) -> None:
        """배치 소비."""
        consumer = KafkaAuditConsumer(
            topics=["audit.events"],
            settings=kafka_settings,
        )
        mock_consumer = mock_confluent_kafka_consumer["consumer_instance"]

        # 3개 메시지 후 None
        mock_consumer.poll.side_effect = [
            mock_kafka_message(offset=1),
            mock_kafka_message(offset=2),
            mock_kafka_message(offset=3),
            None,
        ]

        events = consumer.consume_batch(batch_size=10, timeout=1.0)

        assert len(events) == 3
        assert events[0].offset == 1
        assert events[1].offset == 2
        assert events[2].offset == 3

        consumer.close()

    def test_event_handler_called(self, mock_confluent_kafka_consumer, kafka_settings, mock_kafka_message) -> None:
        """이벤트 핸들러 호출 확인."""
        handled_events = []

        def handler(event: ConsumedEvent) -> bool:
            handled_events.append(event)
            return True

        consumer = KafkaAuditConsumer(
            topics=["audit.events"],
            settings=kafka_settings,
            event_handler=handler,
        )
        mock_consumer = mock_confluent_kafka_consumer["consumer_instance"]
        mock_consumer.poll.return_value = mock_kafka_message()

        event = consumer.poll_once(timeout=1.0)
        assert event is not None

        # 메시지 처리 (내부 메서드 호출)
        success = consumer._process_message(event)

        assert success is True
        assert len(handled_events) == 1
        assert handled_events[0].topic == "test.audit.events"

        consumer.close()

    def test_event_handler_failure(self, mock_confluent_kafka_consumer, kafka_settings, mock_kafka_message) -> None:
        """이벤트 핸들러 실패 시 False 반환."""

        def handler(event: ConsumedEvent) -> bool:
            raise ValueError("Handler error")

        consumer = KafkaAuditConsumer(
            topics=["audit.events"],
            settings=kafka_settings,
            event_handler=handler,
        )
        mock_consumer = mock_confluent_kafka_consumer["consumer_instance"]
        mock_consumer.poll.return_value = mock_kafka_message()

        event = consumer.poll_once(timeout=1.0)
        success = consumer._process_message(event)

        assert success is False

        consumer.close()

    def test_commit_offset(self, mock_confluent_kafka_consumer, kafka_settings, mock_kafka_message) -> None:
        """오프셋 커밋 확인."""
        consumer = KafkaAuditConsumer(
            topics=["audit.events"],
            settings=kafka_settings,
        )
        mock_consumer = mock_confluent_kafka_consumer["consumer_instance"]
        mock_consumer.poll.return_value = mock_kafka_message()

        event = consumer.poll_once(timeout=1.0)
        consumer._commit_offset(event)

        mock_consumer.commit.assert_called_once_with(asynchronous=False)

        consumer.close()

    def test_get_stats(self, mock_confluent_kafka_consumer, kafka_settings, mock_kafka_message) -> None:
        """통계 조회."""
        consumer = KafkaAuditConsumer(
            topics=["audit.events"],
            settings=kafka_settings,
        )
        mock_consumer = mock_confluent_kafka_consumer["consumer_instance"]
        mock_consumer.poll.return_value = mock_kafka_message()

        # 메시지 폴링
        consumer.poll_once(timeout=1.0)

        stats = consumer.get_stats()

        assert "messages_consumed" in stats
        assert "messages_processed" in stats
        assert "messages_failed" in stats
        assert "commits" in stats
        assert stats["messages_consumed"] == 1

        consumer.close()

    def test_context_manager(self, mock_confluent_kafka_consumer, kafka_settings) -> None:
        """Context manager 동작 확인."""
        with KafkaAuditConsumer(
            topics=["audit.events"],
            settings=kafka_settings,
        ) as consumer:
            stats = consumer.get_stats()
            assert stats["messages_consumed"] == 0

        # close가 호출됨
        mock_confluent_kafka_consumer["consumer_instance"].close.assert_called()

    def test_stop_sets_running_false(self, mock_confluent_kafka_consumer, kafka_settings) -> None:
        """stop() 호출 시 _running이 False로 설정됨."""
        consumer = KafkaAuditConsumer(
            topics=["audit.events"],
            settings=kafka_settings,
        )

        consumer._running = True
        consumer.stop()

        assert consumer._running is False

        consumer.close()

    def test_multiple_topics_subscription(self, mock_confluent_kafka_consumer, kafka_settings) -> None:
        """여러 토픽 구독."""
        consumer = KafkaAuditConsumer(
            topics=["audit.events", "dlq.events", "recovery.events"],
            settings=kafka_settings,
        )

        mock_confluent_kafka_consumer["consumer_instance"].subscribe.assert_called_once_with(
            [
                "test.audit.events",
                "test.dlq.events",
                "test.recovery.events",
            ]
        )

        consumer.close()

    def test_parse_message_with_headers(self, mock_confluent_kafka_consumer, kafka_settings) -> None:
        """헤더 포함 메시지 파싱."""
        consumer = KafkaAuditConsumer(
            topics=["audit.events"],
            settings=kafka_settings,
        )

        msg = MagicMock()
        msg.topic.return_value = "test.audit.events"
        msg.partition.return_value = 0
        msg.offset.return_value = 100
        msg.key.return_value = b"key"
        msg.value.return_value = b'{"data": "test"}'
        msg.headers.return_value = [
            ("x-custom-header", b"custom-value"),
            ("selfhealing.cascade_id", b"cascade-123"),
        ]
        msg.timestamp.return_value = (1, 1704067200000)
        msg.error.return_value = None

        mock_consumer = mock_confluent_kafka_consumer["consumer_instance"]
        mock_consumer.poll.return_value = msg

        event = consumer.poll_once(timeout=1.0)

        assert event is not None
        assert event.headers["x-custom-header"] == b"custom-value"
        assert event.headers["selfhealing.cascade_id"] == b"cascade-123"

        consumer.close()
