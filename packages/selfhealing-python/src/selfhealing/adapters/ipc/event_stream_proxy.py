"""
EventBus → gRPC 스트림 중계 프록시.

SelfHealingEventBus의 이벤트를 gRPC Server-Side Streaming으로
타언어 클라이언트에게 실시간 푸시합니다.

특징:
- EventBus 구독을 gRPC 스트림으로 브릿지
- 클라이언트별 구독 필터링
- 연결 해제 시 자동 정리
- 메모리 보호를 위한 큐 크기 제한

Usage:
    from selfhealing.adapters.ipc.event_stream_proxy import EventStreamProxy

    proxy = EventStreamProxy()

    # 스트림 구독 시작
    queue = proxy.subscribe(
        stream_id="client-001",
        event_types=["circuit_breaker_opened", "emergency_level_changed"]
    )

    # 이벤트 수신 (gRPC 스트리밍에서 사용)
    for event in proxy.iter_events(stream_id="client-001"):
        yield event  # gRPC stream.Send()

    # 구독 해제
    proxy.unsubscribe("client-001")
"""

from __future__ import annotations

import structlog
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

logger = structlog.get_logger()


@dataclass
class StreamSubscription:
    """스트림 구독 정보."""

    stream_id: str
    """스트림 식별자."""

    event_types: set[str]
    """구독한 이벤트 타입들."""

    event_queue: queue.Queue
    """이벤트 큐."""

    client_id: str | None = None
    """클라이언트 식별자."""

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """구독 생성 시각."""

    last_event_at: datetime | None = None
    """마지막 이벤트 전송 시각."""

    event_count: int = 0
    """전송된 이벤트 수."""


@dataclass
class ProxyStats:
    """프록시 통계."""

    total_subscriptions: int = 0
    """총 구독 수."""

    active_subscriptions: int = 0
    """활성 구독 수."""

    total_events_proxied: int = 0
    """총 프록시된 이벤트 수."""

    events_dropped: int = 0
    """큐 초과로 드롭된 이벤트 수."""


class EventStreamProxy:
    """
    SelfHealingEventBus를 gRPC 스트림으로 중계.

    EventBus의 이벤트를 구독하여 연결된 클라이언트들에게
    실시간으로 푸시합니다.
    """

    DEFAULT_QUEUE_SIZE = 1000
    EVENT_TIMEOUT = 0.1  # 이벤트 대기 타임아웃 (초)

    def __init__(
        self,
        max_queue_size: int = DEFAULT_QUEUE_SIZE,
    ):
        """
        프록시 초기화.

        Args:
            max_queue_size: 구독당 최대 큐 크기
        """
        self._subscriptions: dict[str, StreamSubscription] = {}
        self._lock = threading.RLock()
        self._max_queue_size = max_queue_size
        self._stats = ProxyStats()
        self._registered = False

        self._register_event_handlers()

    def _register_event_handlers(self) -> None:
        """EventBus 이벤트 핸들러 등록."""
        if self._registered:
            return

        try:
            from selfhealing.services.event_bus import EventType, get_event_bus

            bus = get_event_bus()

            # 모든 이벤트 타입에 대해 핸들러 등록
            for event_type in EventType:
                bus.subscribe(event_type, self._on_event)

            self._registered = True
            logger.debug("cell_registry.bulkheads_registered")
        except ImportError:
            logger.warning("event_stream_proxy.eventbus_available")
        except Exception as e:
            logger.warning(
                "event_stream_proxy.eventbus_registration_failed",
                error=e,
            )

    def _on_event(self, event: Any) -> None:
        """
        EventBus 이벤트 핸들러.

        이벤트를 구독 중인 모든 클라이언트 큐에 전달합니다.
        """
        event_type = event.event_type.value
        event_dict = event.to_dict()

        with self._lock:
            for sub in self._subscriptions.values():
                # 이벤트 타입 필터링 (빈 set은 모든 이벤트)
                if sub.event_types and event_type not in sub.event_types:
                    continue

                try:
                    sub.event_queue.put_nowait(event_dict)
                    sub.last_event_at = datetime.now(timezone.utc)
                    sub.event_count += 1
                    self._stats.total_events_proxied += 1
                except queue.Full:
                    # 큐 가득 참 - 가장 오래된 이벤트 제거 후 재시도
                    try:
                        sub.event_queue.get_nowait()
                        sub.event_queue.put_nowait(event_dict)
                        self._stats.events_dropped += 1
                    except queue.Empty:
                        pass

    def subscribe(
        self,
        event_types: list[str] | None = None,
        stream_id: str | None = None,
        client_id: str | None = None,
    ) -> queue.Queue:
        """
        이벤트 구독 및 큐 생성.

        Args:
            event_types: 구독할 이벤트 타입 목록 (None = 전체)
            stream_id: 스트림 식별자 (None = 자동 생성)
            client_id: 클라이언트 식별자

        Returns:
            이벤트 수신용 큐
        """
        if stream_id is None:
            stream_id = str(uuid.uuid4())

        event_queue: queue.Queue = queue.Queue(maxsize=self._max_queue_size)

        subscription = StreamSubscription(
            stream_id=stream_id,
            event_types=set(event_types) if event_types else set(),
            event_queue=event_queue,
            client_id=client_id,
        )

        with self._lock:
            self._subscriptions[stream_id] = subscription
            self._stats.total_subscriptions += 1
            self._stats.active_subscriptions = len(self._subscriptions)

        logger.info(
            "event_stream_proxy.new_subscription_events",
            stream_id=stream_id,
            value=event_types or 'all',
        )
        return event_queue

    def unsubscribe(self, stream_id: str) -> bool:
        """
        스트림 구독 해제.

        Args:
            stream_id: 스트림 식별자

        Returns:
            해제 성공 여부
        """
        with self._lock:
            if stream_id in self._subscriptions:
                del self._subscriptions[stream_id]
                self._stats.active_subscriptions = len(self._subscriptions)
                logger.info(
                    "event_stream_proxy.unsubscribed",
                    stream_id=stream_id,
                )
                return True
            return False

    def iter_events(
        self,
        stream_id: str,
        timeout: float | None = None,
    ) -> Iterator[dict[str, Any]]:
        """
        이벤트 이터레이터 (gRPC 스트리밍용).

        Args:
            stream_id: 스트림 식별자
            timeout: 이벤트 대기 타임아웃 (None = 무한)

        Yields:
            이벤트 딕셔너리
        """
        sub = self._subscriptions.get(stream_id)
        if sub is None:
            return

        event_timeout = timeout or self.EVENT_TIMEOUT

        while stream_id in self._subscriptions:
            try:
                event = sub.event_queue.get(timeout=event_timeout)
                yield event
            except queue.Empty:
                # 타임아웃 - 계속 대기
                continue

    def get_events_batch(
        self,
        stream_id: str,
        max_events: int = 100,
        timeout: float = 0.01,
    ) -> list[dict[str, Any]]:
        """
        이벤트 배치 조회.

        Args:
            stream_id: 스트림 식별자
            max_events: 최대 이벤트 수
            timeout: 대기 타임아웃

        Returns:
            이벤트 딕셔너리 리스트
        """
        sub = self._subscriptions.get(stream_id)
        if sub is None:
            return []

        events = []
        while len(events) < max_events:
            try:
                event = sub.event_queue.get(timeout=timeout)
                events.append(event)
            except queue.Empty:
                break

        return events

    def get_subscription(self, stream_id: str) -> StreamSubscription | None:
        """구독 정보 조회."""
        return self._subscriptions.get(stream_id)

    def list_subscriptions(self) -> list[dict[str, Any]]:
        """활성 구독 목록."""
        with self._lock:
            return [
                {
                    "stream_id": sub.stream_id,
                    "client_id": sub.client_id,
                    "event_types": list(sub.event_types) or ["all"],
                    "created_at": sub.created_at.isoformat(),
                    "last_event_at": (sub.last_event_at.isoformat() if sub.last_event_at else None),
                    "event_count": sub.event_count,
                    "queue_size": sub.event_queue.qsize(),
                }
                for sub in self._subscriptions.values()
            ]

    @property
    def stats(self) -> ProxyStats:
        """프록시 통계."""
        return self._stats

    def get_stats_dict(self) -> dict[str, Any]:
        """통계 딕셔너리."""
        return {
            "total_subscriptions": self._stats.total_subscriptions,
            "active_subscriptions": self._stats.active_subscriptions,
            "total_events_proxied": self._stats.total_events_proxied,
            "events_dropped": self._stats.events_dropped,
            "max_queue_size": self._max_queue_size,
        }

    def cleanup_idle(self, idle_seconds: float = 300) -> int:
        """
        유휴 구독 정리.

        Args:
            idle_seconds: 유휴 판정 시간 (초)

        Returns:
            정리된 구독 수
        """
        now = datetime.now(timezone.utc)
        to_remove = []

        with self._lock:
            for stream_id, sub in self._subscriptions.items():
                last_activity = sub.last_event_at or sub.created_at
                idle_time = (now - last_activity).total_seconds()
                if idle_time > idle_seconds:
                    to_remove.append(stream_id)

            for stream_id in to_remove:
                del self._subscriptions[stream_id]

            self._stats.active_subscriptions = len(self._subscriptions)

        if to_remove:
            logger.info(
                "event_stream_proxy.cleaned_up_idle_subscriptions",
                count=len(to_remove),
            )

        return len(to_remove)

    def close(self) -> None:
        """프록시 종료 및 리소스 정리."""
        with self._lock:
            stream_ids = list(self._subscriptions.keys())
            for stream_id in stream_ids:
                self.unsubscribe(stream_id)


# =============================================================================
# 싱글톤 인스턴스
# =============================================================================

_proxy: EventStreamProxy | None = None


def get_event_stream_proxy() -> EventStreamProxy:
    """싱글톤 프록시 인스턴스 반환."""
    global _proxy
    if _proxy is None:
        _proxy = EventStreamProxy()
    return _proxy


def reset_event_stream_proxy() -> None:
    """프록시 인스턴스 리셋 (테스트용)."""
    global _proxy
    if _proxy is not None:
        _proxy.close()
    _proxy = None
