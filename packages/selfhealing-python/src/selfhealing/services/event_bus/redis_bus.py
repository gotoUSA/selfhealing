"""
Redis Event Bus

멀티-인스턴스 환경에서 이벤트 실시간 동기화를 위한 Redis Pub/Sub 기반 이벤트 버스.

Features:
- Redis Pub/Sub 기반 분산 이벤트 전파
- 다중 채널 지원 (chaos, config, emergency, circuit_breaker, global)
- 기존 SelfHealingEventBus와 동일한 인터페이스
- Graceful fallback (Redis 연결 실패 시 로컬 처리)

Reference:
- docs/self_healing/middleware_system/70_MULTI_CLUSTER_ARCHITECTURE.md
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from datetime import datetime
from enum import Enum
from typing import Any

from selfhealing.services.event_bus.bus import (
    EventPriority,
    EventType,
    SelfHealingEvent,
    SelfHealingEventBus,
)

logger = logging.getLogger(__name__)

# 인프라 장애 시에도 반드시 전파해야 하는 이벤트 타입
CRITICAL_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.REGION_PRIMARY_CHANGED,
        EventType.EMERGENCY_ACTIVATED,
        EventType.EMERGENCY_DEACTIVATED,
        EventType.KILL_SWITCH_ACTIVATED,
    }
)


# =============================================================================
# Channel Definitions
# =============================================================================


class EventChannel(str, Enum):
    """Redis Pub/Sub 채널 정의."""

    CHAOS = "chaos"
    CONFIG = "config"
    EMERGENCY = "emergency"
    CIRCUIT_BREAKER = "circuit_breaker"
    THROTTLE = "throttle"
    GLOBAL = "global"


# 채널별 Redis 키 패턴
SELFHEALING_EVENT_CHANNELS: dict[str, str] = {
    EventChannel.CHAOS.value: "selfhealing:events:chaos",
    EventChannel.CONFIG.value: "selfhealing:events:config",
    EventChannel.EMERGENCY.value: "selfhealing:events:emergency",
    EventChannel.CIRCUIT_BREAKER.value: "selfhealing:events:cb",
    EventChannel.THROTTLE.value: "selfhealing:events:throttle",
    EventChannel.GLOBAL.value: "selfhealing:global:events",
}

# EventType → Channel 매핑
EVENT_TYPE_TO_CHANNEL: dict[EventType, EventChannel] = {
    # Chaos Events
    EventType.CHAOS_EXPERIMENT_STARTED: EventChannel.CHAOS,
    EventType.CHAOS_EXPERIMENT_STOPPED: EventChannel.CHAOS,
    EventType.CHAOS_EXPERIMENT_BLOCKED: EventChannel.CHAOS,
    # Config Events
    EventType.CONFIG_UPDATED: EventChannel.CONFIG,
    EventType.KILL_SWITCH_ACTIVATED: EventChannel.CONFIG,
    EventType.KILL_SWITCH_DEACTIVATED: EventChannel.CONFIG,
    # Emergency Events
    EventType.EMERGENCY_LEVEL_CHANGED: EventChannel.EMERGENCY,
    EventType.EMERGENCY_ACTIVATED: EventChannel.EMERGENCY,
    EventType.EMERGENCY_DEACTIVATED: EventChannel.EMERGENCY,
    EventType.EMERGENCY_RECOVERY_STARTED: EventChannel.EMERGENCY,
    EventType.EMERGENCY_RECOVERY_COMPLETED: EventChannel.EMERGENCY,
    # Circuit Breaker Events
    EventType.CIRCUIT_BREAKER_OPENED: EventChannel.CIRCUIT_BREAKER,
    EventType.CIRCUIT_BREAKER_CLOSED: EventChannel.CIRCUIT_BREAKER,
    EventType.CIRCUIT_BREAKER_HALF_OPENED: EventChannel.CIRCUIT_BREAKER,
    # Error Budget → Global (cross-cluster 관심)
    EventType.ERROR_BUDGET_CRITICAL: EventChannel.GLOBAL,
    EventType.ERROR_BUDGET_WARNING: EventChannel.GLOBAL,
    EventType.ERROR_BUDGET_RECOVERED: EventChannel.GLOBAL,
    # Security → Global
    EventType.SECURITY_VIOLATION_DETECTED: EventChannel.GLOBAL,
    EventType.SECURITY_VIOLATION_CRITICAL: EventChannel.GLOBAL,
    # Throttle Events
    EventType.THROTTLE_LIMIT_CHANGED: EventChannel.THROTTLE,
    EventType.THROTTLE_SLA_WARNING: EventChannel.THROTTLE,
    EventType.THROTTLE_SLA_CRITICAL: EventChannel.GLOBAL,
    EventType.THROTTLE_LIMIT_RECOVERED: EventChannel.THROTTLE,
    # Throttle + DLQ 연동
    EventType.THROTTLE_REJECTION_STORED: EventChannel.THROTTLE,
    EventType.THROTTLE_REJECTION_REPLAY_STARTED: EventChannel.THROTTLE,
    EventType.THROTTLE_REJECTION_REPLAY_COMPLETED: EventChannel.THROTTLE,
    EventType.THROTTLE_REJECTION_REPLAY_FAILED: EventChannel.THROTTLE,
}

# 기존 호환성을 위한 기본 채널
CHAOS_EVENT_CHANNEL = SELFHEALING_EVENT_CHANNELS[EventChannel.CHAOS.value]


class RedisEventBus:
    """
    Redis Pub/Sub 기반 분산 이벤트 버스.

    멀티-인스턴스 환경에서 이벤트를 실시간으로 동기화합니다.
    기존 SelfHealingEventBus와 동일한 인터페이스를 제공하면서
    Redis를 통해 다른 인스턴스에도 이벤트를 전파합니다.

    다중 채널 지원:
    - chaos: Chaos Engineering 이벤트
    - config: 설정 변경 이벤트
    - emergency: 비상 모드 이벤트
    - circuit_breaker: CB 상태 변경 이벤트
    - global: 글로벌 전파 이벤트 (Error Budget, Security 등)

    Usage:
        bus = get_event_bus(distributed=True)
        bus.subscribe(EventType.CHAOS_EXPERIMENT_STARTED, my_handler)
        bus.publish(SelfHealingEvent(
            event_type=EventType.CHAOS_EXPERIMENT_STARTED,
            data={"experiment_id": "exp-123"},
            source="scheduler",
        ))
    """

    def __init__(
        self,
        redis_url: str | None = None,
        channels: dict[str, str] | None = None,
        fallback_to_local: bool = True,
        subscribe_channels: list[EventChannel] | None = None,
    ):
        """
        초기화.

        Args:
            redis_url: Redis 연결 URL (없으면 환경변수에서 읽음)
            channels: 채널 정의 (없으면 기본값 사용)
            fallback_to_local: Redis 연결 실패 시 로컬 이벤트 버스 사용
            subscribe_channels: 구독할 채널 목록 (없으면 모든 채널)
        """
        self._channels = channels or SELFHEALING_EVENT_CHANNELS
        self._fallback_to_local = fallback_to_local
        self._subscribe_channels = subscribe_channels or list(EventChannel)
        self._local_bus = SelfHealingEventBus()
        self._redis_client: Any | None = None
        self._pubsub: Any | None = None
        self._listener_thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.RLock()
        self._subscribed_redis_channels: set[str] = set()

        # Redis 연결 시도
        self._redis_url = redis_url or self._get_redis_url()
        self._connect_redis()

    def _get_redis_url(self) -> str | None:
        """환경변수에서 Redis URL 읽기."""
        import os

        return os.getenv("REDIS_URL") or os.getenv("CELERY_BROKER_URL")

    def _connect_redis(self) -> bool:
        """Redis 연결."""
        if not self._redis_url:
            logger.info("[RedisEventBus] No Redis URL configured, using local bus only")
            return False

        try:
            import redis

            self._redis_client = redis.from_url(
                self._redis_url,
                decode_responses=True,
            )
            # 연결 테스트
            self._redis_client.ping()
            logger.info("[RedisEventBus] Connected to Redis")
            return True
        except ImportError:
            logger.warning("[RedisEventBus] redis package not installed, using local bus")
            return False
        except Exception as e:
            logger.warning(f"[RedisEventBus] Redis connection failed: {e}, using local bus")
            self._redis_client = None
            return False

    def start_listener(self) -> None:
        """
        Redis Pub/Sub 리스너 시작.

        백그라운드 스레드에서 모든 설정된 Redis 채널을 구독하고
        수신된 이벤트를 로컬 핸들러에 전달합니다.
        """
        if not self._redis_client:
            return

        with self._lock:
            if self._running:
                return

            self._running = True
            self._pubsub = self._redis_client.pubsub()

            # 모든 설정된 채널 구독
            for channel_enum in self._subscribe_channels:
                channel_name = self._channels.get(channel_enum.value)
                if channel_name:
                    self._pubsub.subscribe(channel_name)
                    self._subscribed_redis_channels.add(channel_name)

            self._listener_thread = threading.Thread(
                target=self._listen_loop,
                daemon=True,
                name="RedisEventBusListener",
            )
            self._listener_thread.start()
            logger.info(f"[RedisEventBus] Listener started on channels: " f"{list(self._subscribed_redis_channels)}")

    def stop_listener(self) -> None:
        """Redis Pub/Sub 리스너 중지."""
        with self._lock:
            self._running = False
            if self._pubsub:
                try:
                    self._pubsub.unsubscribe()
                    self._pubsub.close()
                except Exception:
                    pass
                self._pubsub = None
            logger.info("[RedisEventBus] Listener stopped")

    def _listen_loop(self) -> None:
        """Redis 메시지 수신 루프."""
        while self._running and self._pubsub:
            try:
                message = self._pubsub.get_message(timeout=1.0)
                if message and message["type"] == "message":
                    self._handle_redis_message(message["data"])
            except Exception as e:
                if self._running:
                    logger.error(f"[RedisEventBus] Listener error: {e}")

    def _handle_redis_message(self, data: str) -> None:
        """Redis 메시지 처리."""
        try:
            event_dict = json.loads(data)
            event = SelfHealingEvent(
                event_type=EventType(event_dict["event_type"]),
                data=event_dict["data"],
                source=event_dict["source"],
                timestamp=datetime.fromisoformat(event_dict["timestamp"]),
                priority=EventPriority(event_dict.get("priority", 2)),
                correlation_id=event_dict.get("correlation_id"),
            )
            # 로컬 핸들러에 전달 (from_redis=True로 무한 루프 방지)
            self._local_bus.publish(event)
        except Exception as e:
            logger.error(f"[RedisEventBus] Failed to process message: {e}")

    def publish(
        self,
        event: SelfHealingEvent,
        propagate_to_redis: bool = True,
    ) -> None:
        """
        이벤트 발행.

        폴백 체인 (크리티컬 이벤트에만 적용):
        1. 로컬 핸들러 (항상 성공)
        2. Redis Pub/Sub 전파
        3. Kafka 토픽 폴백 (Redis 실패 시)
        4. 로컬 WAL 기록 (Kafka도 실패 시, 최종 안전망)

        Args:
            event: 발행할 이벤트
            propagate_to_redis: Redis로 전파 여부 (기본 True)
        """
        # 1. 로컬 핸들러에 전달 (항상 성공)
        self._local_bus.publish(event)

        if not propagate_to_redis:
            return

        # 2. Redis 시도
        if self._redis_client:
            try:
                channel = self._get_channel_for_event(event.event_type)
                self._redis_client.publish(
                    channel,
                    json.dumps(event.to_dict(), default=str),
                )
                return  # 성공 시 종료
            except Exception as e:
                logger.warning(f"[RedisEventBus] Redis publish failed: {e}")

        # 3. Kafka 폴백 (크리티컬 이벤트만)
        if self._is_critical_event(event):
            try:
                self._publish_to_kafka_fallback(event)
                return  # Kafka 성공 시 종료
            except Exception as e:
                logger.error(f"[RedisEventBus] Kafka fallback failed: {e}")

            # 4. 최종 안전망: 로컬 WAL 기록
            self._write_to_wal(event)

    def _is_critical_event(self, event: SelfHealingEvent) -> bool:
        """인프라 장애 시에도 반드시 전파해야 하는 크리티컬 이벤트인지 판별."""
        return event.event_type in CRITICAL_EVENT_TYPES

    def _publish_to_kafka_fallback(self, event: SelfHealingEvent) -> None:
        """
        Kafka 토픽으로 폴백 발행.

        selfhealing.routing.events 토픽으로 이벤트를 발행합니다.
        MirrorMaker2가 리전 간 자동 미러링하므로 타 리전에도 전달됩니다.

        Raises:
            Exception: Kafka 연결 실패 또는 발행 실패 시
        """
        import os

        kafka_bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS")
        if not kafka_bootstrap:
            raise RuntimeError("KAFKA_BOOTSTRAP_SERVERS not configured")

        try:
            from kafka import KafkaProducer
        except ImportError:
            raise RuntimeError("kafka-python package not installed")

        producer = KafkaProducer(
            bootstrap_servers=kafka_bootstrap.split(","),
            value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
            acks="all",
            retries=3,
            request_timeout_ms=10000,
        )

        topic = "selfhealing.routing.events"
        producer.send(topic, value=event.to_dict()).get(timeout=10)
        producer.flush(timeout=5)
        producer.close(timeout=5)

        logger.info(f"[RedisEventBus] Event published to Kafka fallback: " f"{event.event_type.value}")

    def _write_to_wal(self, event: SelfHealingEvent) -> None:
        """
        로컬 WAL에 이벤트 기록 (최종 안전망).

        인프라 복구 시 WAL을 재생하여 Redis/Kafka로 재발행할 수 있습니다.
        audit/wal 모듈의 WriteAheadLog 패턴을 재활용합니다.
        """
        try:
            from selfhealing.audit.wal import WriteAheadLog
            from selfhealing.audit.wal._models import WALConfig

            config = WALConfig(
                wal_dir="/var/log/selfhealing/event_bus_wal",
                file_prefix="event_bus_wal",
                max_file_size_mb=50,
                sync_on_write=True,
                max_files=5,
            )
            wal = WriteAheadLog(config=config)
            wal.write(event.to_dict())

            logger.warning(f"[RedisEventBus] Critical event written to WAL: " f"{event.event_type.value}")
        except Exception as e:
            logger.error(f"[RedisEventBus] WAL write failed for critical event " f"{event.event_type.value}: {e}")

    def _get_channel_for_event(self, event_type: EventType) -> str:
        """EventType에 맞는 Redis 채널 반환."""
        channel_enum = EVENT_TYPE_TO_CHANNEL.get(event_type, EventChannel.GLOBAL)
        return self._channels.get(channel_enum.value, self._channels[EventChannel.GLOBAL.value])

    def get_channel(self, channel: EventChannel) -> str:
        """특정 채널의 Redis 키 반환."""
        return self._channels.get(channel.value, "")

    def subscribe(
        self,
        event_type: EventType,
        handler: Callable[[SelfHealingEvent], None],
        priority: EventPriority = EventPriority.NORMAL,
    ) -> bool:
        """
        이벤트 구독.

        Args:
            event_type: 구독할 이벤트 타입
            handler: 핸들러 함수
            priority: 핸들러 우선순위

        Returns:
            True if subscribed successfully
        """
        return self._local_bus.subscribe(event_type, handler, priority=priority)

    def unsubscribe(
        self,
        event_type: EventType,
        handler: Callable[[SelfHealingEvent], None],
    ) -> bool:
        """이벤트 구독 해제."""
        return self._local_bus.unsubscribe(event_type, handler)

    def get_local_bus(self) -> SelfHealingEventBus:
        """로컬 이벤트 버스 반환."""
        return self._local_bus

    def is_distributed(self) -> bool:
        """분산 모드(Redis 연결됨) 여부."""
        return self._redis_client is not None


# =============================================================================
# Factory Function
# =============================================================================


_redis_event_bus: RedisEventBus | None = None
_factory_lock = threading.Lock()


def get_event_bus(distributed: bool = False) -> SelfHealingEventBus | RedisEventBus:
    """
    이벤트 버스 인스턴스 반환.

    Args:
        distributed: True이면 Redis 기반 분산 이벤트 버스 반환

    Returns:
        SelfHealingEventBus (로컬) 또는 RedisEventBus (분산)
    """
    if not distributed:
        return SelfHealingEventBus()

    global _redis_event_bus

    if _redis_event_bus is None:
        with _factory_lock:
            if _redis_event_bus is None:
                _redis_event_bus = RedisEventBus()
                _redis_event_bus.start_listener()

    return _redis_event_bus


def reset_redis_event_bus() -> None:
    """Redis 이벤트 버스 리셋 (테스트용)."""
    global _redis_event_bus
    with _factory_lock:
        if _redis_event_bus:
            _redis_event_bus.stop_listener()
            _redis_event_bus = None
