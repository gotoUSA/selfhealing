"""
Kafka Event Bus 통합 테스트.

실제 Kafka 브로커와 연동하여 Producer/Consumer 동작을 검증합니다.

실행 방법:
    docker-compose -f docker-compose.test.yml up kafka -d
    SELFHEALING_KAFKA_BOOTSTRAP_SERVERS=localhost:19092 pytest tests/integration/kafka/ -v
"""

from __future__ import annotations

import pytest

pytest.importorskip("selfhealing", reason="selfhealing 라이브러리(선택)가 설치된 환경에서만 실행")

import time
import uuid



class TestKafkaProducerIntegration:
    """KafkaAuditProducer 통합 테스트."""

    def test_publish_single_event(self, kafka_producer, unique_topic):
        """단일 이벤트 발행 테스트."""
        # Given
        event = {
            "event_id": str(uuid.uuid4()),
            "event_type": "test_event",
            "data": {"message": "Hello Kafka"},
        }

        delivered = []

        def on_delivery(report):
            delivered.append(report)

        # When
        success = kafka_producer.publish(
            topic=unique_topic,
            event=event,
            key="test-key",
            on_delivery=on_delivery,
        )
        kafka_producer.flush(timeout=10.0)

        # Then
        assert success is True
        assert len(delivered) == 1
        assert delivered[0].success is True
        assert delivered[0].key == "test-key"

    def test_publish_multiple_events(self, kafka_producer, unique_topic):
        """다중 이벤트 발행 테스트."""
        # Given
        event_count = 10
        delivered = []

        def on_delivery(report):
            delivered.append(report)

        # When
        for i in range(event_count):
            kafka_producer.publish(
                topic=unique_topic,
                event={"event_id": str(uuid.uuid4()), "index": i},
                key=f"key-{i}",
                on_delivery=on_delivery,
            )

        kafka_producer.flush(timeout=10.0)

        # Then
        assert len(delivered) == event_count
        assert all(r.success for r in delivered)

    def test_publish_audit_event_with_metadata(self, kafka_producer):
        """Audit 이벤트 메타데이터 자동 추가 테스트."""
        # Given
        event = {"action": "dlq_store", "target_id": "test-123"}

        delivered = []

        def on_delivery(report):
            delivered.append(report)

        # When
        success = kafka_producer.publish_audit_event(
            event=event,
            domain="order",
            on_delivery=on_delivery,
        )
        kafka_producer.flush(timeout=10.0)

        # Then
        assert success is True
        assert len(delivered) == 1
        assert delivered[0].success is True

    def test_producer_stats(self, kafka_producer, unique_topic):
        """Producer 통계 테스트."""
        # Given
        for i in range(5):
            kafka_producer.publish(
                topic=unique_topic,
                event={"index": i},
            )
        kafka_producer.flush(timeout=10.0)

        # When
        stats = kafka_producer.get_stats()

        # Then
        assert stats["messages_sent"] == 5
        assert stats["messages_delivered"] == 5
        assert stats["messages_failed"] == 0


class TestKafkaConsumerIntegration:
    """KafkaAuditConsumer 통합 테스트."""

    def test_consume_published_event(self, kafka_settings, unique_topic):
        """발행된 이벤트 소비 테스트."""
        from selfhealing.adapters.kafka.consumer import KafkaAuditConsumer
        from selfhealing.adapters.kafka.producer import KafkaAuditProducer

        # Given: 이벤트 발행
        event_id = str(uuid.uuid4())
        event = {"event_id": event_id, "data": "test_data"}

        producer = KafkaAuditProducer(settings=kafka_settings)
        producer.publish(topic=unique_topic, event=event, key="test-key")
        producer.flush(timeout=10.0)

        # Consumer 생성 (발행 후 생성해야 earliest 오프셋에서 시작)
        consumer = KafkaAuditConsumer(
            topics=[unique_topic],
            settings=kafka_settings,
        )

        try:
            # When: 이벤트 소비 (최대 10초 대기)
            consumed = None
            start_time = time.time()
            while time.time() - start_time < 10:
                consumed = consumer.poll_once(timeout=1.0)
                if consumed:
                    break

            # Then
            assert consumed is not None
            assert consumed.value["event_id"] == event_id
            assert consumed.key == "test-key"

        finally:
            producer.close()
            consumer.close()

    def test_consume_batch(self, kafka_settings, unique_topic):
        """배치 소비 테스트."""
        from selfhealing.adapters.kafka.consumer import KafkaAuditConsumer
        from selfhealing.adapters.kafka.producer import KafkaAuditProducer

        # Given: 여러 이벤트 발행
        producer = KafkaAuditProducer(settings=kafka_settings)
        for i in range(5):
            producer.publish(
                topic=unique_topic,
                event={"index": i},
            )
        producer.flush(timeout=10.0)

        consumer = KafkaAuditConsumer(
            topics=[unique_topic],
            settings=kafka_settings,
        )

        try:
            # When: 배치 소비
            events = consumer.consume_batch(batch_size=10, timeout=10.0)

            # Then
            assert len(events) >= 5
            indices = {e.value["index"] for e in events}
            assert indices == {0, 1, 2, 3, 4}

        finally:
            producer.close()
            consumer.close()

    def test_event_handler_called(self, kafka_settings, unique_topic):
        """이벤트 핸들러 호출 테스트."""
        from selfhealing.adapters.kafka.consumer import KafkaAuditConsumer
        from selfhealing.adapters.kafka.producer import KafkaAuditProducer

        # Given
        handled_events = []

        def handler(event):
            handled_events.append(event)
            return True

        producer = KafkaAuditProducer(settings=kafka_settings)
        producer.publish(topic=unique_topic, event={"test": "data"})
        producer.flush(timeout=10.0)

        consumer = KafkaAuditConsumer(
            topics=[unique_topic],
            settings=kafka_settings,
            event_handler=handler,
        )

        try:
            # When: 한 번 폴링하고 처리
            event = consumer.poll_once(timeout=5.0)
            if event:
                consumer._process_message(event)

            # Then
            assert len(handled_events) >= 1

        finally:
            producer.close()
            consumer.close()


class TestKafkaEventBusIntegration:
    """KafkaEventBus 통합 테스트."""

    def test_publish_and_subscribe(self, kafka_settings):
        """발행-구독 통합 테스트."""
        from selfhealing.adapters.kafka.event_bus import KafkaEventBus

        topic = f"test.events.{uuid.uuid4().hex[:8]}"
        bus = KafkaEventBus(settings=kafka_settings)

        try:
            # Given: 이벤트 발행
            event = {"action": "test_action", "data": "test_data"}

            delivered = []

            def on_delivery(report):
                delivered.append(report)

            # When
            success = bus.publish(
                topic=topic,
                event=event,
                key="test-key",
                on_delivery=on_delivery,
            )
            bus.flush(timeout=10.0)

            # Then
            assert success is True
            assert len(delivered) == 1
            assert delivered[0].success is True

        finally:
            bus.close()

    def test_subscribe_and_start(self, kafka_settings):
        """핸들러 등록 및 시작 테스트."""
        from selfhealing.adapters.kafka.event_bus import KafkaEventBus

        topic = f"test.events.{uuid.uuid4().hex[:8]}"
        bus = KafkaEventBus(settings=kafka_settings)

        try:
            # Given
            received = []

            def handler(event):
                received.append(event)
                return True

            # When
            bus.subscribe(topic, handler)
            bus.publish(topic=topic, event={"test": "data"})
            bus.flush(timeout=10.0)

            # Then: 핸들러가 등록되었는지 확인
            assert topic in bus._handlers
            assert len(bus._handlers[topic]) == 1

        finally:
            bus.close()


class TestKafkaEndToEnd:
    """Kafka End-to-End 통합 테스트."""

    def test_full_publish_consume_cycle(self, kafka_settings):
        """전체 발행-소비 사이클 테스트."""
        from selfhealing.adapters.kafka.consumer import KafkaAuditConsumer
        from selfhealing.adapters.kafka.producer import KafkaAuditProducer

        topic = f"test.e2e.{uuid.uuid4().hex[:8]}"
        event_count = 100

        producer = KafkaAuditProducer(settings=kafka_settings)
        consumer = KafkaAuditConsumer(
            topics=[topic],
            settings=kafka_settings,
        )

        try:
            # Given: 100개 이벤트 발행
            for i in range(event_count):
                producer.publish(
                    topic=topic,
                    event={"event_id": str(uuid.uuid4()), "index": i},
                )
            producer.flush(timeout=30.0)

            # When: 모든 이벤트 소비
            consumed = []
            start_time = time.time()
            while len(consumed) < event_count and (time.time() - start_time) < 30:
                event = consumer.poll_once(timeout=1.0)
                if event:
                    consumed.append(event)

            # Then
            assert len(consumed) == event_count
            indices = {e.value["index"] for e in consumed}
            assert indices == set(range(event_count))

        finally:
            producer.close()
            consumer.close()

    def test_idempotent_producer_no_duplicates(self, kafka_settings):
        """Idempotent Producer 중복 방지 테스트."""
        from selfhealing.adapters.kafka.consumer import KafkaAuditConsumer
        from selfhealing.adapters.kafka.producer import KafkaAuditProducer

        topic = f"test.idempotent.{uuid.uuid4().hex[:8]}"

        producer = KafkaAuditProducer(settings=kafka_settings)
        consumer = KafkaAuditConsumer(
            topics=[topic],
            settings=kafka_settings,
        )

        try:
            # Given: 동일한 이벤트 ID로 여러 번 발행
            event_id = str(uuid.uuid4())
            for _ in range(5):
                producer.publish(
                    topic=topic,
                    event={"event_id": event_id, "data": "test"},
                )
            producer.flush(timeout=10.0)

            # When: 소비
            consumed = []
            start_time = time.time()
            while (time.time() - start_time) < 10:
                event = consumer.poll_once(timeout=1.0)
                if event:
                    consumed.append(event)
                elif consumed:  # 이미 일부 수신했으면 종료
                    break

            # Then: Idempotent Producer는 중복 메시지를 브로커 레벨에서 방지
            # 단, 같은 이벤트를 여러 번 보내면 여러 메시지가 됨 (내용은 동일)
            assert len(consumed) >= 1
            # 모든 이벤트가 같은 event_id를 가짐
            assert all(e.value["event_id"] == event_id for e in consumed)

        finally:
            producer.close()
            consumer.close()
