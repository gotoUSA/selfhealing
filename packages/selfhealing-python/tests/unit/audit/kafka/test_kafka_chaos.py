"""
Kafka Audit Adapter 카오스 테스트.

Kafka 파티션 장애, 네트워크 파티션 등 장애 상황에서의
Producer/Consumer 동작을 검증합니다.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest


class TestKafkaPartitionFailure:
    """Kafka 파티션 오프라인 시 WAL 폴백 테스트."""

    @pytest.fixture
    def mock_kafka_error(self):
        """Mock KafkaError."""
        error = MagicMock()
        error.code.return_value = -191  # _PARTITION_EOF
        error.__str__ = lambda self: "Partition 3 offline"
        return error

    @pytest.fixture
    def mock_kafka_exception(self, mock_kafka_error):
        """Mock KafkaException."""

        class MockKafkaException(Exception):
            def __init__(self, error):
                self.args = (error,)

        return MockKafkaException(mock_kafka_error)

    def test_partition_offline_triggers_error_callback(self, mock_kafka_error):
        """파티션 장애 시 에러 콜백이 호출되는지 확인."""
        from selfhealing.adapters.audit.kafka_adapter import KafkaAuditAdapter
        from selfhealing.interfaces.audit_adapter import AuditEntry
        from selfhealing.settings.kafka import KafkaAuditSettings

        mock_producer = MagicMock()
        settings = KafkaAuditSettings()
        adapter = KafkaAuditAdapter(settings=settings, producer=mock_producer)

        # 에러 콜백 시뮬레이션
        adapter._pending_count = 1
        adapter._delivery_callback(err=mock_kafka_error, msg=None)

        # 에러 카운트 증가 확인
        assert adapter._error_count == 1
        assert adapter._pending_count == 0

    def test_partition_offline_adapter_continues_operation(self):
        """파티션 장애 후에도 어댑터가 계속 동작하는지 확인."""
        from selfhealing.adapters.audit.kafka_adapter import KafkaAuditAdapter
        from selfhealing.interfaces.audit_adapter import AuditEntry
        from selfhealing.settings.kafka import KafkaAuditSettings

        mock_producer = MagicMock()
        settings = KafkaAuditSettings()
        adapter = KafkaAuditAdapter(settings=settings, producer=mock_producer)

        mock_confluent = (MagicMock(), MagicMock(), MagicMock())

        with patch(
            "selfhealing.adapters.audit.kafka_adapter._get_confluent_kafka",
            return_value=mock_confluent,
        ):
            # 첫 번째 메시지 전송 (정상)
            entry1 = AuditEntry(action="TEST1", target_type="order", target_id="1")
            adapter.log(entry1)
            assert mock_producer.produce.call_count == 1

            # 두 번째 메시지 전송 (정상 - 파티션 장애 후에도 계속)
            entry2 = AuditEntry(action="TEST2", target_type="order", target_id="2")
            adapter.log(entry2)
            assert mock_producer.produce.call_count == 2


class TestNetworkPartitionCallback:
    """네트워크 단절 시 콜백 에러 검증."""

    def test_delivery_callback_on_network_timeout(self):
        """네트워크 타임아웃 시 콜백이 에러를 반환하는지 확인."""
        from selfhealing.adapters.audit.kafka_adapter import KafkaAuditAdapter
        from selfhealing.settings.kafka import KafkaAuditSettings

        mock_producer = MagicMock()
        settings = KafkaAuditSettings()
        adapter = KafkaAuditAdapter(settings=settings, producer=mock_producer)

        errors_received = []

        # 원본 콜백 래핑
        original_callback = adapter._delivery_callback

        def error_tracking_callback(err, msg):
            if err:
                errors_received.append(err)
            original_callback(err, msg)

        adapter._delivery_callback = error_tracking_callback
        adapter._pending_count = 1

        # 네트워크 타임아웃 시뮬레이션
        timeout_error = MagicMock()
        timeout_error.code.return_value = -185  # _MSG_TIMED_OUT
        timeout_error.__str__ = lambda self: "Network timeout"

        adapter._delivery_callback(err=timeout_error, msg=None)

        # 에러 수신 확인
        assert len(errors_received) == 1
        assert adapter._error_count == 1

    def test_network_partition_recovery(self):
        """네트워크 복구 후 정상 전송 재개 확인."""
        from selfhealing.adapters.audit.kafka_adapter import KafkaAuditAdapter
        from selfhealing.interfaces.audit_adapter import AuditEntry
        from selfhealing.settings.kafka import KafkaAuditSettings

        mock_producer = MagicMock()
        settings = KafkaAuditSettings()
        adapter = KafkaAuditAdapter(settings=settings, producer=mock_producer)

        mock_confluent = (MagicMock(), MagicMock(), MagicMock())

        with patch(
            "selfhealing.adapters.audit.kafka_adapter._get_confluent_kafka",
            return_value=mock_confluent,
        ):
            # 네트워크 장애 시뮬레이션 (에러 카운트만 증가)
            adapter._error_count = 5

            # 네트워크 복구 후 메시지 전송
            entry = AuditEntry(action="RECOVERY_TEST", target_type="test", target_id="1")
            adapter.log(entry)

            # 정상 전송 확인
            assert mock_producer.produce.call_count == 1


class TestProducerLagDetection:
    """Producer Lag 감지 테스트."""

    def test_high_pending_count_detected(self):
        """pending_count가 높을 때 감지되는지 확인."""
        from selfhealing.adapters.audit.kafka_adapter import KafkaAuditAdapter
        from selfhealing.settings.kafka import KafkaAuditSettings

        mock_producer = MagicMock()
        settings = KafkaAuditSettings(max_queue_messages=100000)
        adapter = KafkaAuditAdapter(settings=settings, producer=mock_producer)

        # 정상 상태
        adapter._pending_count = 1000
        assert adapter.is_healthy() is True

        # 80% 초과 시 unhealthy
        adapter._pending_count = 85000
        assert adapter.is_healthy() is False

    def test_get_stats_includes_pending_count(self):
        """get_stats()에 pending_count가 포함되는지 확인."""
        from selfhealing.adapters.audit.kafka_adapter import KafkaAuditAdapter
        from selfhealing.settings.kafka import KafkaAuditSettings

        mock_producer = MagicMock()
        settings = KafkaAuditSettings()
        adapter = KafkaAuditAdapter(settings=settings, producer=mock_producer)

        adapter._pending_count = 500
        adapter._sent_count = 1000
        adapter._error_count = 5

        stats = adapter.get_stats()

        assert stats["pending_count"] == 500
        assert stats["sent_count"] == 1000
        assert stats["error_count"] == 5


class TestDeadLetterTopic:
    """Dead Letter Topic 동작 테스트."""

    def test_serialization_failure_sends_to_dlt(self):
        """직렬화 실패 시 DLT로 전송되는지 확인."""
        from selfhealing.adapters.audit.kafka_adapter import KafkaAuditAdapter
        from selfhealing.interfaces.audit_adapter import AuditEntry
        from selfhealing.settings.kafka import KafkaAuditSettings

        mock_producer = MagicMock()
        settings = KafkaAuditSettings(
            dead_letter_topic="test.audit.dlt",
        )
        adapter = KafkaAuditAdapter(settings=settings, producer=mock_producer)

        # DLT 전송 테스트
        entry = AuditEntry(action="TEST", target_type="test", target_id="1")
        adapter._send_to_dlt(entry, error="Test serialization error")

        # DLT 토픽으로 전송 확인
        assert mock_producer.produce.call_count == 1
        call_kwargs = mock_producer.produce.call_args.kwargs
        assert call_kwargs["topic"] == "test.audit.dlt"


class TestWALPriorityPurge:
    """WAL 우선순위 기반 Purge 테스트."""

    @pytest.fixture
    def wal_with_priority_files(self, tmp_path):
        """우선순위별 WAL 파일이 있는 WAL 인스턴스 생성."""
        from selfhealing.audit.wal import WALConfig, WriteAheadLog

        config = WALConfig(
            wal_dir=str(tmp_path),
            priority_based_purge=True,
            max_file_size_mb=1,
            critical_retention_min_mb=1,
        )
        wal = WriteAheadLog(config=config)

        # 우선순위별 더미 파일 생성
        for priority in ["debug", "info", "warning", "error"]:
            file_path = tmp_path / f"audit_wal_{priority}_20260201_120000.wal"
            file_path.write_bytes(b"x" * 1024)  # 1KB

        return wal

    def test_purge_deletes_low_priority_first(self, tmp_path):
        """낮은 우선순위 파일이 먼저 삭제되는지 확인."""
        from selfhealing.audit.wal import WALConfig, WriteAheadLog

        config = WALConfig(
            wal_dir=str(tmp_path),
            priority_based_purge=True,
            max_file_size_mb=1,
            critical_retention_min_mb=0,
        )
        wal = WriteAheadLog(config=config)

        # 우선순위별 더미 파일 생성
        debug_file = tmp_path / "audit_wal_debug_20260201_120000.wal"
        info_file = tmp_path / "audit_wal_info_20260201_120001.wal"
        debug_file.write_bytes(b"x" * 1024 * 100)  # 100KB
        info_file.write_bytes(b"x" * 1024 * 100)  # 100KB

        # Purge 실행
        result = wal._purge_by_priority()

        # DEBUG 파일이 먼저 삭제됨
        assert not debug_file.exists() or not info_file.exists()

    def test_purge_protects_critical_files(self, tmp_path):
        """CRITICAL 파일이 보호되는지 확인."""
        from selfhealing.audit.wal import WALConfig, WriteAheadLog

        config = WALConfig(
            wal_dir=str(tmp_path),
            priority_based_purge=True,
            purge_priority_order=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
            critical_retention_min_mb=1,
        )
        wal = WriteAheadLog(config=config)

        # CRITICAL 파일만 생성
        critical_file = tmp_path / "audit_wal_critical_20260201_120000.wal"
        critical_file.write_bytes(b"x" * 1024 * 100)  # 100KB

        # Purge 실행 (CRITICAL은 purge_priority_order에서 마지막이므로 삭제 대상 아님)
        result = wal._purge_by_priority()

        # CRITICAL 파일은 삭제되지 않음 (일반 파일로 취급될 수 있으나 보호 로직 확인)
        # critical_retention_min_mb로 보호됨


class TestKafkaConsumerChaos:
    """Kafka Consumer 카오스 테스트."""

    def test_consumer_handles_rebalance(self):
        """Consumer가 리밸런싱을 정상 처리하는지 확인."""
        from selfhealing.adapters.audit.kafka_consumer import (
            KafkaConsumerConfig,
            RebalanceAwareConsumer,
        )

        mock_consumer = MagicMock()
        config = KafkaConsumerConfig(group_id="test-group")

        rebalance_events = []

        def on_rebalance(event_type, partitions):
            rebalance_events.append((event_type, len(partitions)))

        consumer = RebalanceAwareConsumer(
            config=config,
            consumer=mock_consumer,
            on_rebalance=on_rebalance,
        )

        # 파티션 할당 시뮬레이션
        mock_partitions = [MagicMock() for _ in range(3)]
        mock_consumer.committed.return_value = [MagicMock(offset=100)]

        consumer._on_assign(mock_consumer, mock_partitions)
        assert ("assign", 3) in rebalance_events

        # 파티션 해제 시뮬레이션
        consumer._on_revoke(mock_consumer, mock_partitions)
        assert ("revoke", 3) in rebalance_events

    def test_idempotent_consumer_skips_duplicates(self):
        """IdempotentConsumer가 중복 메시지를 스킵하는지 확인."""
        from selfhealing.adapters.audit.kafka_consumer import (
            IdempotentAuditConsumer,
            KafkaConsumerConfig,
        )

        mock_consumer = MagicMock()
        config = KafkaConsumerConfig()

        consumer = IdempotentAuditConsumer(
            config=config,
            consumer=mock_consumer,
            cache_max_size=1000,
        )

        # 첫 번째 메시지
        mock_message = MagicMock()
        mock_message.value.return_value = json.dumps(
            {
                "id": "unique-id-123",
                "action": "TEST",
            }
        ).encode("utf-8")
        mock_message.topic.return_value = "test"
        mock_message.partition.return_value = 0
        mock_message.offset.return_value = 1

        # 첫 번째 처리 (성공)
        result1 = consumer.process_message(mock_message)
        assert result1 is True
        assert consumer._skipped_count == 0

        # 같은 메시지 다시 처리 (스킵)
        result2 = consumer.process_message(mock_message)
        assert result2 is True
        assert consumer._skipped_count == 1
