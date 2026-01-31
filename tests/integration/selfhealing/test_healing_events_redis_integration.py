"""
Healing Events Redis Store 통합 테스트.

Redis와의 실제 연동을 테스트합니다.
Docker Compose 환경에서 실행되어야 합니다.

Requirements:
- Docker Compose for Redis
- Run: docker-compose -f docker-compose.test.yml up -d
- Then: pytest tests/integration/selfhealing/test_healing_events_redis_integration.py -v
"""

import os
import pytest
import time
from datetime import datetime, timezone as dt_timezone

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django

django.setup()


@pytest.fixture(autouse=True)
def clean_redis_events():
    """각 테스트 전후 Redis 및 In-Memory 정리."""
    from selfhealing.services.healing_events_store import (
        clear_healing_events_redis,
        set_redis_events_enabled,
        _events_memory,
        _events_memory_lock,
    )

    # 테스트 전 정리
    set_redis_events_enabled(True)
    clear_healing_events_redis()
    with _events_memory_lock:
        _events_memory.clear()

    yield

    # 테스트 후 정리
    clear_healing_events_redis()
    with _events_memory_lock:
        _events_memory.clear()


class TestRedisConnection:
    """Redis 연결 테스트."""

    def test_redis_client_available(self):
        """Redis 클라이언트가 사용 가능한지 확인."""
        from selfhealing.adapters.redis import get_redis_client

        client = get_redis_client()
        assert client is not None, "Redis client should be available"

    def test_redis_ping(self):
        """Redis PING 응답 확인."""
        from selfhealing.adapters.redis import get_redis_client

        client = get_redis_client()
        if client:
            response = client.ping()
            assert response is True


class TestAddHealingEventRedisIntegration:
    """add_healing_event_redis 통합 테스트."""

    def test_event_saved_to_redis(self):
        """이벤트가 Redis에 저장되는지 확인."""
        from selfhealing.services.healing_events_store import (
            add_healing_event_redis,
            get_healing_events_redis,
        )

        event = {
            "type": "circuit_breaker_open",
            "service": "payment",
            "timestamp": datetime.now(dt_timezone.utc).isoformat(),
        }

        result = add_healing_event_redis(event)
        assert result is True, "Should return True on Redis success"

        # 조회 확인
        events = get_healing_events_redis(limit=10)
        assert len(events) >= 1

        found = any(e.get("service") == "payment" for e in events)
        assert found, "Saved event should be retrievable"

    def test_multiple_events_saved_in_order(self):
        """여러 이벤트가 순서대로 저장되는지 확인."""
        from selfhealing.services.healing_events_store import (
            add_healing_event_redis,
            get_healing_events_redis,
        )

        for i in range(5):
            add_healing_event_redis({"index": i, "type": "test"})

        events = get_healing_events_redis(limit=10)
        assert len(events) == 5

        # 최신순 (LPUSH이므로 역순)
        indices = [e.get("index") for e in events]
        assert indices == [4, 3, 2, 1, 0], "Events should be in reverse order (newest first)"

    def test_recorded_at_added_automatically(self):
        """recorded_at 필드가 자동 추가되는지 확인."""
        from selfhealing.services.healing_events_store import (
            add_healing_event_redis,
            get_healing_events_redis,
        )

        event = {"type": "test_recorded_at"}
        add_healing_event_redis(event)

        events = get_healing_events_redis(limit=1)
        assert len(events) == 1
        assert "recorded_at" in events[0]


class TestGetHealingEventsRedisIntegration:
    """get_healing_events_redis 통합 테스트."""

    def test_respects_limit(self):
        """limit 파라미터 존중 확인."""
        from selfhealing.services.healing_events_store import (
            add_healing_event_redis,
            get_healing_events_redis,
        )

        for i in range(10):
            add_healing_event_redis({"index": i})

        events = get_healing_events_redis(limit=5)
        assert len(events) == 5

    def test_empty_result_when_no_events(self):
        """이벤트 없을 때 빈 리스트 반환."""
        from selfhealing.services.healing_events_store import get_healing_events_redis

        events = get_healing_events_redis(limit=10)
        assert events == []


class TestTTLIntegration:
    """TTL 설정 통합 테스트."""

    def test_ttl_set_on_first_event(self):
        """첫 이벤트 저장 시 TTL 설정 확인."""
        from selfhealing.services.healing_events_store import (
            add_healing_event_redis,
            _get_today_key,
            EVENTS_TTL_SECONDS,
        )
        from selfhealing.adapters.redis import get_redis_client

        add_healing_event_redis({"type": "ttl_test"})

        client = get_redis_client()
        if client:
            key = _get_today_key()
            ttl = client.ttl(key)

            # TTL이 설정되었는지 확인 (약간의 오차 허용)
            assert ttl > 0, "TTL should be set"
            assert ttl <= EVENTS_TTL_SECONDS, f"TTL should be <= {EVENTS_TTL_SECONDS}"
            assert ttl > EVENTS_TTL_SECONDS - 60, "TTL should be close to configured value"


class TestClearHealingEventsRedisIntegration:
    """clear_healing_events_redis 통합 테스트."""

    def test_clears_redis_and_memory(self):
        """Redis와 In-Memory 모두 초기화."""
        from selfhealing.services.healing_events_store import (
            add_healing_event_redis,
            get_healing_events_redis,
            clear_healing_events_redis,
        )

        for i in range(5):
            add_healing_event_redis({"index": i})

        events_before = get_healing_events_redis(limit=10)
        assert len(events_before) == 5

        count = clear_healing_events_redis()
        assert count >= 5

        events_after = get_healing_events_redis(limit=10)
        assert len(events_after) == 0


class TestGetHealingEventsCountRedisIntegration:
    """get_healing_events_count_redis 통합 테스트."""

    def test_returns_correct_count(self):
        """정확한 카운트 반환."""
        from selfhealing.services.healing_events_store import (
            add_healing_event_redis,
            get_healing_events_count_redis,
        )

        for i in range(7):
            add_healing_event_redis({"index": i})

        count = get_healing_events_count_redis(days_back=1)
        assert count == 7


class TestXTestBaseIntegration:
    """xtest/base.py 통합 테스트."""

    def test_add_healing_event_uses_redis(self):
        """add_healing_event가 Redis를 사용하는지 확인."""
        from selfhealing.api.django.views.xtest.base import (
            add_healing_event,
            get_healing_events,
            _healing_events,
            _healing_events_lock,
        )
        from selfhealing.services.healing_events_store import (
            get_healing_events_redis,
            clear_healing_events_redis,
        )

        # 초기화
        clear_healing_events_redis()
        with _healing_events_lock:
            _healing_events.clear()

        # 이벤트 추가
        add_healing_event(
            {
                "type": "integration_test",
                "source": "xtest_base",
            }
        )

        # Redis에서 조회
        redis_events = get_healing_events_redis(limit=10)
        assert len(redis_events) >= 1

        found = any(e.get("source") == "xtest_base" for e in redis_events)
        assert found, "Event should be in Redis"

    def test_get_healing_events_uses_redis(self):
        """get_healing_events가 Redis에서 조회하는지 확인."""
        from selfhealing.api.django.views.xtest.base import (
            add_healing_event,
            get_healing_events,
            _healing_events,
            _healing_events_lock,
        )
        from selfhealing.services.healing_events_store import clear_healing_events_redis

        # 초기화
        clear_healing_events_redis()
        with _healing_events_lock:
            _healing_events.clear()

        # 이벤트 추가
        add_healing_event({"type": "get_test", "index": 1})
        add_healing_event({"type": "get_test", "index": 2})

        # 조회
        events = get_healing_events(limit=10, use_redis=True)
        assert len(events) >= 2

    def test_fallback_to_memory_when_use_redis_false(self):
        """use_redis=False일 때 In-Memory에서 조회."""
        from selfhealing.api.django.views.xtest.base import (
            add_healing_event,
            get_healing_events,
            _healing_events,
            _healing_events_lock,
        )
        from selfhealing.services.healing_events_store import clear_healing_events_redis

        # 초기화
        clear_healing_events_redis()
        with _healing_events_lock:
            _healing_events.clear()

        # 이벤트 추가
        add_healing_event({"type": "memory_test"})

        # In-Memory에서 조회
        events = get_healing_events(limit=10, use_redis=False)
        assert len(events) >= 1

        found = any(e.get("type") == "memory_test" for e in events)
        assert found


class TestMultiWorkerSimulation:
    """다중 워커 시뮬레이션 테스트."""

    def test_events_shared_across_simulated_workers(self):
        """여러 '워커'에서 동일한 Redis 데이터 접근."""
        from selfhealing.services.healing_events_store import (
            add_healing_event_redis,
            get_healing_events_redis,
            clear_healing_events_redis,
            _events_memory,
            _events_memory_lock,
        )

        # 워커 1: 이벤트 추가
        clear_healing_events_redis()
        add_healing_event_redis({"worker": "worker1", "action": "add"})

        # 워커 2: In-Memory 비우고 Redis에서만 조회
        with _events_memory_lock:
            _events_memory.clear()

        events = get_healing_events_redis(limit=10)
        assert len(events) >= 1

        found = any(e.get("worker") == "worker1" for e in events)
        assert found, "Worker 2 should see Worker 1's event from Redis"
