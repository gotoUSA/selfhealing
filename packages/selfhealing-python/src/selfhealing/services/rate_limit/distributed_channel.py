"""
분산 Rate Limit 이벤트 채널.

Kafka를 통해 429 이벤트를 전체 클러스터에 전파합니다.
단일 Pod가 외부 API의 429 응답을 받으면, 다른 모든 Pod에서도
해당 API에 대한 요청을 자제하도록 합니다 (집단 방어).

Features:
    - Kafka 기반 클러스터 전체 429 이벤트 전파
    - 파티션 키로 순서 보장 (동일 API key는 같은 파티션)
    - 다중 핸들러 지원

Usage:
    from selfhealing.services.rate_limit import DistributedRateLimitChannel

    # 채널 초기화
    channel = DistributedRateLimitChannel()

    # 429 발생 시 전체 클러스터에 전파
    channel.broadcast_rate_limit_429(
        key="payment_api",
        consecutive_429s=3,
        cooldown_until=time.time() + 60,
        calculated_delay=60.0,
    )

    # 구독 (각 Pod에서 호출)
    def my_handler(event_data: dict) -> None:
        print(f"Received 429 for {event_data['key']}")

    channel.subscribe_rate_limit_429(my_handler)
    channel.start()
"""

from __future__ import annotations

import structlog
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from selfhealing.adapters.kafka.event_bus import KafkaEventBus
    from selfhealing.adapters.kafka.consumer import ConsumedEvent

logger = structlog.get_logger()

# Kafka Topic for Rate Limit 429 events
RATE_LIMIT_TOPIC = "selfhealing.rate_limit.events"


class DistributedRateLimitChannel:
    """
    Kafka 기반 분산 Rate Limit 이벤트 채널.

    In-memory EventBus와 달리 Kafka Topic을 통해
    전체 클러스터에 429 이벤트를 전파합니다.

    Attributes:
        _kafka_bus: Kafka EventBus 인스턴스
        _handlers: 등록된 이벤트 핸들러 목록
        _running: 채널 실행 상태
    """

    _instance: DistributedRateLimitChannel | None = None
    _instance_lock = threading.Lock()

    def __init__(self, kafka_bus: "KafkaEventBus | None" = None):
        """
        분산 Rate Limit 채널 초기화.

        Args:
            kafka_bus: Kafka EventBus (None이면 기본 설정으로 생성)
        """
        self._kafka_bus: KafkaEventBus | None = kafka_bus
        self._handlers: list[Callable[[dict[str, Any]], None]] = []
        self._running = False
        self._lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> DistributedRateLimitChannel:
        """싱글톤 인스턴스 반환."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """싱글톤 인스턴스 초기화 (테스트용)."""
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance.stop()
            cls._instance = None

    def _ensure_kafka_bus(self) -> "KafkaEventBus":
        """Kafka EventBus 인스턴스 확보 (지연 초기화)."""
        if self._kafka_bus is None:
            try:
                from selfhealing.adapters.kafka.event_bus import KafkaEventBus

                self._kafka_bus = KafkaEventBus()
            except ImportError as e:
                logger.error(
                    "distributed_rate_limit_channel.kafka_available",
                    error=e,
                )
                raise RuntimeError("Kafka adapter not available") from e

        return self._kafka_bus

    def broadcast_rate_limit_429(
        self,
        key: str,
        consecutive_429s: int,
        cooldown_until: float,
        calculated_delay: float,
    ) -> bool:
        """
        429 이벤트를 전체 클러스터에 브로드캐스트.

        Args:
            key: Rate limit key (예: "payment_api")
            consecutive_429s: 연속 429 횟수
            cooldown_until: Cooldown 종료 시각 (Unix timestamp)
            calculated_delay: 계산된 지연 시간 (초)

        Returns:
            전송 성공 여부
        """
        try:
            kafka_bus = self._ensure_kafka_bus()

            event = {
                "event_type": "RATE_LIMIT_429",
                "key": key,
                "consecutive_429s": consecutive_429s,
                "cooldown_until": cooldown_until,
                "calculated_delay": calculated_delay,
            }

            success = kafka_bus.publish(
                topic=RATE_LIMIT_TOPIC,
                event=event,
                key=key,  # 동일 key는 동일 파티션으로 순서 보장
            )

            if success:
                logger.info(
                    f"[DistributedRateLimitChannel] Broadcasted 429 for '{key}' "
                    f"(consecutive={consecutive_429s})"
                )
            else:
                logger.warning(
                    f"[DistributedRateLimitChannel] Failed to broadcast 429 for '{key}'"
                )

            return success

        except Exception as e:
            logger.error(
                "distributed_rate_limit_channel.broadcast_error",
                error=e,
            )
            return False

    def subscribe_rate_limit_429(
        self,
        handler: Callable[[dict[str, Any]], None],
    ) -> None:
        """
        429 이벤트 구독 등록.

        Args:
            handler: 이벤트 핸들러 (event_data dict를 받음)
        """
        with self._lock:
            self._handlers.append(handler)
            logger.info(
                f"[DistributedRateLimitChannel] Handler registered "
                f"(total: {len(self._handlers)})"
            )

        # 아직 구독 설정 안 됐으면 Kafka 구독 설정
        try:
            kafka_bus = self._ensure_kafka_bus()
            kafka_bus.subscribe(RATE_LIMIT_TOPIC, self._dispatch_to_handlers)
        except Exception as e:
            logger.warning(
                "distributed_rate_limit_channel.subscribe_setup_failed",
                error=e,
            )

    def _dispatch_to_handlers(self, event: "ConsumedEvent") -> bool:
        """
        Kafka 이벤트를 등록된 핸들러들에 전달.

        Args:
            event: Kafka에서 수신한 이벤트

        Returns:
            처리 성공 여부
        """
        event_data = event.value if hasattr(event, "value") else event

        with self._lock:
            handlers = list(self._handlers)

        success = True
        for handler in handlers:
            try:
                handler(event_data)
            except Exception as e:
                logger.error(
                    "distributed_rate_limit_channel.handler_error",
                    error=e,
                )
                success = False

        return success

    def start(self) -> None:
        """Kafka Consumer 시작."""
        if self._running:
            logger.warning("distributed_rate_limit_channel.already_running")
            return

        try:
            kafka_bus = self._ensure_kafka_bus()
            kafka_bus.start()
            self._running = True
            logger.info("distributed_rate_limit_channel.started")
        except Exception as e:
            logger.error(
                "distributed_rate_limit_channel.start_failed",
                error=e,
            )

    def stop(self) -> None:
        """Kafka Consumer 정지."""
        if not self._running:
            return

        try:
            if self._kafka_bus:
                self._kafka_bus.stop()
            self._running = False
            logger.info("distributed_rate_limit_channel.stopped")
        except Exception as e:
            logger.error(
                "distributed_rate_limit_channel.stop_failed",
                error=e,
            )

    @property
    def is_running(self) -> bool:
        """채널 실행 상태 확인."""
        return self._running

    @property
    def handler_count(self) -> int:
        """등록된 핸들러 수."""
        with self._lock:
            return len(self._handlers)


def get_distributed_rate_limit_channel() -> DistributedRateLimitChannel:
    """
    분산 Rate Limit 채널 싱글톤 반환.

    Returns:
        DistributedRateLimitChannel 인스턴스
    """
    return DistributedRateLimitChannel.get_instance()
