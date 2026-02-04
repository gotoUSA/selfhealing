"""
스키마 관련 단위 테스트.

JSON 유효성 검사 및 직렬화/역직렬화를 테스트합니다.
Schema Registry 관련 테스트는 선택적입니다.
"""

from __future__ import annotations

import json

import pytest

from selfhealing.adapters.kafka.schemas import (
    AUDIT_EVENT_SCHEMA,
    DLQ_EVENT_SCHEMA,
    AuditEventSchemaRegistry,
    SchemaRegistryNotConfiguredError,
    deserialize_event_json,
    serialize_event_json,
    validate_audit_event,
)
from selfhealing.adapters.kafka.config import KafkaSettings


class TestValidateAuditEvent:
    """validate_audit_event 단위 테스트."""

    def test_valid_event(self) -> None:
        """유효한 이벤트."""
        event = {
            "event_id": "evt-123",
            "event_type": "dlq_store",
            "timestamp": "2024-01-01T00:00:00Z",
        }

        errors = validate_audit_event(event)

        assert len(errors) == 0

    def test_missing_required_field(self) -> None:
        """필수 필드 누락."""
        event = {
            "event_id": "evt-123",
            # event_type 누락
            "timestamp": "2024-01-01T00:00:00Z",
        }

        errors = validate_audit_event(event)

        assert len(errors) == 1
        assert "event_type" in errors[0]

    def test_missing_multiple_fields(self) -> None:
        """여러 필수 필드 누락."""
        event = {}

        errors = validate_audit_event(event)

        assert len(errors) == 3
        assert any("event_id" in e for e in errors)
        assert any("event_type" in e for e in errors)
        assert any("timestamp" in e for e in errors)

    def test_invalid_event_id_type(self) -> None:
        """잘못된 event_id 타입."""
        event = {
            "event_id": 123,  # 숫자여야 하지만 문자열이어야 함
            "event_type": "test",
            "timestamp": "2024-01-01T00:00:00Z",
        }

        errors = validate_audit_event(event)

        assert len(errors) == 1
        assert "event_id" in errors[0]

    def test_invalid_schema_version_type(self) -> None:
        """잘못된 schema_version 타입."""
        event = {
            "event_id": "evt-123",
            "event_type": "test",
            "timestamp": "2024-01-01T00:00:00Z",
            "schema_version": "1",  # 문자열이지만 정수여야 함
        }

        errors = validate_audit_event(event)

        assert len(errors) == 1
        assert "schema_version" in errors[0]

    def test_valid_with_optional_fields(self) -> None:
        """선택적 필드 포함 유효 이벤트."""
        event = {
            "event_id": "evt-123",
            "event_type": "dlq_store",
            "timestamp": "2024-01-01T00:00:00Z",
            "namespace": "order",
            "domain": "payment",
            "actor": "user-456",
            "data": {"key": "value"},
            "schema_version": 1,
        }

        errors = validate_audit_event(event)

        assert len(errors) == 0


class TestSerializeDeserialize:
    """직렬화/역직렬화 테스트."""

    def test_serialize_event_json(self) -> None:
        """JSON 직렬화."""
        event = {
            "event_id": "evt-123",
            "event_type": "test",
            "data": {"nested": {"key": "value"}},
        }

        serialized = serialize_event_json(event)

        assert isinstance(serialized, bytes)
        deserialized = json.loads(serialized.decode("utf-8"))
        assert deserialized["event_id"] == "evt-123"
        assert deserialized["data"]["nested"]["key"] == "value"

    def test_deserialize_event_json(self) -> None:
        """JSON 역직렬화."""
        data = b'{"event_id": "evt-456", "event_type": "test"}'

        event = deserialize_event_json(data)

        assert event["event_id"] == "evt-456"
        assert event["event_type"] == "test"

    def test_roundtrip(self) -> None:
        """직렬화-역직렬화 왕복."""
        original = {
            "event_id": "evt-789",
            "event_type": "dlq_store",
            "timestamp": "2024-01-01T00:00:00Z",
            "data": {"key": "한글 테스트"},  # 유니코드 테스트
        }

        serialized = serialize_event_json(original)
        restored = deserialize_event_json(serialized)

        assert restored == original

    def test_serialize_with_datetime(self) -> None:
        """datetime 객체 직렬화."""
        from datetime import datetime, timezone

        event = {
            "event_id": "evt-123",
            "timestamp": datetime(2024, 1, 1, tzinfo=timezone.utc),
        }

        # default=str 옵션으로 datetime이 문자열로 변환됨
        serialized = serialize_event_json(event)
        deserialized = json.loads(serialized.decode("utf-8"))

        assert "2024-01-01" in deserialized["timestamp"]


class TestSchemaStrings:
    """스키마 문자열 테스트."""

    def test_audit_event_schema_is_valid_json(self) -> None:
        """AUDIT_EVENT_SCHEMA가 유효한 JSON."""
        schema = json.loads(AUDIT_EVENT_SCHEMA)

        assert schema["type"] == "record"
        assert schema["name"] == "AuditEvent"
        assert schema["namespace"] == "selfhealing.audit"
        assert len(schema["fields"]) > 0

    def test_dlq_event_schema_is_valid_json(self) -> None:
        """DLQ_EVENT_SCHEMA가 유효한 JSON."""
        schema = json.loads(DLQ_EVENT_SCHEMA)

        assert schema["type"] == "record"
        assert schema["name"] == "DLQEvent"
        assert schema["namespace"] == "selfhealing.dlq"
        assert len(schema["fields"]) > 0

    def test_audit_event_schema_required_fields(self) -> None:
        """AUDIT_EVENT_SCHEMA 필수 필드 확인."""
        schema = json.loads(AUDIT_EVENT_SCHEMA)
        field_names = [f["name"] for f in schema["fields"]]

        assert "event_id" in field_names
        assert "event_type" in field_names
        assert "timestamp" in field_names

    def test_dlq_event_schema_required_fields(self) -> None:
        """DLQ_EVENT_SCHEMA 필수 필드 확인."""
        schema = json.loads(DLQ_EVENT_SCHEMA)
        field_names = [f["name"] for f in schema["fields"]]

        assert "dlq_id" in field_names
        assert "original_event" in field_names
        assert "error_message" in field_names


class TestAuditEventSchemaRegistry:
    """AuditEventSchemaRegistry 단위 테스트."""

    def test_not_available_without_url(self) -> None:
        """URL 없으면 사용 불가."""
        settings = KafkaSettings(schema_registry_url=None)
        registry = AuditEventSchemaRegistry(settings)

        assert registry.is_available() is False

    def test_ensure_client_raises_without_url(self) -> None:
        """클라이언트 없으면 예외 발생."""
        settings = KafkaSettings(schema_registry_url=None)
        registry = AuditEventSchemaRegistry(settings)

        with pytest.raises(SchemaRegistryNotConfiguredError):
            registry._ensure_client()
