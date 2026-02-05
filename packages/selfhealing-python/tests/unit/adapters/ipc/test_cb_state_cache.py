"""
CBStateCache 단위 테스트.

테스트 항목:
- TTL 기반 캐시 만료
- EventBus 이벤트 기반 무효화
- Thread-safe 동시 접근
- 캐시 통계
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.adapters.ipc.cb_state_cache import (
    CacheEntry,
    CacheStats,
    CBStateCache,
    get_cb_state_cache,
    reset_cb_state_cache,
)


class TestCacheEntry:
    """CacheEntry 테스트."""

    def test_create_entry(self):
        """캐시 엔트리 생성."""
        entry = CacheEntry(value={"allowed": True}, expires_at=time.time() + 10)

        assert entry.value == {"allowed": True}
        assert entry.expires_at > time.time()
        assert entry.created_at <= time.time()


class TestCacheStats:
    """CacheStats 테스트."""

    def test_initial_stats(self):
        """초기 통계."""
        stats = CacheStats()

        assert stats.hits == 0
        assert stats.misses == 0
        assert stats.invalidations == 0
        assert stats.expirations == 0

    def test_hit_rate_zero(self):
        """조회 없을 때 히트율 0."""
        stats = CacheStats()

        assert stats.hit_rate == 0.0

    def test_hit_rate_calculation(self):
        """히트율 계산."""
        stats = CacheStats(hits=80, misses=20)

        assert stats.hit_rate == 0.8


class TestCBStateCache:
    """CBStateCache 테스트."""

    def test_init_default(self):
        """기본 초기화."""
        cache = CBStateCache(enable_event_invalidation=False)

        assert cache._ttl == CBStateCache.DEFAULT_TTL_SECONDS
        assert cache.size == 0

    def test_init_custom_ttl(self):
        """커스텀 TTL로 초기화."""
        cache = CBStateCache(ttl_seconds=10.0, enable_event_invalidation=False)

        assert cache._ttl == 10.0

    def test_set_and_get(self):
        """캐시 저장 및 조회."""
        cache = CBStateCache(enable_event_invalidation=False)

        cache.set("payment_gateway", {"allowed": True, "state": "closed"})
        value, hit = cache.get("payment_gateway")

        assert hit is True
        assert value == {"allowed": True, "state": "closed"}

    def test_get_nonexistent(self):
        """존재하지 않는 키 조회."""
        cache = CBStateCache(enable_event_invalidation=False)

        value, hit = cache.get("nonexistent")

        assert hit is False
        assert value is None

    def test_get_expired(self):
        """만료된 엔트리 조회."""
        cache = CBStateCache(ttl_seconds=0.01, enable_event_invalidation=False)

        cache.set("test", {"data": "value"})
        time.sleep(0.02)  # TTL 초과

        value, hit = cache.get("test")

        assert hit is False
        assert value is None
        assert cache._stats.expirations == 1

    def test_get_or_set_with_factory(self):
        """get_or_set 팩토리 함수 사용."""
        cache = CBStateCache(enable_event_invalidation=False)
        factory_called = [0]

        def factory():
            factory_called[0] += 1
            return {"data": "new"}

        # 첫 번째 호출 - 팩토리 실행
        result1 = cache.get_or_set("key", factory)
        assert result1 == {"data": "new"}
        assert factory_called[0] == 1

        # 두 번째 호출 - 캐시에서 반환
        result2 = cache.get_or_set("key", factory)
        assert result2 == {"data": "new"}
        assert factory_called[0] == 1  # 팩토리 재호출 안됨

    def test_invalidate(self):
        """특정 키 무효화."""
        cache = CBStateCache(enable_event_invalidation=False)

        cache.set("service1", {"data": 1})
        cache.set("service2", {"data": 2})

        result = cache.invalidate("service1")

        assert result is True
        assert cache.get("service1")[1] is False
        assert cache.get("service2")[1] is True
        assert cache._stats.invalidations == 1

    def test_invalidate_nonexistent(self):
        """존재하지 않는 키 무효화."""
        cache = CBStateCache(enable_event_invalidation=False)

        result = cache.invalidate("nonexistent")

        assert result is False

    def test_invalidate_all(self):
        """전체 캐시 무효화."""
        cache = CBStateCache(enable_event_invalidation=False)

        cache.set("service1", {"data": 1})
        cache.set("service2", {"data": 2})
        cache.set("service3", {"data": 3})

        count = cache.invalidate_all()

        assert count == 3
        assert cache.size == 0

    def test_contains(self):
        """키 존재 확인."""
        cache = CBStateCache(enable_event_invalidation=False)

        cache.set("existing", {"data": True})

        assert cache.contains("existing") is True
        assert cache.contains("nonexistent") is False

    def test_keys(self):
        """캐시된 키 목록."""
        cache = CBStateCache(enable_event_invalidation=False)

        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)

        keys = cache.keys()

        assert set(keys) == {"a", "b", "c"}

    def test_stats(self):
        """캐시 통계."""
        cache = CBStateCache(enable_event_invalidation=False)

        cache.set("key", {"data": True})
        cache.get("key")  # hit
        cache.get("key")  # hit
        cache.get("missing")  # miss

        stats = cache.get_stats_dict()

        assert stats["size"] == 1
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["hit_rate"] == pytest.approx(0.6667, rel=0.01)

    def test_close(self):
        """캐시 종료."""
        cache = CBStateCache(enable_event_invalidation=False)

        cache.set("key1", 1)
        cache.set("key2", 2)

        cache.close()

        assert cache.size == 0


class TestCBStateCacheEventIntegration:
    """EventBus 통합 테스트."""

    def test_event_invalidation_handler_called(self):
        """이벤트 발생 시 무효화 핸들러 호출."""
        cache = CBStateCache(enable_event_invalidation=False)

        # 수동으로 이벤트 핸들러 테스트
        mock_event = MagicMock()
        mock_event.data = {"service_name": "test_service"}
        mock_event.event_type = MagicMock()
        mock_event.event_type.value = "circuit_breaker_opened"

        cache.set("test_service", {"state": "closed"})
        assert cache.contains("test_service")

        cache._on_state_change(mock_event)

        assert not cache.contains("test_service")


class TestCBStateCacheConcurrency:
    """동시성 테스트."""

    def test_concurrent_access(self):
        """동시 접근 테스트."""
        cache = CBStateCache(enable_event_invalidation=False)
        errors = []

        def worker(service_id):
            try:
                for _ in range(100):
                    key = f"service_{service_id}"
                    cache.set(key, {"id": service_id})
                    cache.get(key)
                    cache.invalidate(key)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


class TestGlobalCBStateCache:
    """싱글톤 인스턴스 테스트."""

    def teardown_method(self):
        """테스트 후 싱글톤 리셋."""
        reset_cb_state_cache()

    def test_singleton(self):
        """싱글톤 인스턴스 반환."""
        cache1 = get_cb_state_cache()
        cache2 = get_cb_state_cache()

        assert cache1 is cache2

    def test_reset_singleton(self):
        """싱글톤 리셋."""
        cache1 = get_cb_state_cache()

        reset_cb_state_cache()

        cache2 = get_cb_state_cache()

        assert cache1 is not cache2
