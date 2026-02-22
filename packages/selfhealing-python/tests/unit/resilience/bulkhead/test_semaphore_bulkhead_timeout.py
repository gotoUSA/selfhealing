"""
SemaphoreBulkhead try_acquire() timeout 파라미터 단위 테스트.

테스트 항목:
- 동작: timeout=None이면 논블로킹 즉시 시도
- 동작: timeout 지정 시 대기 후 획득
- 동작: timeout 만료 시 False 반환
"""

import threading
import time

from selfhealing.resilience.bulkhead.semaphore import SemaphoreBulkhead


class TestTryAcquireTimeoutBehavior:
    """try_acquire() timeout 파라미터 동작 검증."""

    def test_none_timeout_succeeds_immediately(self):
        """timeout=None이면 슬롯 여유 시 즉시 True를 반환한다."""
        bulkhead = SemaphoreBulkhead("test", max_concurrent=1)
        assert bulkhead.try_acquire(timeout=None) is True
        bulkhead.release()

    def test_none_timeout_fails_immediately_when_full(self):
        """timeout=None이면 슬롯 만석 시 대기 없이 즉시 False를 반환한다."""
        bulkhead = SemaphoreBulkhead("test", max_concurrent=1)
        bulkhead.try_acquire()  # 슬롯 점유

        start = time.monotonic()
        result = bulkhead.try_acquire(timeout=None)
        elapsed = time.monotonic() - start

        assert result is False
        assert elapsed < 0.05  # 즉시 반환 확인
        bulkhead.release()

    def test_timeout_waits_and_succeeds(self):
        """timeout 지정 시 슬롯이 해제되면 대기 후 획득한다."""
        bulkhead = SemaphoreBulkhead("test", max_concurrent=1)
        bulkhead.try_acquire()  # 슬롯 점유

        def release_later():
            time.sleep(0.02)
            bulkhead.release()

        t = threading.Thread(target=release_later)
        t.start()

        result = bulkhead.try_acquire(timeout=0.5)
        assert result is True
        bulkhead.release()
        t.join()

    def test_timeout_expires_returns_false(self):
        """timeout 내 슬롯이 해제되지 않으면 False를 반환한다."""
        bulkhead = SemaphoreBulkhead("test", max_concurrent=1)
        bulkhead.try_acquire()  # 슬롯 점유

        start = time.monotonic()
        result = bulkhead.try_acquire(timeout=0.05)
        elapsed = time.monotonic() - start

        assert result is False
        assert elapsed >= 0.04  # timeout만큼 대기 확인
        bulkhead.release()
