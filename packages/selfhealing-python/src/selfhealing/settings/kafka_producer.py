"""
Kafka Producer Settings — Pydantic v2.

Kafka producer 타임아웃 및 연결 설정.
하드코딩된 request_timeout_ms, send/flush/close timeout 값을
환경변수로 제어 가능하게 한다.

Environment Variables:
    SELFHEALING_KAFKA_PRODUCER_REQUEST_TIMEOUT_MS=10000
    SELFHEALING_KAFKA_PRODUCER_SEND_TIMEOUT=10.0
    SELFHEALING_KAFKA_PRODUCER_SHUTDOWN_TIMEOUT=5.0
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class KafkaProducerSettings(BaseSettings):
    """Kafka producer 타임아웃 설정."""

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_KAFKA_PRODUCER_",
        env_file=None,
        extra="ignore",
        validate_default=True,
    )

    request_timeout_ms: int = Field(
        default=10000,
        ge=1000,
        le=120000,
        description="Kafka protocol-level request timeout (ms)",
    )

    send_timeout: float = Field(
        default=10.0,
        ge=1.0,
        le=120.0,
        description="Timeout for producer.send().get() — waits for async send completion",
    )

    shutdown_timeout: float = Field(
        default=5.0,
        ge=1.0,
        le=60.0,
        description="Timeout for producer.flush() and producer.close()",
    )


def get_kafka_producer_settings() -> "KafkaProducerSettings":
    """Root settings 경유 단일 진입점 (SSOT)."""
    from selfhealing.settings.root import get_config

    return get_config().kafka_producer


def reset_kafka_producer_settings() -> None:
    """Root reset으로 위임 (테스트용)."""
    from selfhealing.settings.root import reset_config

    reset_config()
