"""
KafkaAuditProducer 단위 테스트.

Kafka Producer의 메시지 발행, 콜백, 통계 기능을 테스트합니다.
confluent-kafka를 Mock하여 실제 Kafka 없이 테스트합니다.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.adapters.kafka.config import KafkaSettings
from selfhealing.adapters.kafka.producer import (
    DeliveryReport,
    KafkaAuditProducer,
    get_kafka_producer,
    reset_kafka_producer,
)


class TestDeliveryReport:
    """DeliveryReport 단위 테스트."""

    def test_success_report(self) -> None:
        """성공 리포트 생성."""
        report = DeliveryReport(
            topic="selfhealing.audit.events",
            partition=0,
            offset=100,
            timestamp=1704067200.0,
            key="test-key",
            error=None,
        )

        assert report.success is True
        assert report.topic == "selfhealing.audit.events"
        assert report.partition == 0
        assert report.offset == 100
        assert report.key == "test-key"

    def test_failure_report(self) -> None:
        """실패 리포트 생성."""
        report = DeliveryReport(
            topic="selfhealing.audit.events",
            partition=0,
            offset=-1,
            timestamp=1704067200.0,
            key=None,
            error="Broker not available",
        )

        assert report.success is False
        assert report.error == "Broker not available"


@pytest.fixture
def mock_confluent_kafka():
    """confluent_kafka 모듈 Mock."""
    mock_producer = MagicMock()
    mock_producer.produce = MagicMock()
    mock_producer.flush = MagicMock(return_value=0)
    mock_producer.poll = MagicMock(return_value=1)

    # confluent_kafka.Producer를 패치 (함수 내부 import 대응)
    mock_module = MagicMock()
    mock_module.Producer = MagicMock(return_value=mock_producer)

    with patch.dict("sys.modules", {"confluent_kafka": mock_module}):
        yield {
            "Producer": mock_module.Producer,
            "producer_instance": mock_producer,
        }


@pytest.fixture
def kafka_settings() -> KafkaSettings:
    """테스트용 Kafka 설정."""
    return KafkaSettings(
        bootstrap_servers="localhost:9092",
        topic_prefix="test.",
        producer_idempotent=True,
    )


class TestKafkaAuditProducer:
    """KafkaAuditProducer 단위 테스트."""

    def setup_method(self) -> None:
        """테스트 전 Producer 싱글톤 초기화."""
        reset_kafka_producer()

    def teardown_method(self) -> None:
        """테스트 후 정리."""
        reset_kafka_producer()

    def test_producer_initialization(self, mock_confluent_kafka, kafka_settings) -> None:
        """Producer 초기화 확인."""
        producer = KafkaAuditProducer(settings=kafka_settings)

        # Producer 클래스가 올바른 설정으로 호출됨
        mock_confluent_kafka["Producer"].assert_called_once()
        call_args = mock_confluent_kafka["Producer"].call_args[0][0]

        assert call_args["bootstrap.servers"] == "localhost:9092"
        assert call_args["acks"] == "all"
        assert call_args["enable.idempotence"] is True

        producer.close()

    def test_publish_event(self, mock_confluent_kafka, kafka_settings) -> None:
        """이벤트 발행 테스트."""
        producer = KafkaAuditProducer(settings=kafka_settings)
        mock_producer = mock_confluent_kafka["producer_instance"]

        event = {"action": "dlq_store", "data": {"key": "value"}}
        success = producer.publish(
            topic="audit.events",
            event=event,
            key="test-domain",
        )

        assert success is True
        mock_producer.produce.assert_called_once()

        # 호출 인자 확인
        call_kwargs = mock_producer.produce.call_args[1]
        assert call_kwargs["topic"] == "test.audit.events"
        assert call_kwargs["key"] == b"test-domain"

        # value가 JSON 직렬화됨
        value = json.loads(call_kwargs["value"].decode())
        assert value["action"] == "dlq_store"

        producer.close()

    def test_publish_audit_event_with_metadata(self, mock_confluent_kafka, kafka_settings) -> None:
        """Audit 이벤트 발행 시 메타데이터 자동 추가."""
        producer = KafkaAuditProducer(settings=kafka_settings)
        mock_producer = mock_confluent_kafka["producer_instance"]

        event = {"action": "test_action"}
        producer.publish_audit_event(event=event, domain="order")

        call_kwargs = mock_producer.produce.call_args[1]
        value = json.loads(call_kwargs["value"].decode())

        # 자동 추가된 메타데이터 확인
        assert "timestamp" in value
        assert "schema_version" in value
        assert value["schema_version"] == 1

        producer.close()

    def test_publish_with_headers(self, mock_confluent_kafka, kafka_settings) -> None:
        """헤더와 함께 발행."""
        producer = KafkaAuditProducer(settings=kafka_settings)
        mock_producer = mock_confluent_kafka["producer_instance"]

        custom_headers = {
            b"x-custom-header": b"custom-value",
        }
        producer.publish(
            topic="audit.events",
            event={"test": "data"},
            headers=custom_headers,
        )

        call_kwargs = mock_producer.produce.call_args[1]
        headers = call_kwargs.get("headers", [])

        # 헤더가 리스트 형식으로 전달됨
        assert headers is not None

        producer.close()

    def test_flush(self, mock_confluent_kafka, kafka_settings) -> None:
        """플러시 테스트."""
        producer = KafkaAuditProducer(settings=kafka_settings)
        mock_producer = mock_confluent_kafka["producer_instance"]

        remaining = producer.flush(timeout=5.0)

        mock_producer.flush.assert_called_once_with(5.0)
        assert remaining == 0

        producer.close()

    def test_poll(self, mock_confluent_kafka, kafka_settings) -> None:
        """폴링 테스트."""
        producer = KafkaAuditProducer(settings=kafka_settings)
        mock_producer = mock_confluent_kafka["producer_instance"]

        events = producer.poll(timeout=0.1)

        mock_producer.poll.assert_called_once_with(0.1)
        assert events == 1

        producer.close()

    def test_get_stats(self, mock_confluent_kafka, kafka_settings) -> None:
        """통계 조회 테스트."""
        producer = KafkaAuditProducer(settings=kafka_settings)

        # 메시지 발행
        producer.publish(topic="test", event={"data": "test"})

        stats = producer.get_stats()

        assert "messages_sent" in stats
        assert "messages_delivered" in stats
        assert "messages_failed" in stats
        assert stats["messages_sent"] == 1

        producer.close()

    def test_context_manager(self, mock_confluent_kafka, kafka_settings) -> None:
        """Context manager 테스트."""
        with KafkaAuditProducer(settings=kafka_settings) as producer:
            producer.publish(topic="test", event={"data": "test"})

        # close가 호출됨
        mock_confluent_kafka["producer_instance"].flush.assert_called()

    def test_delivery_callback(self, mock_confluent_kafka, kafka_settings) -> None:
        """전송 결과 콜백 테스트."""
        callback_results = []

        def on_delivery(report: DeliveryReport) -> None:
            callback_results.append(report)

        producer = KafkaAuditProducer(
            settings=kafka_settings,
            on_delivery=on_delivery,
        )
        mock_producer = mock_confluent_kafka["producer_instance"]

        producer.publish(topic="test", event={"data": "test"})

        # produce의 callback 인자 확인
        call_kwargs = mock_producer.produce.call_args[1]
        callback = call_kwargs["callback"]

        # Mock 메시지 객체 생성
        mock_msg = MagicMock()
        mock_msg.topic.return_value = "test.test"
        mock_msg.partition.return_value = 0
        mock_msg.offset.return_value = 100
        mock_msg.key.return_value = None

        # 콜백 호출 시뮬레이션
        callback(None, mock_msg)  # 성공

        assert len(callback_results) == 1
        assert callback_results[0].success is True

        producer.close()

    def test_publish_without_key(self, mock_confluent_kafka, kafka_settings) -> None:
        """키 없이 발행."""
        producer = KafkaAuditProducer(settings=kafka_settings)
        mock_producer = mock_confluent_kafka["producer_instance"]

        producer.publish(topic="test", event={"data": "test"})

        call_kwargs = mock_producer.produce.call_args[1]
        assert call_kwargs["key"] is None

        producer.close()

    def test_publish_to_specific_partition(self, mock_confluent_kafka, kafka_settings) -> None:
        """특정 파티션에 발행."""
        producer = KafkaAuditProducer(settings=kafka_settings)
        mock_producer = mock_confluent_kafka["producer_instance"]

        producer.publish(
            topic="test",
            event={"data": "test"},
            partition=5,
        )

        call_kwargs = mock_producer.produce.call_args[1]
        assert call_kwargs["partition"] == 5

        producer.close()


class TestKafkaProducerSingleton:
    """Producer 싱글톤 테스트."""

    def setup_method(self) -> None:
        reset_kafka_producer()

    def teardown_method(self) -> None:
        reset_kafka_producer()

    def test_singleton_pattern(self, mock_confluent_kafka) -> None:
        """싱글톤 패턴 동작 확인."""
        producer1 = get_kafka_producer()
        producer2 = get_kafka_producer()

        assert producer1 is producer2

    def test_reset_singleton(self, mock_confluent_kafka) -> None:
        """싱글톤 초기화 확인."""
        producer1 = get_kafka_producer()
        reset_kafka_producer()
        producer2 = get_kafka_producer()

        assert producer1 is not producer2
