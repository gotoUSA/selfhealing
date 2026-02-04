"""
Kafka Event Bus 어댑터.

Memory Buffer 휘발성 문제를 해결하기 위한 Kafka 기반 이벤트 버스 구현.
Pod 재시작, Redis 장애에도 이벤트 손실 0%를 목표로 합니다.

핵심 컴포넌트:
- KafkaSettings: Kafka 연결 및 동작 설정
- KafkaAuditProducer: Idempotent Producer (중복 메시지 방지)
- KafkaAuditConsumer: 수동 오프셋 관리 Consumer
- KafkaEventBus: Publisher/Subscriber 통합 인터페이스

Usage:
    from selfhealing.adapters.kafka import (
        KafkaEventBus,
        KafkaAuditProducer,
        KafkaAuditConsumer,
        KafkaSettings,
    )

    # Event Bus 사용
    with KafkaEventBus() as bus:
        bus.publish("audit.events", {"action": "dlq_store"})

    # Producer 직접 사용
    with KafkaAuditProducer() as producer:
        producer.publish_audit_event(event_data)

    # Consumer 직접 사용
    with KafkaAuditConsumer(topics=["audit.events"]) as consumer:
        consumer.run()
"""

from selfhealing.adapters.kafka.config import (
    KafkaSettings,
    get_kafka_settings,
    reset_kafka_settings,
)
from selfhealing.adapters.kafka.consumer import (
    ConsumedEvent,
    EventHandler,
    KafkaAuditConsumer,
)
from selfhealing.adapters.kafka.event_bus import KafkaEventBus
from selfhealing.adapters.kafka.metrics import (
    TimeLagTracker,
    record_kafka_message_processed,
)
from selfhealing.adapters.kafka.producer import (
    DeliveryReport,
    KafkaAuditProducer,
    get_kafka_producer,
    reset_kafka_producer,
)
from selfhealing.adapters.kafka.retry import (
    NonBlockingRetryHandler,
    RetryTopicConfig,
)
from selfhealing.adapters.kafka.schemas import AuditEventSchemaRegistry

__all__ = [
    # Config
    "KafkaSettings",
    "get_kafka_settings",
    "reset_kafka_settings",
    # Producer
    "KafkaAuditProducer",
    "DeliveryReport",
    "get_kafka_producer",
    "reset_kafka_producer",
    # Consumer
    "KafkaAuditConsumer",
    "ConsumedEvent",
    "EventHandler",
    # Event Bus
    "KafkaEventBus",
    # Schemas
    "AuditEventSchemaRegistry",
    # Metrics
    "TimeLagTracker",
    "record_kafka_message_processed",
    # Retry
    "RetryTopicConfig",
    "NonBlockingRetryHandler",
]
