"""
Audit 이벤트 스키마 관리.

Confluent Schema Registry를 통한 스키마 버전 관리 및 호환성 검사를 지원합니다.

핵심 특징:
- Avro 스키마 직렬화/역직렬화
- 스키마 호환성 검사 (BACKWARD, FORWARD, FULL)
- 스키마 자동 등록

Usage:
    from selfhealing.adapters.kafka.schemas import AuditEventSchemaRegistry

    registry = AuditEventSchemaRegistry(settings)

    # Avro 직렬화
    serializer = registry.get_serializer(AUDIT_EVENT_SCHEMA)
    serialized = serializer(event_data)

    # 호환성 검사
    is_compatible = registry.check_compatibility("audit-events-value", new_schema)
"""

from __future__ import annotations

import json
import structlog
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from selfhealing.adapters.kafka.config import KafkaSettings

logger = structlog.get_logger()


# =============================================================================
# Audit 이벤트 스키마 정의
# =============================================================================

AUDIT_EVENT_SCHEMA = """
{
    "type": "record",
    "name": "AuditEvent",
    "namespace": "selfhealing.audit",
    "fields": [
        {
            "name": "event_id",
            "type": "string",
            "doc": "고유 이벤트 ID"
        },
        {
            "name": "event_type",
            "type": "string",
            "doc": "이벤트 유형 (dlq_store, cb_state_change 등)"
        },
        {
            "name": "timestamp",
            "type": "string",
            "doc": "ISO 8601 형식 타임스탬프"
        },
        {
            "name": "namespace",
            "type": "string",
            "default": "global",
            "doc": "네임스페이스"
        },
        {
            "name": "domain",
            "type": ["null", "string"],
            "default": null,
            "doc": "도메인"
        },
        {
            "name": "actor",
            "type": ["null", "string"],
            "default": null,
            "doc": "액터 ID"
        },
        {
            "name": "data",
            "type": {
                "type": "map",
                "values": "string"
            },
            "default": {},
            "doc": "이벤트 페이로드"
        },
        {
            "name": "schema_version",
            "type": "int",
            "default": 1,
            "doc": "스키마 버전"
        }
    ]
}
"""

DLQ_EVENT_SCHEMA = """
{
    "type": "record",
    "name": "DLQEvent",
    "namespace": "selfhealing.dlq",
    "fields": [
        {
            "name": "dlq_id",
            "type": "string",
            "doc": "DLQ 엔트리 ID"
        },
        {
            "name": "original_event",
            "type": "string",
            "doc": "원본 이벤트 (JSON 문자열)"
        },
        {
            "name": "error_message",
            "type": "string",
            "doc": "오류 메시지"
        },
        {
            "name": "error_type",
            "type": "string",
            "doc": "오류 유형"
        },
        {
            "name": "retry_count",
            "type": "int",
            "default": 0,
            "doc": "재시도 횟수"
        },
        {
            "name": "created_at",
            "type": "string",
            "doc": "생성 시각"
        },
        {
            "name": "namespace",
            "type": "string",
            "default": "global",
            "doc": "네임스페이스"
        },
        {
            "name": "schema_version",
            "type": "int",
            "default": 1,
            "doc": "스키마 버전"
        }
    ]
}
"""


class SchemaRegistryNotConfiguredError(Exception):
    """Schema Registry URL이 설정되지 않았을 때 발생."""

    pass


class SchemaCompatibilityError(Exception):
    """스키마 호환성 검사 실패 시 발생."""

    pass


class AuditEventSchemaRegistry:
    """
    Audit 이벤트 스키마 레지스트리 통합.

    Confluent Schema Registry와 연동하여 스키마 버전 관리 및
    호환성 검사를 수행합니다.
    """

    def __init__(self, settings: KafkaSettings):
        """
        AuditEventSchemaRegistry 초기화.

        Args:
            settings: Kafka 설정 (schema_registry_url 필요)

        Raises:
            SchemaRegistryNotConfiguredError: URL 미설정 시
        """
        self._settings = settings
        self._client = None
        self._serializers: dict[str, Any] = {}
        self._deserializers: dict[str, Any] = {}

        if settings.schema_registry_url:
            self._init_client()

    def _init_client(self) -> None:
        """Schema Registry 클라이언트 초기화."""
        try:
            from confluent_kafka.schema_registry import SchemaRegistryClient

            self._client = SchemaRegistryClient({"url": self._settings.schema_registry_url})
            logger.info(
                "schema_registry.연결됨",
                self=self._settings.schema_registry_url,
            )
        except ImportError:
            logger.warning(
                "[SchemaRegistry] confluent-kafka[schema-registry] 패키지가 "
                "설치되지 않았습니다. 스키마 레지스트리 기능이 비활성화됩니다."
            )
        except Exception as e:
            logger.error(
                "schema_registry.초기화_실패",
                error=e,
            )
            raise

    def is_available(self) -> bool:
        """Schema Registry 사용 가능 여부."""
        return self._client is not None

    def _ensure_client(self) -> Any:
        """클라이언트 사용 가능 여부 확인."""
        if not self._client:
            raise SchemaRegistryNotConfiguredError(
                "schema_registry_url이 설정되지 않았거나 " "confluent-kafka[schema-registry]가 설치되지 않았습니다."
            )
        return self._client

    def get_serializer(
        self,
        schema_str: str,
        auto_register: bool = True,
    ) -> Any:
        """
        Avro 직렬화기 반환.

        Args:
            schema_str: Avro 스키마 문자열 (JSON)
            auto_register: 스키마 자동 등록 여부

        Returns:
            AvroSerializer 인스턴스
        """
        client = self._ensure_client()

        # 캐시 확인
        cache_key = hash(schema_str)
        if cache_key in self._serializers:
            return self._serializers[cache_key]

        try:
            from confluent_kafka.schema_registry import Schema
            from confluent_kafka.schema_registry.avro import AvroSerializer

            schema = Schema(schema_str, "AVRO")
            serializer = AvroSerializer(
                client,
                schema,
                conf={"auto.register.schemas": auto_register},
            )
            self._serializers[cache_key] = serializer
            return serializer

        except Exception as e:
            logger.error(
                "schema_registry.직렬화기_생성_실패",
                error=e,
            )
            raise

    def get_deserializer(self, schema_str: str | None = None) -> Any:
        """
        Avro 역직렬화기 반환.

        Args:
            schema_str: Avro 스키마 문자열 (None이면 레지스트리에서 조회)

        Returns:
            AvroDeserializer 인스턴스
        """
        client = self._ensure_client()

        cache_key = hash(schema_str) if schema_str else "auto"
        if cache_key in self._deserializers:
            return self._deserializers[cache_key]

        try:
            from confluent_kafka.schema_registry.avro import AvroDeserializer

            if schema_str:
                from confluent_kafka.schema_registry import Schema

                schema = Schema(schema_str, "AVRO")
                deserializer = AvroDeserializer(client, schema)
            else:
                # 스키마 자동 조회 모드
                deserializer = AvroDeserializer(client)

            self._deserializers[cache_key] = deserializer
            return deserializer

        except Exception as e:
            logger.error(
                "schema_registry.역직렬화기_생성_실패",
                error=e,
            )
            raise

    def check_compatibility(
        self,
        subject: str,
        schema_str: str,
    ) -> bool:
        """
        스키마 호환성 검사.

        Args:
            subject: 스키마 서브젝트 (예: "audit-events-value")
            schema_str: 검사할 스키마 문자열

        Returns:
            호환성 여부
        """
        client = self._ensure_client()

        try:
            from confluent_kafka.schema_registry import Schema

            schema = Schema(schema_str, "AVRO")
            return client.test_compatibility(subject, schema)

        except Exception as e:
            logger.error(
                "schema_registry.호환성_검사_실패",
                error=e,
            )
            return False

    def register_schema(
        self,
        subject: str,
        schema_str: str,
    ) -> int:
        """
        스키마 등록.

        Args:
            subject: 스키마 서브젝트
            schema_str: 스키마 문자열

        Returns:
            등록된 스키마 ID
        """
        client = self._ensure_client()

        try:
            from confluent_kafka.schema_registry import Schema

            schema = Schema(schema_str, "AVRO")
            schema_id = client.register_schema(subject, schema)
            logger.info(
                "schema_registry.스키마_등록됨_id",
                subject=subject,
                schema_id=schema_id,
            )
            return schema_id

        except Exception as e:
            logger.error(
                "schema_registry.스키마_등록_실패",
                error=e,
            )
            raise

    def get_latest_version(self, subject: str) -> dict[str, Any] | None:
        """
        최신 스키마 버전 조회.

        Args:
            subject: 스키마 서브젝트

        Returns:
            스키마 정보 (version, schema_id, schema) 또는 None
        """
        client = self._ensure_client()

        try:
            registered = client.get_latest_version(subject)
            return {
                "version": registered.version,
                "schema_id": registered.schema_id,
                "schema": registered.schema.schema_str,
            }
        except Exception as e:
            logger.warning(
                "schema_registry.스키마_조회_실패",
                subject=subject,
                error=e,
            )
            return None


# =============================================================================
# JSON 스키마 유틸리티 (Schema Registry 없이 사용)
# =============================================================================


def validate_audit_event(event: dict[str, Any]) -> list[str]:
    """
    Audit 이벤트 유효성 검사 (JSON 기반).

    Schema Registry 없이 기본적인 필드 검증을 수행합니다.

    Args:
        event: 검사할 이벤트

    Returns:
        오류 메시지 목록 (빈 리스트면 유효)
    """
    errors = []

    # 필수 필드 검사
    required_fields = ["event_id", "event_type", "timestamp"]
    for field in required_fields:
        if field not in event:
            errors.append(f"필수 필드 누락: {field}")

    # 타입 검사
    if "event_id" in event and not isinstance(event["event_id"], str):
        errors.append("event_id는 문자열이어야 합니다")

    if "event_type" in event and not isinstance(event["event_type"], str):
        errors.append("event_type은 문자열이어야 합니다")

    if "timestamp" in event and not isinstance(event["timestamp"], str):
        errors.append("timestamp는 문자열이어야 합니다")

    if "schema_version" in event and not isinstance(event["schema_version"], int):
        errors.append("schema_version은 정수여야 합니다")

    return errors


def serialize_event_json(event: dict[str, Any]) -> bytes:
    """
    이벤트를 JSON 바이트로 직렬화.

    Schema Registry 없이 사용하는 간단한 직렬화.

    Args:
        event: 직렬화할 이벤트

    Returns:
        JSON 인코딩된 바이트
    """
    return json.dumps(event, default=str, ensure_ascii=False).encode("utf-8")


def deserialize_event_json(data: bytes) -> dict[str, Any]:
    """
    JSON 바이트를 이벤트로 역직렬화.

    Args:
        data: JSON 인코딩된 바이트

    Returns:
        이벤트 딕셔너리
    """
    return json.loads(data.decode("utf-8"))
