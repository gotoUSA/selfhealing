"""
Kafka Audit Settings.

고처리량 감사 이벤트를 Kafka로 스트리밍하기 위한 설정.
confluent-kafka (librdkafka 기반) Producer 설정을 제공합니다.

주요 기능:
- Idempotent Producer (중복 전송 방지)
- 배치 전송 (linger_ms, batch_size)
- 압축 지원 (snappy, lz4, zstd)
- Hot Partition 방지 (솔트 파티셔닝)
- TLS/SASL 인증

환경 변수 접두사: SELFHEALING_KAFKA_AUDIT_

Usage:
    from selfhealing.settings.kafka import KafkaAuditSettings

    settings = KafkaAuditSettings()
    print(settings.bootstrap_servers)
    print(settings.topic)
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SerializationFormat(str, Enum):
    """직렬화 포맷."""

    JSON = "json"
    AVRO = "avro"
    PROTOBUF = "protobuf"


class KafkaAuditSettings(BaseSettings):
    """
    Kafka 감사 로그 설정.

    환경 변수 예시:
        SELFHEALING_KAFKA_AUDIT_BOOTSTRAP_SERVERS=kafka1:9092,kafka2:9092
        SELFHEALING_KAFKA_AUDIT_TOPIC=selfhealing.audit.events
        SELFHEALING_KAFKA_AUDIT_ENABLE_IDEMPOTENCE=true
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_KAFKA_AUDIT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # Connection
    # ==========================================================================
    bootstrap_servers: list[str] = Field(
        default=["localhost:9092"],
        description="Kafka 브로커 주소 목록",
    )
    topic: str = Field(
        default="selfhealing.audit.events",
        description="감사 이벤트 토픽명",
    )
    dead_letter_topic: str = Field(
        default="selfhealing.audit.events.dlt",
        description="Dead Letter Topic (직렬화 실패 이벤트 보관)",
    )

    # ==========================================================================
    # Idempotent Producer (Exactly-once)
    # ==========================================================================
    enable_idempotence: bool = Field(
        default=True,
        description="Idempotent Producer 활성화 (중복 전송 방지)",
    )

    # ==========================================================================
    # Batching
    # ==========================================================================
    batch_size_bytes: int = Field(
        default=16384,  # 16KB
        ge=1024,
        le=1048576,  # 1MB
        description="배치 크기 (bytes)",
    )
    linger_ms: int = Field(
        default=10,  # 10ms
        ge=0,
        le=1000,
        description="배치 대기 시간 (ms). P50 ~20ms 예상",
    )
    latency_budget_p99_ms: int = Field(
        default=50,
        description="Producer 지연 허용치 P99 (linger + ack + network)",
    )
    latency_alert_threshold_ms: int = Field(
        default=100,
        description="지연 알림 임계값. P99 > 100ms 시 경고",
    )

    # ==========================================================================
    # Hot Partition 방지
    # ==========================================================================
    partition_salt_enabled: bool = Field(
        default=True,
        description="Hot Partition 방지용 솔트 활성화",
    )
    partition_salt_range: int = Field(
        default=100,
        ge=10,
        le=1000,
        description="솔트 범위 (파티션 분산 정도)",
    )

    # ==========================================================================
    # Compression
    # ==========================================================================
    compression_type: str = Field(
        default="zstd",
        description="압축 알고리즘: zstd(권장), snappy, lz4, gzip, none",
    )
    compression_level: int = Field(
        default=3,
        ge=1,
        le=22,
        description="Zstd 압축 레벨 (1-22, 높을수록 압축률↑ CPU↑)",
    )

    # ==========================================================================
    # Reliability
    # ==========================================================================
    acks: str = Field(
        default="all",
        description="ACK 레벨: 0(없음), 1(리더), all(모든 레플리카)",
    )
    retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="재시도 횟수",
    )
    retry_backoff_ms: int = Field(
        default=100,
        ge=10,
        le=5000,
        description="재시도 대기 시간 (ms)",
    )
    message_timeout_ms: int = Field(
        default=30000,  # 30초
        ge=1000,
        le=120000,
        description="메시지 전송 타임아웃 (ms)",
    )

    # ==========================================================================
    # Buffer (Producer Lag 모니터링용)
    # ==========================================================================
    buffer_memory: int = Field(
        default=33554432,  # 32MB
        ge=1048576,  # 1MB
        le=1073741824,  # 1GB
        description="프로듀서 버퍼 메모리 (bytes)",
    )
    max_queue_messages: int = Field(
        default=100000,
        ge=1000,
        le=10000000,
        description="프로듀서 큐 최대 메시지 수",
    )

    # ==========================================================================
    # Security: TLS/SSL
    # ==========================================================================
    security_protocol: str = Field(
        default="PLAINTEXT",
        description="보안 프로토콜: PLAINTEXT, SSL, SASL_PLAINTEXT, SASL_SSL",
    )
    ssl_cafile: str | None = Field(
        default=None,
        description="CA 인증서 경로",
    )
    ssl_certfile: str | None = Field(
        default=None,
        description="클라이언트 인증서 경로",
    )
    ssl_keyfile: str | None = Field(
        default=None,
        description="클라이언트 키 경로",
    )

    # ==========================================================================
    # Security: SASL (프로덕션 인증)
    # ==========================================================================
    sasl_mechanism: str = Field(
        default="SCRAM-SHA-512",
        description="SASL 메커니즘: PLAIN, SCRAM-SHA-256, SCRAM-SHA-512",
    )
    sasl_username: str | None = Field(
        default=None,
        description="SASL 사용자명",
    )
    sasl_password: str | None = Field(
        default=None,
        description="SASL 비밀번호 (시크릿 관리 권장)",
    )

    # ==========================================================================
    # Serialization
    # ==========================================================================
    serialization_format: SerializationFormat = Field(
        default=SerializationFormat.JSON,
        description="직렬화 포맷",
    )
    avro_schema_path: str | None = Field(
        default=None,
        description="Avro 스키마 파일 경로 (.avsc)",
    )

    # ==========================================================================
    # Schema Registry
    # ==========================================================================
    schema_registry_url: str | None = Field(
        default=None,
        description="Confluent Schema Registry URL",
    )
    schema_compatibility: str = Field(
        default="BACKWARD",
        description="스키마 호환성 정책: BACKWARD, FORWARD, FULL",
    )

    def get_producer_config(self) -> dict:
        """
        confluent-kafka Producer 설정 딕셔너리 반환.

        Returns:
            Producer 생성에 필요한 설정 딕셔너리
        """
        import os

        config = {
            # Connection
            "bootstrap.servers": ",".join(self.bootstrap_servers),
            "client.id": f"selfhealing-audit-{os.getpid()}",
            # Idempotent Producer (Exactly-once 보장)
            "enable.idempotence": self.enable_idempotence,
            "acks": "all" if self.enable_idempotence else self.acks,
            "max.in.flight.requests.per.connection": 5,
            # 배치 설정 (성능 최적화)
            "batch.size": self.batch_size_bytes,
            "linger.ms": self.linger_ms,
            # 압축
            "compression.type": self.compression_type,
            # 신뢰성
            "retries": self.retries,
            "retry.backoff.ms": self.retry_backoff_ms,
            # 버퍼 (Producer Lag 모니터링용)
            "queue.buffering.max.messages": self.max_queue_messages,
            "queue.buffering.max.kbytes": self.buffer_memory // 1024,
            # 메시지 전송 타임아웃
            "message.timeout.ms": self.message_timeout_ms,
        }

        # TLS/SASL 인증 (프로덕션용)
        if self.security_protocol != "PLAINTEXT":
            config["security.protocol"] = self.security_protocol

            if "SSL" in self.security_protocol:
                if self.ssl_cafile:
                    config["ssl.ca.location"] = self.ssl_cafile
                if self.ssl_certfile:
                    config["ssl.certificate.location"] = self.ssl_certfile
                if self.ssl_keyfile:
                    config["ssl.key.location"] = self.ssl_keyfile

            if "SASL" in self.security_protocol:
                config["sasl.mechanism"] = self.sasl_mechanism
                if self.sasl_username:
                    config["sasl.username"] = self.sasl_username
                if self.sasl_password:
                    config["sasl.password"] = self.sasl_password

        return config
