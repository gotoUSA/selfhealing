"""
Redis Event Bus

멀티-인스턴스 환경에서 Chaos 이벤트 실시간 동기화를 위한 Redis Pub/Sub 기반 이벤트 버스.

Features:
- Redis Pub/Sub 기반 분산 이벤트 전파
- 기존 SelfHealingEventBus와 동일한 인터페이스
- Graceful fallback (Redis 연결 실패 시 로컬 처리)

Reference: 31_CHAOS_EXPERIMENT_EXPANSION.md §6.1 (Redis Pub/Sub 실시간 동기화)
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from selfhealing.services.event_bus import (
    EventType,
    EventPriority,
    SelfHealingEvent,
    SelfHealingEventBus,
)

logger = logging.getLogger(__name__)


# Redis channel name for chaos events
CHAOS_EVENT_CHANNEL = "selfhealing:chaos:events"


class RedisEventBus:
    """
    Redis Pub/Sub 기반 분산 이벤트 버스.
    
    멀티-인스턴스 환경에서 Chaos 이벤트를 실시간으로 동기화합니다.
    기존 SelfHealingEventBus와 동일한 인터페이스를 제공하면서
    Redis를 통해 다른 인스턴스에도 이벤트를 전파합니다.
    
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
        redis_url: Optional[str] = None,
        channel: str = CHAOS_EVENT_CHANNEL,
        fallback_to_local: bool = True,
    ):
        """
        초기화.
        
        Args:
            redis_url: Redis 연결 URL (없으면 환경변수에서 읽음)
            channel: Pub/Sub 채널명
            fallback_to_local: Redis 연결 실패 시 로컬 이벤트 버스 사용
        """
        self._channel = channel
        self._fallback_to_local = fallback_to_local
        self._local_bus = SelfHealingEventBus()
        self._redis_client: Optional[Any] = None
        self._pubsub: Optional[Any] = None
        self._listener_thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.RLock()
        
        # Redis 연결 시도
        self._redis_url = redis_url or self._get_redis_url()
        self._connect_redis()
    
    def _get_redis_url(self) -> Optional[str]:
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
            logger.info(f"[RedisEventBus] Connected to Redis")
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
        
        백그라운드 스레드에서 Redis 채널을 구독하고
        수신된 이벤트를 로컬 핸들러에 전달합니다.
        """
        if not self._redis_client:
            return
        
        with self._lock:
            if self._running:
                return
            
            self._running = True
            self._pubsub = self._redis_client.pubsub()
            self._pubsub.subscribe(self._channel)
            
            self._listener_thread = threading.Thread(
                target=self._listen_loop,
                daemon=True,
                name="RedisEventBusListener",
            )
            self._listener_thread.start()
            logger.info(f"[RedisEventBus] Listener started on channel: {self._channel}")
    
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
        
        Args:
            event: 발행할 이벤트
            propagate_to_redis: Redis로 전파 여부 (기본 True)
        """
        # 로컬 핸들러에 전달
        self._local_bus.publish(event)
        
        # Redis로 전파
        if propagate_to_redis and self._redis_client:
            try:
                self._redis_client.publish(
                    self._channel,
                    json.dumps(event.to_dict(), default=str),
                )
            except Exception as e:
                logger.warning(f"[RedisEventBus] Failed to publish to Redis: {e}")
    
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


_redis_event_bus: Optional[RedisEventBus] = None
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
