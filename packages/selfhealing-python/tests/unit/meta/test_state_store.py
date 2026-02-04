"""
WatchdogStateStore 테스트.

Redis 기반 상태 저장소 테스트.
"""

import threading
from datetime import datetime, timezone
from unittest import mock

import pytest

from selfhealing.meta.state_store import (
    WatchdogStateStore,
    get_watchdog_state_store,
    reset_watchdog_state_store,
)


class TestWatchdogStateStore:
    """WatchdogStateStore 테스트."""

    @pytest.fixture
    def store(self):
        """Store fixture."""
        return WatchdogStateStore()

    def test_initialization(self, store):
        """초기화 테스트."""
        assert store is not None
        assert store._local_failures == {}
        assert store._local_cooldowns == {}

    def test_get_failure_count_default(self, store):
        """기본 실패 횟수 테스트 (0)."""
        count = store.get_failure_count("unknown")
        assert count == 0

    def test_increment_failure_count(self, store):
        """실패 횟수 증가 테스트."""
        store.increment_failure_count("redis")
        store.increment_failure_count("redis")
        store.increment_failure_count("redis")

        count = store.get_failure_count("redis")
        assert count == 3

    def test_reset_failure_count(self, store):
        """실패 횟수 리셋 테스트."""
        store.increment_failure_count("redis")
        store.increment_failure_count("redis")

        store.reset_failure_count("redis")

        count = store.get_failure_count("redis")
        assert count == 0

    def test_reset_all_failure_counts(self, store):
        """모든 실패 횟수 리셋 테스트."""
        store.increment_failure_count("redis")
        store.increment_failure_count("postgres")
        store.increment_failure_count("celery")

        store.reset_all_failure_counts()

        assert store.get_failure_count("redis") == 0
        assert store.get_failure_count("postgres") == 0
        assert store.get_failure_count("celery") == 0


class TestEscalationCooldown:
    """에스컬레이션 쿨다운 테스트."""

    @pytest.fixture
    def store(self):
        """Store fixture."""
        return WatchdogStateStore()

    def test_get_last_escalation_time_default(self, store):
        """기본 에스컬레이션 시각 (0)."""
        time = store.get_last_escalation_time("unknown")
        assert time == 0

    def test_record_escalation(self, store):
        """에스컬레이션 기록 테스트."""
        store.record_escalation("redis")

        time = store.get_last_escalation_time("redis")
        assert time > 0

    def test_can_escalate_no_prior(self, store):
        """이전 에스컬레이션 없을 때 가능."""
        can = store.can_escalate("new_component", cooldown_seconds=3600)
        assert can is True

    def test_can_escalate_within_cooldown(self, store):
        """쿨다운 내 에스컬레이션 불가."""
        store.record_escalation("redis")

        can = store.can_escalate("redis", cooldown_seconds=3600)
        assert can is False

    def test_can_escalate_after_cooldown(self, store):
        """쿨다운 후 에스컬레이션 가능."""
        # 과거 시간으로 기록
        import time as time_module

        store._local_cooldowns["redis"] = time_module.time() - 7200  # 2시간 전

        can = store.can_escalate("redis", cooldown_seconds=3600)
        assert can is True

    def test_reset_escalation_cooldown(self, store):
        """에스컬레이션 쿨다운 리셋 테스트."""
        store.record_escalation("redis")
        store.reset_escalation_cooldown("redis")

        can = store.can_escalate("redis", cooldown_seconds=3600)
        assert can is True


class TestLastLoopTimestamp:
    """마지막 루프 타임스탬프 테스트 (Liveness)."""

    @pytest.fixture
    def store(self):
        """Store fixture."""
        return WatchdogStateStore()

    def test_get_last_loop_timestamp_default(self, store):
        """기본 타임스탬프 (None)."""
        timestamp = store.get_last_loop_timestamp()
        assert timestamp is None

    def test_update_last_loop_timestamp(self, store):
        """타임스탬프 갱신 테스트."""
        store.update_last_loop_timestamp()

        timestamp = store.get_last_loop_timestamp()
        assert timestamp is not None
        assert isinstance(timestamp, datetime)

    def test_get_last_loop_age_seconds_no_record(self, store):
        """기록 없을 때 무한대."""
        age = store.get_last_loop_age_seconds()
        assert age == float("inf")

    def test_get_last_loop_age_seconds(self, store):
        """경과 시간 테스트."""
        store.update_last_loop_timestamp()

        age = store.get_last_loop_age_seconds()
        assert age >= 0
        assert age < 1  # 방금 갱신했으므로 1초 미만


class TestDistributedLock:
    """분산 락 테스트."""

    @pytest.fixture
    def store(self):
        """Store fixture."""
        return WatchdogStateStore()

    def test_acquire_lock_no_redis(self, store):
        """Redis 없을 때 락 획득 (항상 True)."""
        acquired = store.acquire_escalation_lock("test", lock_ttl_seconds=30)
        assert acquired is True

    def test_release_lock_no_redis(self, store):
        """Redis 없을 때 락 해제."""
        # 예외 없이 완료되어야 함
        store.release_escalation_lock("test")


class TestClearAll:
    """전체 초기화 테스트."""

    @pytest.fixture
    def store(self):
        """Store fixture."""
        return WatchdogStateStore()

    def test_clear_all(self, store):
        """모든 상태 초기화 테스트."""
        store.increment_failure_count("redis")
        store.record_escalation("redis")
        store.update_last_loop_timestamp()

        store.clear_all()

        assert store.get_failure_count("redis") == 0
        assert store.get_last_escalation_time("redis") == 0
        assert store.get_last_loop_timestamp() is None


class TestThreadSafety:
    """스레드 안전성 테스트."""

    def test_concurrent_increment(self):
        """동시 증가 테스트."""
        store = WatchdogStateStore()

        def increment():
            for _ in range(100):
                store.increment_failure_count("test")

        threads = [threading.Thread(target=increment) for _ in range(10)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 1000번 증가
        assert store.get_failure_count("test") == 1000


class TestSingleton:
    """싱글톤 테스트."""

    def test_singleton_returns_same_instance(self):
        """싱글톤 인스턴스 반환 테스트."""
        reset_watchdog_state_store()

        store1 = get_watchdog_state_store()
        store2 = get_watchdog_state_store()

        assert store1 is store2

        reset_watchdog_state_store()

    def test_reset_clears_singleton(self):
        """싱글톤 리셋 테스트."""
        reset_watchdog_state_store()

        store1 = get_watchdog_state_store()
        reset_watchdog_state_store()
        store2 = get_watchdog_state_store()

        assert store1 is not store2

        reset_watchdog_state_store()


class TestRedisIntegration:
    """Redis 통합 테스트 (Mock)."""

    def test_get_failure_count_from_redis(self):
        """Redis에서 실패 횟수 조회 (Mock)."""
        store = WatchdogStateStore()

        mock_redis = mock.MagicMock()
        mock_redis.hget.return_value = b"5"
        store._redis = mock_redis

        count = store.get_failure_count("redis")
        assert count == 5

    def test_increment_failure_count_redis(self):
        """Redis 실패 횟수 증가 (Mock)."""
        store = WatchdogStateStore()

        mock_redis = mock.MagicMock()
        mock_redis.hincrby.return_value = 3
        store._redis = mock_redis

        new_count = store.increment_failure_count("redis")
        assert new_count == 3

    def test_acquire_lock_redis_success(self):
        """Redis 락 획득 성공 (Mock)."""
        store = WatchdogStateStore()

        mock_redis = mock.MagicMock()
        mock_redis.set.return_value = True
        store._redis = mock_redis

        acquired = store.acquire_escalation_lock("test", lock_ttl_seconds=30)
        assert acquired is True

    def test_acquire_lock_redis_failed(self):
        """Redis 락 획득 실패 (Mock)."""
        store = WatchdogStateStore()

        mock_redis = mock.MagicMock()
        mock_redis.set.return_value = False
        store._redis = mock_redis

        acquired = store.acquire_escalation_lock("test", lock_ttl_seconds=30)
        assert acquired is False
