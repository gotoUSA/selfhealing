"""
EventStreamProxy 단위 테스트.

테스트 항목:
- 이벤트 구독/해제
- 이벤트 필터링
- 큐 오버플로우 처리
- 프록시 통계
"""

from __future__ import annotations

import queue
import threading
import time
from unittest.mock import MagicMock

import pytest

from selfhealing.adapters.ipc.event_stream_proxy import (
    EventStreamProxy,
    ProxyStats,
    StreamSubscription,
    get_event_stream_proxy,
    reset_event_stream_proxy,
)


class TestStreamSubscription:
    """StreamSubscription 테스트."""

    def test_create_subscription(self):
        """구독 생성."""
        sub = StreamSubscription(
            stream_id="test-stream",
            event_types={"circuit_breaker_opened"},
            event_queue=queue.Queue(),
            client_id="client-001",
        )

        assert sub.stream_id == "test-stream"
        assert "circuit_breaker_opened" in sub.event_types
        assert sub.client_id == "client-001"
        assert sub.event_count == 0


class TestProxyStats:
    """ProxyStats 테스트."""

    def test_initial_stats(self):
        """초기 통계."""
        stats = ProxyStats()

        assert stats.total_subscriptions == 0
        assert stats.active_subscriptions == 0
        assert stats.total_events_proxied == 0
        assert stats.events_dropped == 0


class TestEventStreamProxy:
    """EventStreamProxy 테스트."""

    def test_init(self):
        """프록시 초기화."""
        proxy = EventStreamProxy(max_queue_size=100)

        assert proxy._max_queue_size == 100
        assert len(proxy._subscriptions) == 0

    def test_subscribe(self):
        """구독 시작."""
        proxy = EventStreamProxy()

        event_queue = proxy.subscribe(
            event_types=["circuit_breaker_opened"],
            stream_id="test-stream",
            client_id="client-001",
        )

        assert isinstance(event_queue, queue.Queue)
        assert "test-stream" in proxy._subscriptions
        assert proxy._stats.active_subscriptions == 1

    def test_subscribe_auto_stream_id(self):
        """stream_id 자동 생성."""
        proxy = EventStreamProxy()

        event_queue = proxy.subscribe()

        assert len(proxy._subscriptions) == 1
        stream_id = list(proxy._subscriptions.keys())[0]
        assert len(stream_id) > 0  # UUID 형식

    def test_subscribe_all_events(self):
        """모든 이벤트 구독 (event_types=None)."""
        proxy = EventStreamProxy()

        proxy.subscribe(event_types=None, stream_id="all-events")

        sub = proxy.get_subscription("all-events")
        assert sub.event_types == set()  # 빈 set = 모든 이벤트

    def test_unsubscribe(self):
        """구독 해제."""
        proxy = EventStreamProxy()

        proxy.subscribe(stream_id="to-unsub")
        assert proxy._stats.active_subscriptions == 1

        result = proxy.unsubscribe("to-unsub")

        assert result is True
        assert proxy._stats.active_subscriptions == 0
        assert "to-unsub" not in proxy._subscriptions

    def test_unsubscribe_nonexistent(self):
        """존재하지 않는 구독 해제."""
        proxy = EventStreamProxy()

        result = proxy.unsubscribe("nonexistent")

        assert result is False

    def test_get_subscription(self):
        """구독 정보 조회."""
        proxy = EventStreamProxy()

        proxy.subscribe(stream_id="get-test", client_id="my-client")

        sub = proxy.get_subscription("get-test")

        assert sub is not None
        assert sub.stream_id == "get-test"
        assert sub.client_id == "my-client"

    def test_get_subscription_nonexistent(self):
        """존재하지 않는 구독 조회."""
        proxy = EventStreamProxy()

        sub = proxy.get_subscription("nonexistent")

        assert sub is None

    def test_on_event_handler(self):
        """이벤트 핸들러 테스트."""
        proxy = EventStreamProxy()

        q = proxy.subscribe(event_types=["circuit_breaker_opened"], stream_id="event-test")

        # 이벤트 시뮬레이션
        mock_event = MagicMock()
        mock_event.event_type.value = "circuit_breaker_opened"
        mock_event.to_dict.return_value = {
            "event_type": "circuit_breaker_opened",
            "data": {"service_name": "test"},
        }

        proxy._on_event(mock_event)

        # 큐에서 이벤트 확인
        assert not q.empty()
        event = q.get_nowait()
        assert event["event_type"] == "circuit_breaker_opened"

    def test_event_filtering(self):
        """이벤트 필터링."""
        proxy = EventStreamProxy()

        # CB 이벤트만 구독
        q = proxy.subscribe(event_types=["circuit_breaker_opened"], stream_id="filtered")

        # 다른 타입 이벤트
        mock_event = MagicMock()
        mock_event.event_type.value = "emergency_level_changed"
        mock_event.to_dict.return_value = {"event_type": "emergency_level_changed"}

        proxy._on_event(mock_event)

        # 필터링되어 큐에 없어야 함
        assert q.empty()

    def test_get_events_batch(self):
        """이벤트 배치 조회."""
        proxy = EventStreamProxy()

        q = proxy.subscribe(stream_id="batch-test")

        # 이벤트 직접 추가
        for i in range(5):
            q.put({"index": i})

        events = proxy.get_events_batch("batch-test", max_events=3)

        assert len(events) == 3
        assert events[0]["index"] == 0

    def test_get_events_batch_empty(self):
        """비어있는 배치 조회."""
        proxy = EventStreamProxy()

        proxy.subscribe(stream_id="empty-test")

        events = proxy.get_events_batch("empty-test", timeout=0.01)

        assert events == []

    def test_get_events_batch_nonexistent(self):
        """존재하지 않는 스트림 배치 조회."""
        proxy = EventStreamProxy()

        events = proxy.get_events_batch("nonexistent")

        assert events == []


class TestEventStreamProxyConcurrency:
    """동시성 테스트."""

    def test_concurrent_subscribe_unsubscribe(self):
        """동시 구독/해제."""
        proxy = EventStreamProxy()
        errors = []

        def worker(worker_id):
            try:
                for i in range(50):
                    stream_id = f"stream-{worker_id}-{i}"
                    proxy.subscribe(stream_id=stream_id)
                    proxy.unsubscribe(stream_id)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


class TestGlobalEventStreamProxy:
    """싱글톤 인스턴스 테스트."""

    def teardown_method(self):
        """테스트 후 싱글톤 리셋."""
        reset_event_stream_proxy()

    def test_singleton(self):
        """싱글톤 인스턴스 반환."""
        proxy1 = get_event_stream_proxy()
        proxy2 = get_event_stream_proxy()

        assert proxy1 is proxy2

    def test_reset_singleton(self):
        """싱글톤 리셋."""
        proxy1 = get_event_stream_proxy()

        reset_event_stream_proxy()

        proxy2 = get_event_stream_proxy()

        assert proxy1 is not proxy2
