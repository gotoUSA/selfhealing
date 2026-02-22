"""
Kafka 통합 테스트용 pytest fixtures.

환경변수:
    SELFHEALING_KAFKA_BOOTSTRAP_SERVERS: Kafka 브로커 주소
    SELFHEALING_KAFKA_TOPIC_PREFIX: 테스트용 토픽 프리픽스
"""

from __future__ import annotations

import os
import sys
import uuid

import pytest

# selfhealing 패키지 경로 추가
_selfhealing_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "packages", "selfhealing-python", "src")
if os.path.exists(_selfhealing_path):
    sys.path.insert(0, os.path.abspath(_selfhealing_path))


def is_kafka_available() -> bool:
    """Kafka 브로커 사용 가능 여부 확인."""
    bootstrap_servers = os.getenv("SELFHEALING_KAFKA_BOOTSTRAP_SERVERS", "localhost:19092")
    if not bootstrap_servers:
        return False

    try:
        from confluent_kafka.admin import AdminClient

        admin = AdminClient({"bootstrap.servers": bootstrap_servers})
        # 메타데이터 조회로 연결 확인
        metadata = admin.list_topics(timeout=5)
        return metadata is not None
    except Exception:
        return False


# Kafka 사용 가능할 때만 통합 테스트 실행
pytestmark = pytest.mark.skipif(
    not is_kafka_available(),
    reason="Kafka 브로커에 연결할 수 없습니다. SELFHEALING_KAFKA_BOOTSTRAP_SERVERS 환경변수를 확인하세요.",
)


@pytest.fixture(scope="session")
def kafka_bootstrap_servers() -> str:
    """Kafka 브로커 주소."""
    return os.getenv("SELFHEALING_KAFKA_BOOTSTRAP_SERVERS", "localhost:19092")


@pytest.fixture(scope="session")
def kafka_settings(kafka_bootstrap_servers: str):
    """테스트용 Kafka 설정."""
    from selfhealing.adapters.kafka.config import KafkaSettings, reset_kafka_settings

    # 캐시 초기화
    reset_kafka_settings()

    # 테스트용 설정 생성
    settings = KafkaSettings(
        bootstrap_servers=kafka_bootstrap_servers,
        topic_prefix=f"test.{uuid.uuid4().hex[:8]}.",  # 고유 프리픽스
        producer_acks="all",
        producer_idempotent=True,
        consumer_group_id=f"test-group-{uuid.uuid4().hex[:8]}",
        consumer_enable_auto_commit=False,
        consumer_auto_offset_reset="earliest",
    )

    yield settings

    # 정리: 캐시 초기화
    reset_kafka_settings()


@pytest.fixture
def unique_topic() -> str:
    """고유한 테스트 토픽 이름 생성."""
    return f"test.events.{uuid.uuid4().hex[:8]}"


@pytest.fixture
def kafka_producer(kafka_settings):
    """테스트용 Kafka Producer."""
    from selfhealing.adapters.kafka.producer import KafkaAuditProducer

    producer = KafkaAuditProducer(settings=kafka_settings)
    yield producer
    producer.close()


@pytest.fixture
def kafka_consumer(kafka_settings, unique_topic):
    """테스트용 Kafka Consumer."""
    from selfhealing.adapters.kafka.consumer import KafkaAuditConsumer

    consumer = KafkaAuditConsumer(
        topics=[unique_topic],
        settings=kafka_settings,
    )
    yield consumer
    consumer.close()


@pytest.fixture
def kafka_event_bus(kafka_settings):
    """테스트용 Kafka Event Bus."""
    from selfhealing.adapters.kafka.event_bus import KafkaEventBus

    bus = KafkaEventBus(settings=kafka_settings)
    yield bus
    bus.close()
