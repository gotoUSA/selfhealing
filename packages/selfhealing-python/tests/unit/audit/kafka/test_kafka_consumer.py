"""
Kafka Audit Consumer 단위 테스트.

BaseAuditConsumer, IdempotentAuditConsumer, RebalanceAwareConsumer,
PostgreSQLSinkConsumer의 핵심 기능을 테스트합니다.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


class TestKafkaConsumerConfig:
    """KafkaConsumerConfig 테스트."""

    def test_default_config(self):
        """기본 설정값 확인."""
        from selfhealing.adapters.audit.kafka_consumer import KafkaConsumerConfig

        config = KafkaConsumerConfig()

        assert config.bootstrap_servers == ["localhost:9092"]
        assert config.group_id == "selfhealing-audit-consumer"
        assert config.topic == "selfhealing.audit.events"
        assert config.auto_offset_reset == "earliest"
        assert config.enable_auto_commit is False
        assert config.partition_assignment_strategy == "cooperative-sticky"

    def test_get_consumer_config(self):
        """Consumer 설정 딕셔너리 생성 확인."""
        from selfhealing.adapters.audit.kafka_consumer import KafkaConsumerConfig

        config = KafkaConsumerConfig(
            bootstrap_servers=["kafka1:9092", "kafka2:9092"],
            group_id="test-group",
        )
        consumer_config = config.get_consumer_config()

        assert consumer_config["bootstrap.servers"] == "kafka1:9092,kafka2:9092"
        assert consumer_config["group.id"] == "test-group"
        assert consumer_config["enable.auto.commit"] is False

    def test_sasl_config(self):
        """SASL 설정 확인."""
        from selfhealing.adapters.audit.kafka_consumer import KafkaConsumerConfig

        config = KafkaConsumerConfig(
            security_protocol="SASL_SSL",
            sasl_mechanism="SCRAM-SHA-512",
            sasl_username="test-user",
            sasl_password="test-pass",
        )
        consumer_config = config.get_consumer_config()

        assert consumer_config["security.protocol"] == "SASL_SSL"
        assert consumer_config["sasl.mechanism"] == "SCRAM-SHA-512"
        assert consumer_config["sasl.username"] == "test-user"
        assert consumer_config["sasl.password"] == "test-pass"


class TestBaseAuditConsumer:
    """BaseAuditConsumer 테스트."""

    @pytest.fixture
    def mock_consumer(self):
        """Mock Kafka Consumer."""
        consumer = MagicMock()
        consumer.poll = MagicMock(return_value=None)
        consumer.commit = MagicMock()
        consumer.close = MagicMock()
        consumer.subscribe = MagicMock()
        return consumer

    @pytest.fixture
    def config(self):
        """테스트용 설정."""
        from selfhealing.adapters.audit.kafka_consumer import KafkaConsumerConfig

        return KafkaConsumerConfig(
            bootstrap_servers=["localhost:9092"],
            topic="test.audit.events",
            group_id="test-group",
        )

    def test_get_stats(self, mock_consumer, config):
        """통계 반환 테스트."""
        from selfhealing.adapters.audit.kafka_consumer import IdempotentAuditConsumer

        consumer = IdempotentAuditConsumer(config=config, consumer=mock_consumer)

        stats = consumer.get_stats()

        assert "processed_count" in stats
        assert "error_count" in stats
        assert "skipped_count" in stats
        assert "running" in stats
        assert stats["running"] is False

    def test_close_cleans_up(self, mock_consumer, config):
        """close()가 리소스를 정리하는지 확인."""
        from selfhealing.adapters.audit.kafka_consumer import IdempotentAuditConsumer

        consumer = IdempotentAuditConsumer(config=config, consumer=mock_consumer)
        consumer.close()

        mock_consumer.close.assert_called_once()


class TestIdempotentAuditConsumer:
    """IdempotentAuditConsumer 테스트."""

    @pytest.fixture
    def mock_consumer(self):
        """Mock Kafka Consumer."""
        consumer = MagicMock()
        consumer.poll = MagicMock(return_value=None)
        consumer.commit = MagicMock()
        consumer.close = MagicMock()
        consumer.subscribe = MagicMock()
        return consumer

    @pytest.fixture
    def config(self):
        """테스트용 설정."""
        from selfhealing.adapters.audit.kafka_consumer import KafkaConsumerConfig

        return KafkaConsumerConfig()

    @pytest.fixture
    def idempotent_consumer(self, mock_consumer, config):
        """IdempotentAuditConsumer 인스턴스."""
        from selfhealing.adapters.audit.kafka_consumer import IdempotentAuditConsumer

        return IdempotentAuditConsumer(
            config=config,
            consumer=mock_consumer,
            cache_max_size=1000,
        )

    def test_first_message_processed(self, idempotent_consumer):
        """첫 번째 메시지가 정상 처리되는지 확인."""
        mock_message = MagicMock()
        mock_message.value.return_value = json.dumps(
            {
                "id": "unique-id-1",
                "action": "TEST_ACTION",
            }
        ).encode("utf-8")
        mock_message.topic.return_value = "test"
        mock_message.partition.return_value = 0
        mock_message.offset.return_value = 1

        result = idempotent_consumer.process_message(mock_message)

        assert result is True
        assert idempotent_consumer._skipped_count == 0

    def test_duplicate_message_skipped(self, idempotent_consumer):
        """중복 메시지가 스킵되는지 확인."""
        mock_message = MagicMock()
        mock_message.value.return_value = json.dumps(
            {
                "id": "unique-id-2",
                "action": "TEST_ACTION",
            }
        ).encode("utf-8")
        mock_message.topic.return_value = "test"
        mock_message.partition.return_value = 0
        mock_message.offset.return_value = 2

        # 첫 번째 처리
        result1 = idempotent_consumer.process_message(mock_message)
        assert result1 is True
        assert idempotent_consumer._skipped_count == 0

        # 두 번째 처리 (중복)
        result2 = idempotent_consumer.process_message(mock_message)
        assert result2 is True
        assert idempotent_consumer._skipped_count == 1

    def test_cache_eviction_on_max_size(self, config, mock_consumer):
        """캐시 최대 크기 초과 시 오래된 항목 삭제 확인."""
        from selfhealing.adapters.audit.kafka_consumer import IdempotentAuditConsumer

        consumer = IdempotentAuditConsumer(
            config=config,
            consumer=mock_consumer,
            cache_max_size=10,
        )

        # 10개 메시지 처리
        for i in range(10):
            mock_message = MagicMock()
            mock_message.value.return_value = json.dumps(
                {
                    "id": f"id-{i}",
                    "action": "TEST",
                }
            ).encode("utf-8")
            mock_message.topic.return_value = "test"
            mock_message.partition.return_value = 0
            mock_message.offset.return_value = i

            consumer.process_message(mock_message)

        # 캐시가 10개까지 차 있음
        assert len(consumer._memory_cache) == 10

        # 11번째 메시지 처리 시 캐시 정리됨
        mock_message = MagicMock()
        mock_message.value.return_value = json.dumps(
            {
                "id": "id-11",
                "action": "TEST",
            }
        ).encode("utf-8")
        mock_message.topic.return_value = "test"
        mock_message.partition.return_value = 0
        mock_message.offset.return_value = 11

        consumer.process_message(mock_message)

        # 10% 삭제 후 추가되므로 10개 유지
        assert len(consumer._memory_cache) <= 10


class TestRebalanceAwareConsumer:
    """RebalanceAwareConsumer 테스트."""

    @pytest.fixture
    def mock_consumer(self):
        """Mock Kafka Consumer."""
        consumer = MagicMock()
        consumer.poll = MagicMock(return_value=None)
        consumer.commit = MagicMock()
        consumer.close = MagicMock()
        consumer.subscribe = MagicMock()
        consumer.committed = MagicMock(return_value=[MagicMock(offset=100)])
        consumer.assign = MagicMock()
        return consumer

    @pytest.fixture
    def config(self):
        """테스트용 설정."""
        from selfhealing.adapters.audit.kafka_consumer import KafkaConsumerConfig

        return KafkaConsumerConfig()

    def test_on_assign_restores_offset(self, mock_consumer, config):
        """파티션 할당 시 오프셋이 복구되는지 확인."""
        from selfhealing.adapters.audit.kafka_consumer import RebalanceAwareConsumer

        rebalance_events = []

        def on_rebalance(event_type, partitions):
            rebalance_events.append((event_type, len(partitions)))

        consumer = RebalanceAwareConsumer(
            config=config,
            consumer=mock_consumer,
            on_rebalance=on_rebalance,
        )

        mock_partition = MagicMock()
        mock_partition.topic = "test"
        mock_partition.partition = 0
        mock_partition.offset = -1

        consumer._on_assign(mock_consumer, [mock_partition])

        assert ("assign", 1) in rebalance_events
        mock_consumer.assign.assert_called_once()

    def test_on_revoke_commits_pending(self, mock_consumer, config):
        """파티션 해제 시 pending 오프셋이 커밋되는지 확인."""
        from selfhealing.adapters.audit.kafka_consumer import RebalanceAwareConsumer

        rebalance_events = []

        def on_rebalance(event_type, partitions):
            rebalance_events.append((event_type, len(partitions)))

        consumer = RebalanceAwareConsumer(
            config=config,
            consumer=mock_consumer,
            on_rebalance=on_rebalance,
        )

        # pending offset 추가
        mock_offset = MagicMock()
        consumer._pending_offsets[("test", 0)] = mock_offset

        mock_partition = MagicMock()
        consumer._on_revoke(mock_consumer, [mock_partition])

        assert ("revoke", 1) in rebalance_events
        mock_consumer.commit.assert_called_once()
        assert len(consumer._pending_offsets) == 0


class TestPostgreSQLSinkConsumer:
    """PostgreSQLSinkConsumer 테스트."""

    @pytest.fixture
    def mock_consumer(self):
        """Mock Kafka Consumer."""
        consumer = MagicMock()
        consumer.poll = MagicMock(return_value=None)
        consumer.commit = MagicMock()
        consumer.close = MagicMock()
        consumer.subscribe = MagicMock()
        return consumer

    @pytest.fixture
    def mock_db_connection(self):
        """Mock DB 연결."""
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        conn.commit = MagicMock()
        conn.rollback = MagicMock()
        conn.close = MagicMock()
        return conn

    @pytest.fixture
    def sink_consumer(self, mock_consumer, mock_db_connection):
        """PostgreSQLSinkConsumer 인스턴스."""
        from selfhealing.adapters.audit.kafka_consumer import (
            KafkaConsumerConfig,
            PostgreSQLSinkConfig,
            PostgreSQLSinkConsumer,
        )

        kafka_config = KafkaConsumerConfig()
        sink_config = PostgreSQLSinkConfig(batch_size=5)

        return PostgreSQLSinkConsumer(
            kafka_config=kafka_config,
            sink_config=sink_config,
            consumer=mock_consumer,
            db_connection=mock_db_connection,
        )

    def test_messages_batched(self, sink_consumer):
        """메시지가 배치로 처리되는지 확인."""
        mock_message = MagicMock()
        mock_message.value.return_value = json.dumps(
            {
                "id": "batch-id-1",
                "action": "TEST",
                "target_type": "order",
                "target_id": "123",
                "details": {},
            }
        ).encode("utf-8")
        mock_message.topic.return_value = "test"
        mock_message.partition.return_value = 0
        mock_message.offset.return_value = 1

        # 4개 처리 (배치 크기 5 미만)
        for _ in range(4):
            sink_consumer._do_process(mock_message)

        assert len(sink_consumer._batch) == 4

    def test_batch_flushed_on_size(self, sink_consumer, mock_db_connection):
        """배치가 크기에 도달하면 플러시되는지 확인."""
        # psycopg2.extras 모킹
        with patch("selfhealing.adapters.audit.kafka_consumer.PostgreSQLSinkConsumer._flush_batch") as mock_flush:
            mock_flush.return_value = True

            for i in range(5):
                mock_message = MagicMock()
                mock_message.value.return_value = json.dumps(
                    {
                        "id": f"batch-id-{i}",
                        "action": "TEST",
                    }
                ).encode("utf-8")
                mock_message.topic.return_value = "test"
                mock_message.partition.return_value = 0
                mock_message.offset.return_value = i

                sink_consumer._do_process(mock_message)

            # 5개 처리 시 플러시 호출됨
            mock_flush.assert_called_once()

    def test_close_flushes_remaining(self, sink_consumer):
        """close()가 남은 배치를 플러시하는지 확인."""
        # 배치에 데이터 추가
        sink_consumer._batch.append(("id", "action", "type", "id", None, "{}", None, None, True, None))

        with patch.object(sink_consumer, "_flush_batch") as mock_flush:
            mock_flush.return_value = True
            sink_consumer.close()

            mock_flush.assert_called_once()


class TestPostgreSQLSinkConfig:
    """PostgreSQLSinkConfig 테스트."""

    def test_default_config(self):
        """기본 설정값 확인."""
        from selfhealing.adapters.audit.kafka_consumer import PostgreSQLSinkConfig

        config = PostgreSQLSinkConfig()

        assert config.table_name == "audit_log"
        assert config.batch_size == 500
        assert config.upsert_on_conflict is True
