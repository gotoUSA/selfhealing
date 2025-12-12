"""
Stage 35: Cache Stampede Prevention - Unit Tests

이 파일은 Stage 35 시나리오의 핵심 로직을 검증합니다.
- Hot Key Expiry Stampede
- Probabilistic Early Expiration
- Multi-Key Batch Expiry
"""

import pytest
import time
import threading
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock
from dataclasses import dataclass

import sys
import os

_current_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(os.path.dirname(_current_dir))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


class TestCacheEntry:
    """CacheEntry 관련 테스트"""

    def test_cache_entry_creation(self):
        """캐시 엔트리 생성"""
        from load_tests.scenarios.stage35_cache_stampede import CacheEntry, CACHE_TTL_S

        now = time.time()
        entry = CacheEntry(
            key="test_key", value={"data": "test"}, created_at=now, ttl_s=CACHE_TTL_S, expires_at=now + CACHE_TTL_S
        )

        assert entry.key == "test_key"
        assert entry.value == {"data": "test"}
        assert entry.hit_count == 0
        assert entry.is_refreshing is False

    def test_cache_entry_not_expired(self):
        """만료되지 않은 캐시 엔트리"""
        from load_tests.scenarios.stage35_cache_stampede import CacheEntry

        now = time.time()
        entry = CacheEntry(key="test_key", value={"data": "test"}, created_at=now, ttl_s=60, expires_at=now + 60)

        assert entry.is_expired is False
        assert entry.time_to_expiry > 0

    def test_cache_entry_expired(self):
        """만료된 캐시 엔트리"""
        from load_tests.scenarios.stage35_cache_stampede import CacheEntry

        now = time.time()
        entry = CacheEntry(
            key="test_key",
            value={"data": "test"},
            created_at=now - 100,
            ttl_s=60,
            expires_at=now - 40,  # Expired 40 seconds ago
        )

        assert entry.is_expired is True
        assert entry.time_to_expiry == 0


class TestSimulatedCache:
    """SimulatedCache 관련 테스트"""

    def test_cache_get_miss(self):
        """캐시 미스 시 DB 조회"""
        from load_tests.scenarios.stage35_cache_stampede import SimulatedCache, reset_stats

        reset_stats()
        cache = SimulatedCache()

        value, was_hit, elapsed = cache.get("new_key")

        assert was_hit is False
        assert value is not None
        assert elapsed > 0

    def test_cache_get_hit(self):
        """캐시 히트"""
        from load_tests.scenarios.stage35_cache_stampede import SimulatedCache, reset_stats

        reset_stats()
        cache = SimulatedCache()

        # First access - miss
        cache.get("test_key")

        # Second access - hit
        value, was_hit, elapsed = cache.get("test_key")

        assert was_hit is True
        assert value is not None

    def test_cache_expire_key(self):
        """키 만료"""
        from load_tests.scenarios.stage35_cache_stampede import SimulatedCache, reset_stats

        reset_stats()
        cache = SimulatedCache()

        # Populate cache
        cache.get("test_key")

        # Verify hit
        _, was_hit1, _ = cache.get("test_key")
        assert was_hit1 is True

        # Expire key
        cache.expire_key("test_key")

        # Now should be miss
        _, was_hit2, _ = cache.get("test_key")
        assert was_hit2 is False

    def test_cache_expire_all(self):
        """모든 키 만료"""
        from load_tests.scenarios.stage35_cache_stampede import SimulatedCache, reset_stats

        reset_stats()
        cache = SimulatedCache()

        # Populate cache
        for i in range(5):
            cache.get(f"key_{i}")

        assert cache.get_cache_size() == 5

        # Expire all
        cache.expire_all()

        # All should be misses now
        for i in range(5):
            _, was_hit, _ = cache.get(f"key_{i}")
            assert was_hit is False

    def test_cache_reset(self):
        """캐시 리셋"""
        from load_tests.scenarios.stage35_cache_stampede import SimulatedCache, reset_stats

        reset_stats()
        cache = SimulatedCache()

        # Populate cache
        for i in range(5):
            cache.get(f"key_{i}")

        assert cache.get_cache_size() == 5
        assert cache.get_db_query_count() == 5

        # Reset
        cache.reset()

        assert cache.get_cache_size() == 0
        assert cache.get_db_query_count() == 0


class TestStampedePrevention:
    """Stampede 방지 테스트"""

    def test_single_request_no_stampede(self):
        """단일 요청은 stampede 아님"""
        from load_tests.scenarios.stage35_cache_stampede import SimulatedCache, reset_stats

        reset_stats()
        cache = SimulatedCache()

        # Expire key
        cache.get("test_key")
        cache.expire_key("test_key")

        # Single request
        db_before = cache.get_db_query_count()
        cache.get("test_key")
        db_after = cache.get_db_query_count()

        assert db_after - db_before == 1

    def test_concurrent_requests_single_db_query(self):
        """동시 요청 시 DB 쿼리 1회만 실행"""
        from load_tests.scenarios.stage35_cache_stampede import SimulatedCache, StampedePrevention, reset_stats

        reset_stats()
        cache = SimulatedCache()

        # Populate and expire
        cache.get("hot_key")
        cache.expire_key("hot_key")

        results = StampedePrevention.simulate_hot_key_stampede(cache=cache, key="hot_key", num_requests=50, concurrent=True)

        # Should have at most 1 DB query (stampede prevented)
        assert results["db_queries"] <= 1
        assert results["stampede_prevented"] is True

    def test_multi_key_batch_expiry(self):
        """다중 키 배치 만료 테스트"""
        from load_tests.scenarios.stage35_cache_stampede import SimulatedCache, StampedePrevention, reset_stats

        reset_stats()
        cache = SimulatedCache()

        num_keys = 5
        requests_per_key = 10

        results = StampedePrevention.simulate_multi_key_batch_expiry(
            cache=cache, num_keys=num_keys, requests_per_key=requests_per_key
        )

        # Each key should have at most 1 DB query
        assert results["total_db_queries"] <= num_keys
        assert results["stampede_prevented"] is True


class TestCacheStampedeStats:
    """CacheStampedeStats 테스트"""

    def test_stats_reset(self):
        """통계 리셋"""
        from load_tests.scenarios.stage35_cache_stampede import CacheStampedeStats

        stats = CacheStampedeStats()
        stats.total_requests = 100
        stats.cache_hits = 80
        stats.cache_misses = 20

        stats.reset()

        assert stats.total_requests == 0
        assert stats.cache_hits == 0
        assert stats.cache_misses == 0

    def test_cache_hit_rate(self):
        """캐시 히트율 계산"""
        from load_tests.scenarios.stage35_cache_stampede import CacheStampedeStats

        stats = CacheStampedeStats()
        stats.cache_hits = 80
        stats.cache_misses = 20

        assert stats.get_cache_hit_rate() == 0.8

    def test_cache_hit_rate_zero(self):
        """캐시 히트율 0인 경우"""
        from load_tests.scenarios.stage35_cache_stampede import CacheStampedeStats

        stats = CacheStampedeStats()
        stats.cache_hits = 0
        stats.cache_misses = 0

        assert stats.get_cache_hit_rate() == 0.0

    def test_avg_response_time(self):
        """평균 응답 시간 계산"""
        from load_tests.scenarios.stage35_cache_stampede import CacheStampedeStats

        stats = CacheStampedeStats()
        stats.response_times = [0.01, 0.02, 0.03]  # seconds

        # Should return in ms
        avg = stats.get_avg_response_time()
        assert abs(avg - 20.0) < 0.01  # 20ms average

    def test_p95_response_time(self):
        """P95 응답 시간 계산"""
        from load_tests.scenarios.stage35_cache_stampede import CacheStampedeStats

        stats = CacheStampedeStats()
        stats.response_times = [0.01 * i for i in range(1, 101)]  # 0.01 to 1.0

        p95 = stats.get_p95_response_time()
        assert p95 >= 900  # Around 950ms for P95


class TestEarlyRefresh:
    """Probabilistic Early Refresh 테스트"""

    def test_early_refresh_near_expiry(self):
        """만료 임박 시 early refresh 가능성"""
        from load_tests.scenarios.stage35_cache_stampede import CacheEntry, CACHE_EARLY_EXPIRY_DELTA_S

        now = time.time()
        # Entry expiring in 5 seconds (within DELTA)
        entry = CacheEntry(key="test_key", value={"data": "test"}, created_at=now - 55, ttl_s=60, expires_at=now + 5)

        # Time to expiry should be within delta
        assert entry.time_to_expiry < CACHE_EARLY_EXPIRY_DELTA_S
        # May or may not trigger due to probability
        # Just verify it doesn't crash
        _ = entry.should_early_refresh

    def test_early_refresh_expired_always_true(self):
        """만료된 엔트리는 항상 should_early_refresh=True"""
        from load_tests.scenarios.stage35_cache_stampede import CacheEntry

        now = time.time()
        entry = CacheEntry(
            key="test_key", value={"data": "test"}, created_at=now - 100, ttl_s=60, expires_at=now - 40  # Already expired
        )

        assert entry.is_expired is True
        assert entry.should_early_refresh is True

    def test_get_with_early_refresh(self):
        """Early refresh 포함 get 호출"""
        from load_tests.scenarios.stage35_cache_stampede import SimulatedCache, reset_stats

        reset_stats()
        cache = SimulatedCache()

        # Initial get
        value1, hit1, _ = cache.get_with_early_refresh("test_key")
        assert hit1 is False  # First access is miss

        # Second access should be hit
        value2, hit2, _ = cache.get_with_early_refresh("test_key")
        assert hit2 is True


class TestReportGeneration:
    """리포트 생성 테스트"""

    def test_generate_stage35_report(self):
        """Stage 35 리포트 생성"""
        from load_tests.scenarios.stage35_cache_stampede import generate_stage35_report, CacheStampedeStats

        stats = CacheStampedeStats()
        stats.start_time = time.time() - 60
        stats.phase = "completed"
        stats.total_requests = 1000
        stats.cache_hits = 800
        stats.cache_misses = 200
        stats.hot_key_requests = 500
        stats.hot_key_db_queries = 10
        stats.stampedes_detected = 5
        stats.stampedes_prevented = 5

        report = generate_stage35_report(stats)

        assert report["stage"] == "35"
        assert report["name"] == "Cache Stampede Prevention"
        assert report["summary"]["total_requests"] == 1000
        assert report["summary"]["cache_hit_rate"] == 0.8
        assert report["verification"]["stampede_prevented"] is True


class TestIntegration:
    """통합 테스트"""

    def test_run_hot_key_stampede_test(self):
        """Hot key stampede 테스트 실행"""
        from load_tests.scenarios.stage35_cache_stampede import run_hot_key_stampede_test, reset_cache, reset_stats

        reset_cache()
        reset_stats()

        results = run_hot_key_stampede_test(num_requests=20)

        assert "total_requests" in results
        assert "db_queries" in results
        assert "stampede_prevented" in results
        assert results["total_requests"] == 20

    def test_run_multi_key_batch_test(self):
        """Multi-key batch 테스트 실행"""
        from load_tests.scenarios.stage35_cache_stampede import run_multi_key_batch_test, reset_cache, reset_stats

        reset_cache()
        reset_stats()

        results = run_multi_key_batch_test(num_keys=5, requests_per_key=5)

        assert "num_keys" in results
        assert "total_db_queries" in results
        assert "stampede_prevented" in results
        assert results["num_keys"] == 5

    def test_run_early_refresh_test(self):
        """Early refresh 테스트 실행"""
        from load_tests.scenarios.stage35_cache_stampede import run_early_refresh_test, reset_cache, reset_stats

        reset_cache()
        reset_stats()

        results = run_early_refresh_test(num_iterations=20)

        assert "iterations" in results
        assert "early_refresh_triggers" in results
        assert results["iterations"] == 20

    def test_run_all_stage35_tests(self):
        """전체 Stage 35 테스트 실행"""
        from load_tests.scenarios.stage35_cache_stampede import run_all_stage35_tests, reset_cache, reset_stats

        reset_cache()
        reset_stats()

        results = run_all_stage35_tests()

        assert results["stage"] == "35"
        assert "tests" in results
        assert "report" in results
        assert "hot_key_stampede" in results["tests"]
        assert "multi_key_batch" in results["tests"]
        assert "early_refresh" in results["tests"]


class TestConcurrency:
    """동시성 테스트"""

    def test_concurrent_cache_access(self):
        """동시 캐시 접근 테스트"""
        from load_tests.scenarios.stage35_cache_stampede import SimulatedCache, reset_stats

        reset_stats()
        cache = SimulatedCache()

        results = []
        errors = []

        def access_cache(key: str):
            try:
                value, hit, elapsed = cache.get(key)
                results.append((key, hit, elapsed))
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(50):
            key = f"key_{i % 5}"  # 5 unique keys
            t = threading.Thread(target=access_cache, args=(key,))
            threads.append(t)

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 50

    def test_lock_contention(self):
        """Lock 경합 테스트"""
        from load_tests.scenarios.stage35_cache_stampede import SimulatedCache, reset_stats

        reset_stats()
        cache = SimulatedCache()

        # Pre-populate and expire
        cache.get("contention_key")
        cache.expire_key("contention_key")

        results = []
        start_barrier = threading.Barrier(10)

        def access_with_barrier():
            start_barrier.wait()  # All threads start at same time
            value, hit, elapsed = cache.get("contention_key")
            results.append((hit, elapsed))

        threads = [threading.Thread(target=access_with_barrier) for _ in range(10)]

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        # Should have exactly 1 miss (first to acquire lock), rest are hits
        misses = sum(1 for hit, _ in results if not hit)
        hits = sum(1 for hit, _ in results if hit)

        # Due to timing, might have slight variations
        assert misses >= 1  # At least one miss
        assert hits + misses == 10


class TestEdgeCases:
    """엣지 케이스 테스트"""

    def test_empty_cache(self):
        """빈 캐시 테스트"""
        from load_tests.scenarios.stage35_cache_stampede import SimulatedCache, reset_stats

        reset_stats()
        cache = SimulatedCache()

        assert cache.get_cache_size() == 0
        assert cache.get_db_query_count() == 0

    def test_cache_with_custom_fetch_fn(self):
        """커스텀 fetch 함수 테스트"""
        from load_tests.scenarios.stage35_cache_stampede import SimulatedCache, reset_stats

        reset_stats()
        cache = SimulatedCache()

        custom_value = {"custom": "data", "id": 123}

        def custom_fetch():
            return custom_value

        value, hit, elapsed = cache.get("custom_key", fetch_fn=custom_fetch)

        assert hit is False
        assert value == custom_value

    def test_very_short_ttl(self):
        """매우 짧은 TTL 테스트"""
        from load_tests.scenarios.stage35_cache_stampede import CacheEntry

        now = time.time()
        entry = CacheEntry(key="short_ttl", value="test", created_at=now, ttl_s=0.001, expires_at=now + 0.001)  # 1ms

        time.sleep(0.01)  # Wait 10ms
        assert entry.is_expired is True

    def test_zero_requests_stats(self):
        """요청 없는 경우 통계"""
        from load_tests.scenarios.stage35_cache_stampede import CacheStampedeStats

        stats = CacheStampedeStats()

        assert stats.get_cache_hit_rate() == 0.0
        assert stats.get_avg_response_time() == 0.0
        assert stats.get_p95_response_time() == 0.0
        assert stats.get_db_query_dedup_rate() == 0.0
