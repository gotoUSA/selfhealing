"""
KafkaAuditAdapter 단위 테스트.

confluent-kafka Mock을 사용하여 KafkaAuditAdapter의
핵심 기능을 테스트합니다.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


class TestKafkaAuditSettings:
    """KafkaAuditSettings 테스트."""

    def test_default_settings(self):
        """기본 설정값 확인."""
        from selfhealing.settings.kafka import KafkaAuditSettings

        settings = KafkaAuditSettings()

        assert settings.bootstrap_servers == ["localhost:9092"]
        assert settings.topic == "selfhealing.audit.events"
        assert settings.dead_letter_topic == "selfhealing.audit.events.dlt"
        assert settings.enable_idempotence is True
        assert settings.batch_size_bytes == 16384
        assert settings.linger_ms == 10
        assert settings.compression_type == "zstd"
        assert settings.acks == "all"
        assert settings.partition_salt_enabled is True
        assert settings.partition_salt_range == 100

    def test_get_producer_config(self):
        """Producer 설정 딕셔너리 생성 확인."""
        from selfhealing.settings.kafka import KafkaAuditSettings

        settings = KafkaAuditSettings()
        config = settings.get_producer_config()

        assert "bootstrap.servers" in config
        assert config["enable.idempotence"] is True
        assert config["acks"] == "all"
        assert config["batch.size"] == 16384
        assert config["linger.ms"] == 10
        assert config["compression.type"] == "zstd"

    def test_sasl_ssl_config(self):
        """SASL/SSL 설정 확인."""
        from selfhealing.settings.kafka import KafkaAuditSettings

        settings = KafkaAuditSettings(
            security_protocol="SASL_SSL",
            sasl_mechanism="SCRAM-SHA-512",
            sasl_username="test-user",
            sasl_password="test-pass",
            ssl_cafile="/path/to/ca.crt",
        )
        config = settings.get_producer_config()

        assert config["security.protocol"] == "SASL_SSL"
        assert config["sasl.mechanism"] == "SCRAM-SHA-512"
        assert config["sasl.username"] == "test-user"
        assert config["sasl.password"] == "test-pass"
        assert config["ssl.ca.location"] == "/path/to/ca.crt"


class TestKafkaAuditAdapter:
    """KafkaAuditAdapter 테스트."""

    @pytest.fixture
    def mock_producer(self):
        """Mock Kafka Producer."""
        producer = MagicMock()
        producer.produce = MagicMock()
        producer.poll = MagicMock(return_value=0)
        producer.flush = MagicMock(return_value=0)
        return producer

    @pytest.fixture
    def mock_confluent_kafka(self):
        """Mock confluent_kafka module."""
        mock_error = MagicMock()
        mock_exception = MagicMock()
        mock_producer_class = MagicMock()
        return mock_producer_class, mock_error, mock_exception

    @pytest.fixture
    def settings(self):
        """테스트용 설정."""
        from selfhealing.settings.kafka import KafkaAuditSettings

        return KafkaAuditSettings(
            bootstrap_servers=["localhost:9092"],
            topic="test.audit.events",
        )

    @pytest.fixture
    def adapter(self, mock_producer, settings):
        """Mock Producer를 사용하는 어댑터."""
        from selfhealing.adapters.audit.kafka_adapter import KafkaAuditAdapter

        adapter = KafkaAuditAdapter(settings=settings, producer=mock_producer)
        return adapter

    def test_log_sends_to_kafka(self, adapter, mock_producer, mock_confluent_kafka):
        """log()가 Kafka로 전송하는지 확인."""
        from selfhealing.interfaces.audit_adapter import AuditEntry

        with patch(
            "selfhealing.adapters.audit.kafka_adapter._get_confluent_kafka",
            return_value=mock_confluent_kafka,
        ):
            entry = AuditEntry(
                action="TEST_ACTION",
                target_type="order",
                target_id="123",
            )

            adapter.log(entry)

            mock_producer.produce.assert_called_once()
            call_kwargs = mock_producer.produce.call_args.kwargs

            assert call_kwargs["topic"] == "test.audit.events"
            assert b"order:123" in call_kwargs["key"]
            assert b"TEST_ACTION" in call_kwargs["value"]

    def test_log_includes_schema_version(self, adapter, mock_producer, mock_confluent_kafka):
        """로그에 schema_version이 포함되는지 확인."""
        import json

        from selfhealing.interfaces.audit_adapter import AuditEntry

        with patch(
            "selfhealing.adapters.audit.kafka_adapter._get_confluent_kafka",
            return_value=mock_confluent_kafka,
        ):
            entry = AuditEntry(
                action="TEST_ACTION",
                target_type="product",
                target_id="456",
            )

            adapter.log(entry)

            call_kwargs = mock_producer.produce.call_args.kwargs
            value = json.loads(call_kwargs["value"].decode("utf-8"))

            assert value["schema_version"] == 1
            assert value["action"] == "TEST_ACTION"
            assert value["target_type"] == "product"
            assert value["target_id"] == "456"

    def test_partition_key_with_salt(self, adapter):
        """솔트가 적용된 파티션 키 생성 확인."""
        from selfhealing.interfaces.audit_adapter import AuditEntry

        entry = AuditEntry(
            action="TEST",
            target_type="order",
            target_id="789",
            timestamp=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        )

        key = adapter._compute_partition_key(entry)

        assert key.startswith("order:789:")
        assert len(key.split(":")) == 3

    def test_partition_key_without_target_id(self, adapter):
        """target_id가 없을 때 None 반환 확인."""
        from selfhealing.interfaces.audit_adapter import AuditEntry

        entry = AuditEntry(
            action="TEST",
            target_type="order",
            target_id=None,
        )

        key = adapter._compute_partition_key(entry)
        assert key is None

    def test_log_is_non_blocking(self, adapter, mock_producer, mock_confluent_kafka):
        """log()가 Non-blocking인지 확인."""
        from selfhealing.interfaces.audit_adapter import AuditEntry

        with patch(
            "selfhealing.adapters.audit.kafka_adapter._get_confluent_kafka",
            return_value=mock_confluent_kafka,
        ):
            entry = AuditEntry(action="TEST", target_type="test", target_id="1")

            start = time.time()
            for _ in range(100):
                adapter.log(entry)
            elapsed = time.time() - start

            # 100개 전송이 100ms 이내 (produce()는 non-blocking)
            assert elapsed < 0.1

    def test_log_batch(self, adapter, mock_producer, mock_confluent_kafka):
        """배치 로깅 테스트."""
        from selfhealing.interfaces.audit_adapter import AuditEntry

        with patch(
            "selfhealing.adapters.audit.kafka_adapter._get_confluent_kafka",
            return_value=mock_confluent_kafka,
        ):
            entries = [
                AuditEntry(action="TEST1", target_type="order", target_id="1"),
                AuditEntry(action="TEST2", target_type="order", target_id="2"),
                AuditEntry(action="TEST3", target_type="order", target_id="3"),
            ]

            adapter.log_batch(entries)

            assert mock_producer.produce.call_count == 3

    def test_close_flushes_buffer(self, adapter, mock_producer):
        """close()가 버퍼를 플러시하는지 확인."""
        adapter.close()

        mock_producer.flush.assert_called()

    def test_get_stats(self, adapter):
        """통계 반환 테스트."""
        stats = adapter.get_stats()

        assert "sent_count" in stats
        assert "error_count" in stats
        assert "pending_count" in stats
        assert "last_delivery_time" in stats
        assert "closed" in stats
        assert stats["closed"] is False

    def test_is_healthy(self, adapter):
        """건강 상태 확인 테스트."""
        assert adapter.is_healthy() is True

        # pending_count를 높이면 unhealthy
        adapter._pending_count = 90000
        assert adapter.is_healthy() is False

    def test_closed_adapter_ignores_log(self, adapter, mock_producer):
        """닫힌 어댑터는 로그를 무시."""
        from selfhealing.interfaces.audit_adapter import AuditEntry

        adapter.close()
        mock_producer.produce.reset_mock()

        entry = AuditEntry(action="TEST", target_type="test", target_id="1")
        adapter.log(entry)

        mock_producer.produce.assert_not_called()

    def test_delivery_callback_success(self, adapter):
        """전송 성공 콜백 테스트."""
        adapter._pending_count = 1

        adapter._delivery_callback(err=None, msg=MagicMock())

        assert adapter._sent_count == 1
        assert adapter._pending_count == 0
        assert adapter._last_delivery_time is not None

    def test_delivery_callback_error(self, adapter):
        """전송 실패 콜백 테스트."""
        adapter._pending_count = 1
        mock_error = MagicMock()
        mock_error.__str__ = lambda x: "Test error"

        adapter._delivery_callback(err=mock_error, msg=None)

        assert adapter._error_count == 1
        assert adapter._pending_count == 0

    def test_headers_include_causation(self, adapter, mock_producer, mock_confluent_kafka):
        """Causation 헤더가 포함되는지 확인."""
        from selfhealing.context.causation_context import CausationContext
        from selfhealing.interfaces.audit_adapter import AuditEntry

        with patch(
            "selfhealing.adapters.audit.kafka_adapter._get_confluent_kafka",
            return_value=mock_confluent_kafka,
        ):
            with CausationContext.start_cascade(namespace="test") as ctx:
                entry = AuditEntry(action="TEST", target_type="test", target_id="1")
                adapter.log(entry)

            call_kwargs = mock_producer.produce.call_args.kwargs
            headers = dict(call_kwargs["headers"])

            assert b"x-audit-action" in headers or "x-audit-action" in headers
            assert b"x-region" in headers or "x-region" in headers

    def test_query_raises_not_implemented(self, adapter):
        """query()는 NotImplementedError 발생."""
        with pytest.raises(NotImplementedError):
            adapter.query()


class TestKafkaAuditAdapterSingleton:
    """KafkaAuditAdapter 싱글톤 테스트."""

    def test_get_kafka_audit_adapter_singleton(self):
        """싱글톤 패턴 확인."""
        from selfhealing.adapters.audit.kafka_adapter import (
            get_kafka_audit_adapter,
            reset_kafka_audit_adapter,
        )

        reset_kafka_audit_adapter()

        with patch("selfhealing.adapters.audit.kafka_adapter.KafkaAuditAdapter._create_producer") as mock:
            mock.return_value = MagicMock()

            adapter1 = get_kafka_audit_adapter()
            adapter2 = get_kafka_audit_adapter()

            assert adapter1 is adapter2

        reset_kafka_audit_adapter()


class TestKafkaCheckpointManager:
    """KafkaCheckpointManager 테스트."""

    @pytest.fixture
    def temp_file_path(self, tmp_path):
        """임시 파일 경로."""
        return tmp_path / "checkpoint.json"

    def test_save_and_load_checkpoint(self, temp_file_path):
        """체크포인트 저장 및 로드 테스트."""
        from selfhealing.audit.kafka_checkpoint import KafkaCheckpointManager

        manager = KafkaCheckpointManager(
            storage="file",
            file_path=temp_file_path,
        )

        manager.save_checkpoint(
            namespace="test",
            wal_sequence=100,
            kafka_topic="test.topic",
            kafka_partition=3,
            kafka_offset=5000,
            checksum="abc123",
        )

        data = manager.get_last_checkpoint("test")

        assert data is not None
        assert data.wal_sequence == 100
        assert data.kafka_topic == "test.topic"
        assert data.kafka_partition == 3
        assert data.kafka_offset == 5000
        assert data.checksum == "abc123"

    def test_get_wal_sequence(self, temp_file_path):
        """WAL 시퀀스 조회 테스트."""
        from selfhealing.audit.kafka_checkpoint import KafkaCheckpointManager

        manager = KafkaCheckpointManager(
            storage="file",
            file_path=temp_file_path,
        )

        # 체크포인트가 없으면 0 반환
        assert manager.get_wal_sequence("nonexistent") == 0

        manager.save_checkpoint(
            namespace="test",
            wal_sequence=200,
            kafka_topic="test.topic",
            kafka_partition=0,
            kafka_offset=0,
            checksum="xyz",
        )

        assert manager.get_wal_sequence("test") == 200

    def test_delete_checkpoint(self, temp_file_path):
        """체크포인트 삭제 테스트."""
        from selfhealing.audit.kafka_checkpoint import KafkaCheckpointManager

        manager = KafkaCheckpointManager(
            storage="file",
            file_path=temp_file_path,
        )

        manager.save_checkpoint(
            namespace="test",
            wal_sequence=100,
            kafka_topic="test.topic",
            kafka_partition=0,
            kafka_offset=0,
            checksum="abc",
        )

        assert manager.get_last_checkpoint("test") is not None

        result = manager.delete_checkpoint("test")

        assert result is True
        assert manager.get_last_checkpoint("test") is None

    def test_redis_storage_requires_client(self):
        """Redis 저장소는 클라이언트 필요."""
        from selfhealing.audit.kafka_checkpoint import KafkaCheckpointManager

        with pytest.raises(ValueError, match="redis_client is required"):
            KafkaCheckpointManager(storage="redis", redis_client=None)


class TestKafkaCheckpointData:
    """KafkaCheckpointData 테스트."""

    def test_to_dict_and_from_dict(self):
        """직렬화/역직렬화 테스트."""
        from selfhealing.audit.kafka_checkpoint import KafkaCheckpointData

        data = KafkaCheckpointData(
            wal_sequence=100,
            kafka_topic="test.topic",
            kafka_partition=5,
            kafka_offset=10000,
            timestamp="2026-01-31T12:00:00+00:00",
            checksum="abc123",
        )

        data_dict = data.to_dict()
        restored = KafkaCheckpointData.from_dict(data_dict)

        assert restored.wal_sequence == data.wal_sequence
        assert restored.kafka_topic == data.kafka_topic
        assert restored.kafka_partition == data.kafka_partition
        assert restored.kafka_offset == data.kafka_offset
        assert restored.timestamp == data.timestamp
        assert restored.checksum == data.checksum


class TestSerializationFormat:
    """SerializationFormat 테스트."""

    def test_serialization_format_enum(self):
        """직렬화 포맷 열거형 테스트."""
        from selfhealing.settings.kafka import SerializationFormat

        assert SerializationFormat.JSON.value == "json"
        assert SerializationFormat.AVRO.value == "avro"
        assert SerializationFormat.PROTOBUF.value == "protobuf"


class TestSyncWalToKafkaWithCheckpoint:
    """sync_wal_to_kafka_with_checkpoint 함수 테스트."""

    @pytest.fixture
    def temp_checkpoint_path(self, tmp_path):
        """임시 체크포인트 파일 경로."""
        return tmp_path / "checkpoint.json"

    @pytest.fixture
    def mock_wal(self):
        """Mock WAL 인스턴스."""
        mock = MagicMock()
        return mock

    @pytest.fixture
    def mock_kafka_producer(self):
        """Mock KafkaAuditProducer 인스턴스."""
        mock = MagicMock()
        mock.publish_audit_event = MagicMock(return_value=True)
        mock.flush = MagicMock(return_value=0)
        mock._settings = MagicMock()
        mock._settings.full_audit_topic = "selfhealing.audit.events"
        return mock

    @pytest.fixture
    def checkpoint_manager(self, temp_checkpoint_path):
        """테스트용 KafkaCheckpointManager."""
        from selfhealing.audit.kafka_checkpoint import KafkaCheckpointManager

        return KafkaCheckpointManager(
            storage="file",
            file_path=temp_checkpoint_path,
        )

    def test_sync_with_new_kafka_producer(self, mock_wal, mock_kafka_producer, checkpoint_manager):
        """KafkaAuditProducer를 사용한 동기화 테스트."""
        from selfhealing.audit.kafka_checkpoint import sync_wal_to_kafka_with_checkpoint

        # Mock WAL 엔트리 설정
        mock_entry1 = MagicMock()
        mock_entry1.sequence = 1
        mock_entry1.data = {"event_type": "test", "data": "entry1"}
        mock_entry1.checksum = "checksum1"

        mock_entry2 = MagicMock()
        mock_entry2.sequence = 2
        mock_entry2.data = {"event_type": "test", "data": "entry2"}
        mock_entry2.checksum = "checksum2"

        mock_wal.recover_unprocessed = MagicMock(return_value=[mock_entry1, mock_entry2])

        # 동기화 수행
        synced = sync_wal_to_kafka_with_checkpoint(
            wal=mock_wal,
            producer=mock_kafka_producer,
            checkpoint=checkpoint_manager,
            namespace="test",
        )

        # 결과 검증
        assert synced == 2
        assert mock_kafka_producer.publish_audit_event.call_count == 2
        assert mock_kafka_producer.flush.call_count == 2

        # 체크포인트가 저장되었는지 확인
        last_cp = checkpoint_manager.get_last_checkpoint("test")
        assert last_cp is not None
        assert last_cp.wal_sequence == 2
        assert last_cp.kafka_topic == "selfhealing.audit.events"

    def test_sync_resumes_from_checkpoint(self, mock_wal, mock_kafka_producer, checkpoint_manager):
        """체크포인트부터 재개되는지 테스트."""
        from selfhealing.audit.kafka_checkpoint import sync_wal_to_kafka_with_checkpoint

        # 기존 체크포인트 저장
        checkpoint_manager.save_checkpoint(
            namespace="test",
            wal_sequence=5,
            kafka_topic="old.topic",
            kafka_partition=0,
            kafka_offset=0,
            checksum="old",
        )

        # Mock WAL 엔트리 설정 (시퀀스 6 이후만 반환)
        mock_entry = MagicMock()
        mock_entry.sequence = 6
        mock_entry.data = {"event_type": "test", "data": "entry6"}
        mock_entry.checksum = "checksum6"

        mock_wal.recover_unprocessed = MagicMock(return_value=[mock_entry])

        # 동기화 수행
        synced = sync_wal_to_kafka_with_checkpoint(
            wal=mock_wal,
            producer=mock_kafka_producer,
            checkpoint=checkpoint_manager,
            namespace="test",
        )

        # WAL이 마지막 시퀀스 5 이후부터 조회되었는지 확인
        mock_wal.recover_unprocessed.assert_called_once_with(last_processed_seq=5)
        assert synced == 1

    def test_sync_stops_on_publish_failure(self, mock_wal, mock_kafka_producer, checkpoint_manager):
        """발행 실패 시 동기화 중단 테스트."""
        from selfhealing.audit.kafka_checkpoint import sync_wal_to_kafka_with_checkpoint

        # Mock 설정: 첫 번째는 성공, 두 번째는 실패
        mock_kafka_producer.publish_audit_event = MagicMock(side_effect=[True, False])

        mock_entry1 = MagicMock()
        mock_entry1.sequence = 1
        mock_entry1.data = {"event_type": "test"}
        mock_entry1.checksum = "checksum1"

        mock_entry2 = MagicMock()
        mock_entry2.sequence = 2
        mock_entry2.data = {"event_type": "test"}
        mock_entry2.checksum = "checksum2"

        mock_wal.recover_unprocessed = MagicMock(return_value=[mock_entry1, mock_entry2])

        # 동기화 수행
        synced = sync_wal_to_kafka_with_checkpoint(
            wal=mock_wal,
            producer=mock_kafka_producer,
            checkpoint=checkpoint_manager,
            namespace="test",
        )

        # 첫 번째만 동기화됨
        assert synced == 1

        # 체크포인트는 첫 번째까지만 저장
        last_cp = checkpoint_manager.get_last_checkpoint("test")
        assert last_cp is not None
        assert last_cp.wal_sequence == 1

    def test_sync_with_empty_wal(self, mock_wal, mock_kafka_producer, checkpoint_manager):
        """빈 WAL 동기화 테스트."""
        from selfhealing.audit.kafka_checkpoint import sync_wal_to_kafka_with_checkpoint

        mock_wal.recover_unprocessed = MagicMock(return_value=[])

        synced = sync_wal_to_kafka_with_checkpoint(
            wal=mock_wal,
            producer=mock_kafka_producer,
            checkpoint=checkpoint_manager,
            namespace="test",
        )

        assert synced == 0
        assert mock_kafka_producer.publish_audit_event.call_count == 0

    def test_sync_with_legacy_adapter(self, mock_wal, checkpoint_manager):
        """기존 KafkaAuditAdapter와의 하위 호환성 테스트."""
        from selfhealing.audit.kafka_checkpoint import sync_wal_to_kafka_with_checkpoint

        # 기존 어댑터 Mock (publish_audit_event 없음)
        mock_adapter = MagicMock(spec=["log", "flush", "_settings"])
        mock_adapter.log = MagicMock()
        mock_adapter.flush = MagicMock()
        mock_adapter._settings = MagicMock()
        mock_adapter._settings.topic = "legacy.topic"

        mock_entry = MagicMock()
        mock_entry.sequence = 1
        # AuditEntry에 맞는 필드 사용
        mock_entry.data = {"action": "dlq_store", "target_type": "dlq_entry", "target_id": "test123"}
        mock_entry.checksum = "checksum1"

        mock_wal.recover_unprocessed = MagicMock(return_value=[mock_entry])

        # 동기화 수행
        synced = sync_wal_to_kafka_with_checkpoint(
            wal=mock_wal,
            producer=mock_adapter,  # 기존 어댑터
            checkpoint=checkpoint_manager,
            namespace="test",
        )

        assert synced == 1
        assert mock_adapter.log.call_count == 1
        assert mock_adapter.flush.call_count == 1

        # 체크포인트 확인
        last_cp = checkpoint_manager.get_last_checkpoint("test")
        assert last_cp.kafka_topic == "legacy.topic"

    def test_sync_stops_on_exception(self, mock_wal, mock_kafka_producer, checkpoint_manager):
        """예외 발생 시 동기화 중단 테스트."""
        from selfhealing.audit.kafka_checkpoint import sync_wal_to_kafka_with_checkpoint

        mock_kafka_producer.publish_audit_event = MagicMock(side_effect=Exception("Kafka connection error"))

        mock_entry = MagicMock()
        mock_entry.sequence = 1
        mock_entry.data = {"event_type": "test"}
        mock_entry.checksum = "checksum1"

        mock_wal.recover_unprocessed = MagicMock(return_value=[mock_entry])

        # 동기화 수행 (예외가 발생해도 함수는 정상 종료)
        synced = sync_wal_to_kafka_with_checkpoint(
            wal=mock_wal,
            producer=mock_kafka_producer,
            checkpoint=checkpoint_manager,
            namespace="test",
        )

        assert synced == 0
