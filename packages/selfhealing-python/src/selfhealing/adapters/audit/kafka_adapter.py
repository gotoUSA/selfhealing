"""
Kafka Audit Adapter.

고처리량 감사 이벤트를 Kafka로 스트리밍하는 어댑터.
confluent-kafka (librdkafka 기반)를 사용하여 높은 처리량과 신뢰성을 제공합니다.

주요 기능:
- 비동기 전송 (Non-blocking)
- Idempotent Producer (중복 방지)
- 자동 배치 (linger.ms)
- 압축 지원 (snappy/lz4/zstd)
- Hot Partition 방지 (솔트 파티셔닝)
- Producer Lag 메트릭 내장
- Dead Letter Topic (직렬화 실패 시)

Usage:
    from selfhealing.adapters.audit.kafka_adapter import KafkaAuditAdapter

    adapter = KafkaAuditAdapter()
    adapter.log(AuditEntry(...))
    adapter.close()  # 종료 시 플러시
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import structlog

from selfhealing.context.causation_context import get_causation_for_kafka
from selfhealing.interfaces.audit_adapter import AuditEntry, AuditLogAdapter

if TYPE_CHECKING:
    from confluent_kafka import Producer

logger = structlog.get_logger()


# =============================================================================
# confluent-kafka Lazy Import
# =============================================================================


def _get_confluent_kafka():
    """confluent-kafka 지연 로딩."""
    try:
        from confluent_kafka import KafkaError, KafkaException, Producer

        return Producer, KafkaError, KafkaException
    except ImportError as e:
        raise ImportError(
            "confluent-kafka is required for KafkaAuditAdapter. " "Install it with: pip install 'selfhealing[kafka]'"
        ) from e


# =============================================================================
# KafkaAuditAdapter
# =============================================================================


class KafkaAuditAdapter(AuditLogAdapter):
    """
    Kafka 기반 감사 로그 어댑터 (confluent-kafka 기반).

    특징:
    - 비동기 전송 (Non-blocking)
    - Idempotent Producer (중복 방지)
    - 자동 배치 (linger.ms)
    - 압축 지원 (snappy/lz4/zstd)
    - Hot Partition 방지 (솔트 파티셔닝)
    - Producer Lag 메트릭 내장
    """

    SCHEMA_VERSION = 1

    def __init__(
        self,
        settings=None,
        producer: Producer | None = None,
    ):
        """
        KafkaAuditAdapter 초기화.

        Args:
            settings: KafkaAuditSettings 인스턴스 (None이면 환경변수에서 로드)
            producer: 외부 주입 프로듀서 (테스트용)
        """
        from selfhealing.settings.kafka import KafkaAuditSettings

        self._settings = settings or KafkaAuditSettings()
        self._producer = producer
        self._producer_initialized = producer is not None
        self._lock = threading.Lock()
        self._closed = False

        # 통계 (Producer Lag 모니터링용)
        self._sent_count = 0
        self._error_count = 0
        self._pending_count = 0
        self._last_delivery_time: float | None = None

    def _ensure_producer(self) -> Producer:
        """Producer 지연 초기화."""
        if not self._producer_initialized:
            with self._lock:
                if not self._producer_initialized:
                    self._producer = self._create_producer()
                    self._producer_initialized = True
        return self._producer  # type: ignore

    def _create_producer(self) -> Producer:
        """confluent-kafka Producer 생성."""
        Producer, _, _ = _get_confluent_kafka()
        config = self._settings.get_producer_config()
        return Producer(config)

    def _compute_partition_key(self, entry: AuditEntry) -> str | None:
        """
        Hot Partition 방지를 위한 솔트 가미 파티셔닝.

        동일 대상의 이벤트가 파티션에 분산됨.
        순서 보장이 필요한 경우 솔트 제거 옵션 제공.
        """
        if not entry.target_id:
            return None

        base_key = f"{entry.target_type}:{entry.target_id}"

        if self._settings.partition_salt_enabled:
            ts_salt = str(int(entry.timestamp.timestamp() * 1000) % self._settings.partition_salt_range).zfill(2)
            return f"{base_key}:{ts_salt}"

        return base_key

    def _serialize_entry(self, entry: AuditEntry) -> dict[str, Any]:
        """AuditEntry를 Kafka 메시지 값으로 직렬화."""
        return {
            "schema_version": self.SCHEMA_VERSION,
            **entry.to_dict(),
        }

    def _delivery_callback(self, err, msg) -> None:
        """confluent-kafka 전송 완료 콜백."""
        with self._lock:
            self._pending_count -= 1
            self._last_delivery_time = time.time()

            if err:
                self._error_count += 1
                logger.warning(
                    "kafka_audit_adapter.delivery_failed",
                    error=err,
                )
            else:
                self._sent_count += 1

    def log(self, entry: AuditEntry) -> None:
        """
        단일 감사 이벤트 Kafka 전송.

        Non-blocking: produce()는 즉시 반환, 실제 전송은 배치로.

        Args:
            entry: 감사 로그 엔트리
        """
        if self._closed:
            logger.warning("kafka_audit_adapter.adapter_closed_ignoring_log")
            return

        _, KafkaError, KafkaException = _get_confluent_kafka()

        try:
            value = self._serialize_entry(entry)
            key = self._compute_partition_key(entry)

            # Causation 헤더 추가 + Region 정보
            headers = dict(get_causation_for_kafka())
            action_value = entry.action.value if hasattr(entry.action, "value") else str(entry.action)
            headers["x-audit-action"] = action_value.encode("utf-8")
            headers["x-region"] = os.environ.get("SELFHEALING_NAMESPACE_REGION", "unknown").encode("utf-8")

            with self._lock:
                self._pending_count += 1

            producer = self._ensure_producer()
            producer.produce(
                topic=self._settings.topic,
                key=key.encode("utf-8") if key else None,
                value=json.dumps(value).encode("utf-8"),
                headers=list(headers.items()),
                callback=self._delivery_callback,
            )

            # poll()로 콜백 처리 트리거 (non-blocking)
            producer.poll(0)

        except (TypeError, ValueError) as e:
            # 직렬화 실패 → Dead Letter Topic으로 전송
            logger.exception(
                "kafka_audit_adapter.serialization_failed",
                error=e,
            )
            self._send_to_dlt(entry, error=str(e))
            with self._lock:
                self._error_count += 1
                self._pending_count -= 1

        except Exception as e:
            logger.exception(
                "kafka_audit_adapter.produce_failed",
                error=e,
            )
            with self._lock:
                self._error_count += 1
                self._pending_count -= 1

    def _send_to_dlt(self, entry: AuditEntry, error: str) -> None:
        """Dead Letter Topic으로 실패 이벤트 전송."""
        try:
            producer = self._ensure_producer()
            dlt_value = {
                "original_entry": str(entry.to_dict()),
                "error": error,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            producer.produce(
                topic=self._settings.dead_letter_topic,
                value=json.dumps(dlt_value).encode("utf-8"),
            )
        except Exception as e:
            logger.exception(
                "kafka_audit_adapter.dlt_send_failed",
                error=e,
            )

    def log_batch(self, entries: list[AuditEntry]) -> None:
        """
        배치 감사 이벤트 Kafka 전송.

        linger.ms 덕분에 자동으로 배치됨.

        Args:
            entries: 감사 로그 엔트리 목록
        """
        for entry in entries:
            self.log(entry)

        # 배치 후 poll로 콜백 처리
        if self._producer_initialized and self._producer:
            self._producer.poll(0)

    def flush(self, timeout: float | None = None) -> int:
        """
        버퍼 플러시 (동기).

        Args:
            timeout: 플러시 타임아웃 (초)

        Returns:
            플러시되지 않은 메시지 수
        """
        if self._producer_initialized and self._producer:
            return self._producer.flush(timeout or 5.0)
        return 0

    def close(self) -> None:
        """리소스 정리."""
        with self._lock:
            if self._closed:
                return
            self._closed = True

        if self._producer_initialized and self._producer:
            try:
                remaining = self._producer.flush(timeout=10.0)
                if remaining > 0:
                    logger.warning(
                        "kafka_audit_adapter.messages_delivered",
                        remaining=remaining,
                    )
            except Exception as e:
                logger.warning(
                    "kafka_audit_adapter.close_error",
                    error=e,
                )

        logger.info(
            "kafka_audit_adapter.closed_sent_errors",
            self=self._sent_count,
            self_1=self._error_count,
        )

    def get_stats(self) -> dict[str, Any]:
        """
        통계 반환 (Producer Lag 모니터링용).

        pending_count가 높으면 Producer Lag 발생 중.

        Returns:
            통계 딕셔너리
        """
        with self._lock:
            return {
                "sent_count": self._sent_count,
                "error_count": self._error_count,
                "pending_count": self._pending_count,
                "last_delivery_time": self._last_delivery_time,
                "closed": self._closed,
            }

    def is_healthy(self) -> bool:
        """
        Producer 상태 확인.

        pending_count가 max_queue_messages의 80%를 초과하면 unhealthy.

        Returns:
            건강 상태
        """
        with self._lock:
            threshold = self._settings.max_queue_messages * 0.8
            return self._pending_count < threshold and not self._closed

    def query(
        self,
        action=None,
        target_type: str | None = None,
        target_id: str | None = None,
        start_time=None,
        end_time=None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """
        Kafka Producer는 쿼리를 지원하지 않음.

        Consumer 측에서 구현해야 함.
        """
        raise NotImplementedError(
            "Kafka Producer does not support query. " "Use a Kafka Consumer with appropriate storage backend."
        )


# =============================================================================
# Singleton Pattern
# =============================================================================


_kafka_adapter: KafkaAuditAdapter | None = None
_adapter_lock = threading.Lock()


def get_kafka_audit_adapter(settings=None) -> KafkaAuditAdapter:
    """
    KafkaAuditAdapter 싱글톤 반환.

    Args:
        settings: KafkaAuditSettings (첫 호출 시에만 적용)

    Returns:
        KafkaAuditAdapter 인스턴스
    """
    global _kafka_adapter

    with _adapter_lock:
        if _kafka_adapter is None:
            _kafka_adapter = KafkaAuditAdapter(settings=settings)
        return _kafka_adapter


def reset_kafka_audit_adapter() -> None:
    """싱글톤 인스턴스 초기화 (테스트용)."""
    global _kafka_adapter

    with _adapter_lock:
        if _kafka_adapter is not None:
            _kafka_adapter.close()
            _kafka_adapter = None
