"""
Kafka Audit Producer.

Idempotent Producer를 사용하여 중복 메시지 없이
Audit 이벤트를 Kafka로 전송합니다.

핵심 특징:
- Idempotent Producer: 중복 메시지 방지
- WAL-First: Kafka 전송 전 WAL 기록 (선택적)
- Delivery Callback: 비동기 전송 결과 추적
- Graceful Shutdown: 미전송 메시지 처리

Usage:
    from selfhealing.adapters.kafka.producer import KafkaAuditProducer

    with KafkaAuditProducer() as producer:
        producer.publish_audit_event(
            event={"action": "dlq_store", "data": {...}},
            domain="order",
        )
        producer.flush()
"""

from __future__ import annotations

import json
import structlog
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from confluent_kafka import Producer

from selfhealing.adapters.kafka.config import KafkaSettings, get_kafka_settings

logger = structlog.get_logger()


@dataclass
class DeliveryReport:
    """
    Kafka 메시지 전송 결과 리포트.

    Producer의 전송 결과를 비동기 콜백에서 받습니다.
    """

    topic: str
    """전송된 토픽."""

    partition: int
    """전송된 파티션."""

    offset: int
    """메시지 오프셋."""

    timestamp: float
    """전송 시각 (Unix timestamp)."""

    key: str | None
    """메시지 키."""

    error: str | None = None
    """에러 메시지 (실패 시)."""

    @property
    def success(self) -> bool:
        """전송 성공 여부."""
        return self.error is None


class KafkaAuditProducer:
    """
    Kafka Audit 이벤트 Producer.

    Idempotent Producer를 사용하여 정확히 한 번(Exactly-once) 전송을 보장합니다.
    """

    def __init__(
        self,
        settings: KafkaSettings | None = None,
        on_delivery: Callable[[DeliveryReport], None] | None = None,
        wal: Any | None = None,
    ):
        """
        KafkaAuditProducer 초기화.

        Args:
            settings: Kafka 설정 (None이면 기본값 사용)
            on_delivery: 전송 결과 콜백 (모든 메시지에 적용)
            wal: WriteAheadLog 인스턴스 (WAL-First 프로토콜 사용 시)
        """
        self._settings = settings or get_kafka_settings()
        self._on_delivery = on_delivery
        self._wal = wal

        self._producer: Producer | None = None
        self._lock = threading.Lock()
        self._stats = {
            "messages_sent": 0,
            "messages_delivered": 0,
            "messages_failed": 0,
            "last_error": None,
        }

        self._init_producer()

    def _init_producer(self) -> None:
        """Kafka Producer 초기화."""
        try:
            from confluent_kafka import Producer

            config = self._build_producer_config()
            self._producer = Producer(config)

            logger.info(
                f"[KafkaProducer] 초기화 완료 - "
                f"bootstrap_servers={self._settings.bootstrap_servers}, "
                f"idempotent={self._settings.producer_idempotent}"
            )
        except ImportError:
            logger.error(
                "[KafkaProducer] confluent-kafka 패키지가 설치되지 않았습니다. " "설치: pip install 'selfhealing[kafka]'"
            )
            raise
        except Exception as e:
            logger.error(
                "kafka_producer.초기화_실패",
                error=e,
            )
            raise

    def _build_producer_config(self) -> dict[str, Any]:
        """Producer 설정 딕셔너리 생성."""
        config: dict[str, Any] = {
            "bootstrap.servers": self._settings.bootstrap_servers,
            "acks": self._settings.producer_acks,
            "retries": self._settings.producer_retries,
            "batch.size": self._settings.producer_batch_size,
            "linger.ms": self._settings.producer_linger_ms,
            "compression.type": self._settings.producer_compression_type,
            "enable.idempotence": self._settings.producer_idempotent,
        }

        # 보안 설정 적용
        if self._settings.security_protocol != "PLAINTEXT":
            config["security.protocol"] = self._settings.security_protocol
            if self._settings.sasl_mechanism:
                config["sasl.mechanism"] = self._settings.sasl_mechanism
                config["sasl.username"] = self._settings.sasl_username
                config["sasl.password"] = self._settings.sasl_password
            if self._settings.ssl_cafile:
                config["ssl.ca.location"] = self._settings.ssl_cafile

        return config

    def _delivery_callback(
        self,
        err: Any,
        msg: Any,
        user_callback: Callable[[DeliveryReport], None] | None = None,
    ) -> None:
        """전송 결과 콜백 (내부용)."""
        report = DeliveryReport(
            topic=msg.topic(),
            partition=msg.partition(),
            offset=msg.offset() if msg.offset() is not None else -1,
            timestamp=time.time(),
            key=msg.key().decode() if msg.key() else None,
            error=str(err) if err else None,
        )

        with self._lock:
            if err:
                self._stats["messages_failed"] += 1
                self._stats["last_error"] = str(err)
                logger.error(
                    "kafka_producer.전송_실패",
                    error=err,
                )
            else:
                self._stats["messages_delivered"] += 1
                logger.debug(
                    "kafka_producer.전송_완료",
                    report=report.topic,
                    report_1=report.partition,
                    report_2=report.offset,
                )

        # 사용자 정의 콜백 호출
        if user_callback:
            try:
                user_callback(report)
            except Exception as e:
                logger.error(
                    "kafka_producer.사용자_콜백_오류",
                    error=e,
                )

        # 기본 콜백 호출
        if self._on_delivery:
            try:
                self._on_delivery(report)
            except Exception as e:
                logger.error(
                    "kafka_producer.콜백_오류",
                    error=e,
                )

    def publish(
        self,
        topic: str,
        event: dict[str, Any],
        key: str | None = None,
        headers: dict[str, bytes] | None = None,
        partition: int | None = None,
        on_delivery: Callable[[DeliveryReport], None] | None = None,
    ) -> bool:
        """
        이벤트 발행.

        WAL-First 프로토콜 (활성화 시):
        1. WAL에 기록
        2. Kafka로 전송
        3. 전송 결과 콜백

        Args:
            topic: 토픽 이름 (프리픽스 제외, 자동으로 추가됨)
            event: 이벤트 데이터 (JSON 직렬화 가능한 딕셔너리)
            key: 파티션 키 (None이면 라운드로빈)
            headers: 추가 헤더 (기존 causation 헤더에 병합)
            partition: 특정 파티션 지정 (None이면 자동)
            on_delivery: 이 메시지의 전송 결과 콜백

        Returns:
            전송 시작 성공 여부 (실제 전송 완료는 콜백에서 확인)
        """
        if not self._producer:
            logger.error("kafka_producer.producer가_초기화되지_않았습니다")
            return False

        try:
            # WAL-First 프로토콜 (선택적)
            if self._wal:
                self._wal.write(
                    {
                        "kafka_topic": topic,
                        "kafka_key": key,
                        "event": event,
                        "timestamp": time.time(),
                    }
                )

            # 전체 토픽 이름 생성
            full_topic = f"{self._settings.topic_prefix}{topic}"

            # JSON 직렬화
            value = json.dumps(event, default=str, ensure_ascii=False).encode("utf-8")

            # Causation Context 헤더 추가
            from selfhealing.context.causation_context import get_causation_for_kafka

            all_headers = get_causation_for_kafka()
            if headers:
                all_headers.update(headers)

            # confluent-kafka는 헤더를 리스트 형식으로 요구
            header_list = [(k, v) for k, v in all_headers.items()] if all_headers else None

            # 메시지 전송
            self._producer.produce(
                topic=full_topic,
                value=value,
                key=key.encode("utf-8") if key else None,
                headers=header_list,
                partition=partition if partition is not None else -1,
                callback=lambda err, msg: self._delivery_callback(err, msg, on_delivery),
            )

            with self._lock:
                self._stats["messages_sent"] += 1

            return True

        except Exception as e:
            logger.error(
                "kafka_producer.발행_오류",
                error=e,
            )
            with self._lock:
                self._stats["messages_failed"] += 1
                self._stats["last_error"] = str(e)
            return False

    def publish_audit_event(
        self,
        event: dict[str, Any],
        domain: str | None = None,
        on_delivery: Callable[[DeliveryReport], None] | None = None,
    ) -> bool:
        """
        Audit 이벤트 발행 (편의 메서드).

        자동으로 타임스탬프와 스키마 버전을 추가합니다.

        Args:
            event: Audit 이벤트 데이터
            domain: 도메인 (파티션 키로 사용)
            on_delivery: 전송 결과 콜백

        Returns:
            전송 시작 성공 여부
        """
        # 메타데이터 자동 추가
        event.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        event.setdefault("schema_version", 1)

        return self.publish(
            topic=self._settings.audit_topic,
            event=event,
            key=domain,
            on_delivery=on_delivery,
        )

    def flush(self, timeout: float = 10.0) -> int:
        """
        버퍼에 있는 모든 메시지 전송 완료 대기.

        Args:
            timeout: 최대 대기 시간 (초)

        Returns:
            플러시 후 버퍼에 남은 메시지 수 (0이 정상)
        """
        if not self._producer:
            return 0

        remaining = self._producer.flush(timeout)
        if remaining > 0:
            logger.warning(
                "kafka_producer.플러시_메시지가_버퍼에_남음",
                remaining=remaining,
            )
        return remaining

    def poll(self, timeout: float = 0) -> int:
        """
        전송 결과 콜백 처리를 위한 폴링.

        비동기 전송 후 콜백을 처리하려면 주기적으로 호출해야 합니다.

        Args:
            timeout: 폴링 타임아웃 (초)

        Returns:
            처리된 이벤트 수
        """
        if not self._producer:
            return 0
        return self._producer.poll(timeout)

    def close(self) -> None:
        """Producer 종료 (Graceful shutdown)."""
        if self._producer:
            # 미전송 메시지 처리
            remaining = self.flush(timeout=30.0)
            if remaining > 0:
                logger.warning(
                    "kafka_producer.메시지가_미전송_상태로_종료",
                    remaining=remaining,
                )
            self._producer = None
            logger.info("kafka_producer.종료됨")

    def get_stats(self) -> dict[str, Any]:
        """전송 통계 반환."""
        with self._lock:
            return dict(self._stats)

    def __enter__(self) -> KafkaAuditProducer:
        """Context manager 진입."""
        return self

    def __exit__(self, *args: Any) -> None:
        """Context manager 종료."""
        self.close()


# =============================================================================
# 싱글톤 인스턴스 관리
# =============================================================================

_producer: KafkaAuditProducer | None = None
_producer_lock = threading.Lock()


def get_kafka_producer() -> KafkaAuditProducer:
    """
    Kafka Producer 싱글톤 반환.

    애플리케이션 전체에서 하나의 Producer 인스턴스를 공유합니다.
    """
    global _producer
    if _producer is None:
        with _producer_lock:
            if _producer is None:
                _producer = KafkaAuditProducer()
    return _producer


def reset_kafka_producer() -> None:
    """
    Producer 싱글톤 초기화.

    테스트 환경에서 Producer를 다시 생성할 때 사용합니다.
    """
    global _producer
    with _producer_lock:
        if _producer is not None:
            _producer.close()
            _producer = None
