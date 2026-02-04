"""
Kafka 설정 모듈.

Kafka Producer/Consumer 연결 설정을 관리합니다.
환경변수 우선순위: SELFHEALING_KAFKA_* 환경변수 > 기본값
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


def _get_default_security_protocol() -> str:
    """
    환경에 따른 기본 보안 프로토콜 반환.

    프로덕션/스테이징 환경에서는 SASL_SSL 사용,
    개발 환경에서만 PLAINTEXT 허용.
    """
    env = os.getenv("ENVIRONMENT", "development")
    if env in ("production", "staging"):
        return "SASL_SSL"
    return "PLAINTEXT"


class KafkaSettings(BaseSettings):
    """
    Kafka 설정.

    브로커, 토픽, Producer/Consumer, 보안 설정을 포함합니다.
    모든 설정은 환경변수로 오버라이드 가능합니다.
    """

    # =========================================================================
    # 브로커 설정
    # =========================================================================
    bootstrap_servers: str = Field(
        default="localhost:9092",
        description="Kafka 브로커 주소 (쉼표 구분)",
    )

    # =========================================================================
    # 토픽 설정
    # =========================================================================
    topic_prefix: str = Field(
        default="selfhealing.",
        description="토픽 프리픽스",
    )
    audit_topic: str = Field(
        default="audit.events",
        description="Audit 이벤트 토픽",
    )
    dlq_topic: str = Field(
        default="dlq.events",
        description="Dead Letter Queue 이벤트 토픽",
    )
    recovery_topic: str = Field(
        default="recovery.events",
        description="Recovery 이벤트 토픽",
    )

    # =========================================================================
    # Producer 설정
    # =========================================================================
    producer_acks: Literal["0", "1", "all"] = Field(
        default="all",
        description="Producer ACK 레벨 (all 권장 - 데이터 무손실 보장)",
    )
    producer_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Producer 재시도 횟수",
    )
    producer_batch_size: int = Field(
        default=16384,
        ge=0,
        description="Producer 배치 크기 (bytes)",
    )
    producer_linger_ms: int = Field(
        default=10,
        ge=0,
        le=1000,
        description="Producer 배치 대기 시간 (ms)",
    )
    producer_compression_type: Literal["none", "gzip", "snappy", "lz4", "zstd"] = Field(
        default="zstd",
        description="압축 알고리즘 (zstd 권장 - 높은 압축률과 속도)",
    )
    producer_idempotent: bool = Field(
        default=True,
        description="Idempotent Producer 활성화 (중복 메시지 방지)",
    )

    # =========================================================================
    # Consumer 설정
    # =========================================================================
    consumer_group_id: str = Field(
        default="selfhealing-audit-consumer",
        description="Consumer 그룹 ID",
    )
    consumer_auto_offset_reset: Literal["earliest", "latest"] = Field(
        default="earliest",
        description="오프셋 리셋 정책 (earliest: 처음부터, latest: 최신부터)",
    )
    consumer_enable_auto_commit: bool = Field(
        default=False,
        description="자동 오프셋 커밋 비활성화 (수동 커밋 권장)",
    )
    consumer_max_poll_records: int = Field(
        default=500,
        ge=1,
        le=10000,
        description="한 번에 폴링할 최대 레코드 수",
    )
    consumer_session_timeout_ms: int = Field(
        default=30000,
        ge=6000,
        le=300000,
        description="Consumer 세션 타임아웃 (ms)",
    )

    # =========================================================================
    # 보안 설정
    # =========================================================================
    security_protocol: str = Field(
        default_factory=_get_default_security_protocol,
        description="보안 프로토콜 (PLAINTEXT, SSL, SASL_PLAINTEXT, SASL_SSL)",
    )
    sasl_mechanism: str | None = Field(
        default=None,
        description="SASL 메커니즘 (PLAIN, SCRAM-SHA-256, SCRAM-SHA-512)",
    )
    sasl_username: str | None = Field(
        default=None,
        description="SASL 사용자명",
    )
    sasl_password: str | None = Field(
        default=None,
        description="SASL 비밀번호",
    )
    ssl_cafile: str | None = Field(
        default=None,
        description="SSL CA 인증서 파일 경로",
    )

    # =========================================================================
    # Schema Registry 설정
    # =========================================================================
    schema_registry_url: str | None = Field(
        default=None,
        description="Confluent Schema Registry URL",
    )
    schema_compatibility: Literal["BACKWARD", "FORWARD", "FULL", "NONE"] = Field(
        default="BACKWARD",
        description="스키마 호환성 정책",
    )

    model_config = {
        "env_prefix": "SELFHEALING_KAFKA_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    # =========================================================================
    # 계산된 속성
    # =========================================================================
    @property
    def full_audit_topic(self) -> str:
        """프리픽스가 포함된 Audit 토픽 이름."""
        return f"{self.topic_prefix}{self.audit_topic}"

    @property
    def full_dlq_topic(self) -> str:
        """프리픽스가 포함된 DLQ 토픽 이름."""
        return f"{self.topic_prefix}{self.dlq_topic}"

    @property
    def full_recovery_topic(self) -> str:
        """프리픽스가 포함된 Recovery 토픽 이름."""
        return f"{self.topic_prefix}{self.recovery_topic}"


@lru_cache(maxsize=1)
def get_kafka_settings() -> KafkaSettings:
    """
    Kafka 설정 싱글톤 반환.

    설정은 애플리케이션 시작 시 한 번만 로드됩니다.
    """
    return KafkaSettings()


def reset_kafka_settings() -> None:
    """
    설정 캐시 초기화.

    테스트 환경에서 설정을 다시 로드할 때 사용합니다.
    """
    get_kafka_settings.cache_clear()
