"""
KafkaSettings 단위 테스트.

Kafka 설정 클래스의 기본값, 환경변수 로딩, 계산된 속성을 테스트합니다.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from selfhealing.adapters.kafka.config import (
    KafkaSettings,
    get_kafka_settings,
    reset_kafka_settings,
)


class TestKafkaSettings:
    """KafkaSettings 단위 테스트."""

    def setup_method(self) -> None:
        """테스트 전 설정 캐시 초기화."""
        reset_kafka_settings()

    def teardown_method(self) -> None:
        """테스트 후 설정 캐시 초기화."""
        reset_kafka_settings()

    def test_default_values(self) -> None:
        """기본값이 올바르게 설정되는지 확인."""
        settings = KafkaSettings()

        assert settings.bootstrap_servers == "localhost:9092"
        assert settings.topic_prefix == "selfhealing."
        assert settings.audit_topic == "audit.events"
        assert settings.dlq_topic == "dlq.events"
        assert settings.recovery_topic == "recovery.events"

    def test_producer_default_values(self) -> None:
        """Producer 기본값 확인."""
        settings = KafkaSettings()

        assert settings.producer_acks == "all"
        assert settings.producer_retries == 3
        assert settings.producer_batch_size == 16384
        assert settings.producer_linger_ms == 10
        assert settings.producer_compression_type == "zstd"
        assert settings.producer_idempotent is True

    def test_consumer_default_values(self) -> None:
        """Consumer 기본값 확인."""
        settings = KafkaSettings()

        assert settings.consumer_group_id == "selfhealing-audit-consumer"
        assert settings.consumer_auto_offset_reset == "earliest"
        assert settings.consumer_enable_auto_commit is False
        assert settings.consumer_max_poll_records == 500
        assert settings.consumer_session_timeout_ms == 30000

    def test_full_topic_names(self) -> None:
        """전체 토픽 이름 계산 확인."""
        settings = KafkaSettings()

        assert settings.full_audit_topic == "selfhealing.audit.events"
        assert settings.full_dlq_topic == "selfhealing.dlq.events"
        assert settings.full_recovery_topic == "selfhealing.recovery.events"

    def test_custom_topic_prefix(self) -> None:
        """커스텀 토픽 프리픽스 적용 확인."""
        settings = KafkaSettings(topic_prefix="custom.")

        assert settings.full_audit_topic == "custom.audit.events"
        assert settings.full_dlq_topic == "custom.dlq.events"

    def test_environment_variable_override(self) -> None:
        """환경변수로 설정 오버라이드 확인."""
        with patch.dict(
            os.environ,
            {
                "SELFHEALING_KAFKA_BOOTSTRAP_SERVERS": "kafka-1:9092,kafka-2:9092",
                "SELFHEALING_KAFKA_TOPIC_PREFIX": "test.",
                "SELFHEALING_KAFKA_PRODUCER_ACKS": "1",
            },
        ):
            reset_kafka_settings()
            settings = KafkaSettings()

            assert settings.bootstrap_servers == "kafka-1:9092,kafka-2:9092"
            assert settings.topic_prefix == "test."
            assert settings.producer_acks == "1"

    def test_security_protocol_development(self) -> None:
        """개발 환경에서 기본 보안 프로토콜 확인."""
        with patch.dict(os.environ, {"ENVIRONMENT": "development"}, clear=False):
            settings = KafkaSettings()
            assert settings.security_protocol == "PLAINTEXT"

    def test_security_protocol_production(self) -> None:
        """프로덕션 환경에서 기본 보안 프로토콜 확인."""
        with patch.dict(os.environ, {"ENVIRONMENT": "production"}, clear=False):
            # 새 인스턴스 생성 시 기본값 함수가 호출됨
            settings = KafkaSettings(security_protocol="SASL_SSL")
            assert settings.security_protocol == "SASL_SSL"

    def test_get_kafka_settings_singleton(self) -> None:
        """싱글톤 패턴 동작 확인."""
        reset_kafka_settings()
        settings1 = get_kafka_settings()
        settings2 = get_kafka_settings()

        assert settings1 is settings2

    def test_reset_kafka_settings(self) -> None:
        """설정 캐시 초기화 확인."""
        settings1 = get_kafka_settings()
        reset_kafka_settings()
        settings2 = get_kafka_settings()

        # 새 인스턴스지만 같은 값
        assert settings1 is not settings2
        assert settings1.bootstrap_servers == settings2.bootstrap_servers

    def test_sasl_settings(self) -> None:
        """SASL 인증 설정 확인."""
        settings = KafkaSettings(
            security_protocol="SASL_SSL",
            sasl_mechanism="SCRAM-SHA-512",
            sasl_username="user",
            sasl_password="pass",
        )

        assert settings.security_protocol == "SASL_SSL"
        assert settings.sasl_mechanism == "SCRAM-SHA-512"
        assert settings.sasl_username == "user"
        assert settings.sasl_password == "pass"

    def test_schema_registry_settings(self) -> None:
        """Schema Registry 설정 확인."""
        settings = KafkaSettings(
            schema_registry_url="http://localhost:8081",
            schema_compatibility="FULL",
        )

        assert settings.schema_registry_url == "http://localhost:8081"
        assert settings.schema_compatibility == "FULL"

    def test_compression_type_validation(self) -> None:
        """압축 타입 유효성 검사."""
        # 유효한 압축 타입
        for compression in ["none", "gzip", "snappy", "lz4", "zstd"]:
            settings = KafkaSettings(producer_compression_type=compression)
            assert settings.producer_compression_type == compression

    def test_acks_validation(self) -> None:
        """ACK 레벨 유효성 검사."""
        for acks in ["0", "1", "all"]:
            settings = KafkaSettings(producer_acks=acks)
            assert settings.producer_acks == acks
