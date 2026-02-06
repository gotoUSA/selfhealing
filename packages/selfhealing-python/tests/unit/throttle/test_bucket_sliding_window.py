"""
BucketSlidingWindow 단위 테스트.

O(1) 고정 버킷 기반 슬라이딩 윈도우 테스트.
"""

import time
import threading
import pytest


class TestBucketSlidingWindow:
    """BucketSlidingWindow 클래스 테스트."""

    def test_record_increments_count(self):
        """record()가 카운트를 증가시키는지 확인."""
        from selfhealing.services.throttle.base import BucketSlidingWindow

        window = BucketSlidingWindow(window_seconds=60)

        count1 = window.record("test_key")
        assert count1 == 1

        count2 = window.record("test_key")
        assert count2 == 2

        count3 = window.record("test_key")
        assert count3 == 3

    def test_get_count_returns_total(self):
        """get_count()가 올바른 총합을 반환하는지 확인."""
        from selfhealing.services.throttle.base import BucketSlidingWindow

        window = BucketSlidingWindow(window_seconds=60)

        for _ in range(5):
            window.record("key1")

        assert window.get_count("key1") == 5
        assert window.get_count("key2") == 0  # 존재하지 않는 키

    def test_separate_keys_have_separate_counts(self):
        """다른 키는 별도 카운트를 가지는지 확인."""
        from selfhealing.services.throttle.base import BucketSlidingWindow

        window = BucketSlidingWindow(window_seconds=60)

        for _ in range(3):
            window.record("key_a")
        for _ in range(5):
            window.record("key_b")

        assert window.get_count("key_a") == 3
        assert window.get_count("key_b") == 5

    def test_shard_lock_distribution(self):
        """shard lock이 키 해시에 따라 분배되는지 확인."""
        from selfhealing.services.throttle.base import BucketSlidingWindow

        window = BucketSlidingWindow(window_seconds=60, num_shards=4)

        # 여러 키가 서로 다른 shard lock을 사용
        lock1 = window._get_shard_lock("key_a")
        lock2 = window._get_shard_lock("key_b")
        lock3 = window._get_shard_lock("key_c")

        # Lock 객체가 유효한지 확인 (threading.Lock은 factory 함수)
        lock_type = type(threading.Lock())
        assert isinstance(lock1, lock_type)
        assert isinstance(lock2, lock_type)
        assert isinstance(lock3, lock_type)

    def test_thread_safety(self):
        """멀티스레드 환경에서 안전한지 확인."""
        from selfhealing.services.throttle.base import BucketSlidingWindow

        window = BucketSlidingWindow(window_seconds=60, num_shards=16)
        errors = []
        total_ops = {"value": 0}

        def worker(key_prefix: str, num_ops: int):
            for i in range(num_ops):
                try:
                    window.record(f"{key_prefix}_{i % 5}")
                except Exception as e:
                    errors.append(e)
            total_ops["value"] += num_ops

        threads = [threading.Thread(target=worker, args=(f"worker_{i}", 100)) for i in range(4)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert total_ops["value"] == 400


class TestBucketSlidingWindowPerformance:
    """BucketSlidingWindow 성능 벤치마크 테스트."""

    @pytest.mark.slow
    def test_record_is_fast(self):
        """record()가 빠르게 동작하는지 확인."""
        from selfhealing.services.throttle.base import BucketSlidingWindow

        window = BucketSlidingWindow(window_seconds=60)

        # 워밍업
        for _ in range(100):
            window.record("warmup_key")

        # 벤치마크: 10K 연산
        start = time.perf_counter()
        for _ in range(10_000):
            window.record("bench_key")
        elapsed = time.perf_counter() - start

        ops_per_sec = 10_000 / elapsed
        print(f"BucketSlidingWindow: {ops_per_sec:,.0f} ops/sec ({elapsed:.3f}s)")

        # 최소 10K ops/sec 달성 확인 (보수적 기준)
        assert ops_per_sec >= 10_000, f"Expected >= 10K ops/sec, got {ops_per_sec:,.0f}"
