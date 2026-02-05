"""
AsyncSemaphoreBulkhead 단위 테스트.

비동기 세마포어 기반 격벽의 동작을 검증합니다:
- 비동기 획득/반환 동작
- 최대 동시 실행 제한
- asyncio.wait_for 기반 타임아웃
"""

from __future__ import annotations

import asyncio

import pytest

from selfhealing.resilience.bulkhead.async_semaphore import AsyncSemaphoreBulkhead
from selfhealing.resilience.bulkhead.base import BulkheadState, BulkheadType
from selfhealing.resilience.bulkhead.exceptions import BulkheadFullException


class TestAsyncSemaphoreBulkheadBasic:
    """기본 동작 테스트."""

    @pytest.mark.asyncio
    async def test_create_bulkhead_with_default_values(self):
        """기본값으로 격벽 생성."""
        bulkhead = AsyncSemaphoreBulkhead("test")

        assert bulkhead.name == "test"
        state = bulkhead.get_state()
        assert state.max_concurrent == 10
        assert state.active_count == 0
        assert state.bulkhead_type == BulkheadType.SEMAPHORE

    @pytest.mark.asyncio
    async def test_acquire_and_release(self):
        """획득 후 반환."""
        bulkhead = AsyncSemaphoreBulkhead("test", max_concurrent=2)

        async with bulkhead.acquire():
            state = bulkhead.get_state()
            assert state.active_count == 1

        state = bulkhead.get_state()
        assert state.active_count == 0

    @pytest.mark.asyncio
    async def test_nested_acquire(self):
        """중첩 획득."""
        bulkhead = AsyncSemaphoreBulkhead("test", max_concurrent=3)

        async with bulkhead.acquire():
            async with bulkhead.acquire():
                state = bulkhead.get_state()
                assert state.active_count == 2

            state = bulkhead.get_state()
            assert state.active_count == 1

        state = bulkhead.get_state()
        assert state.active_count == 0


class TestAsyncSemaphoreBulkheadConcurrency:
    """동시성 제한 테스트."""

    @pytest.mark.asyncio
    async def test_reject_when_full_no_timeout(self):
        """격벽이 가득 찬 경우 즉시 거부 (timeout=None)."""
        bulkhead = AsyncSemaphoreBulkhead("test", max_concurrent=1)

        async with bulkhead.acquire():
            with pytest.raises(BulkheadFullException) as exc_info:
                async with bulkhead.acquire():
                    pass

            assert exc_info.value.bulkhead_name == "test"
            assert exc_info.value.max_concurrent == 1

    @pytest.mark.asyncio
    async def test_reject_increments_counter(self):
        """거부 시 카운터 증가."""
        bulkhead = AsyncSemaphoreBulkhead("test", max_concurrent=1)

        async with bulkhead.acquire():
            for _ in range(3):
                with pytest.raises(BulkheadFullException):
                    async with bulkhead.acquire():
                        pass

        state = bulkhead.get_state()
        assert state.rejected_count == 3
        assert state.last_rejection_time is not None

    @pytest.mark.asyncio
    async def test_concurrent_tasks_limited(self):
        """동시 태스크 수 제한 검증."""
        bulkhead = AsyncSemaphoreBulkhead("test", max_concurrent=3)
        max_concurrent_observed = 0
        lock = asyncio.Lock()

        async def worker():
            nonlocal max_concurrent_observed
            try:
                async with bulkhead.acquire(timeout=1.0):
                    async with lock:
                        current = bulkhead.get_state().active_count
                        if current > max_concurrent_observed:
                            max_concurrent_observed = current
                    await asyncio.sleep(0.05)
            except BulkheadFullException:
                pass

        # 10개 태스크 동시 실행
        tasks = [asyncio.create_task(worker()) for _ in range(10)]
        await asyncio.gather(*tasks)

        # 최대 동시 실행 수는 3을 넘지 않아야 함
        assert max_concurrent_observed <= 3


class TestAsyncSemaphoreBulkheadTimeout:
    """타임아웃 테스트."""

    @pytest.mark.asyncio
    async def test_acquire_with_timeout_success(self):
        """타임아웃 내에 획득 성공."""
        bulkhead = AsyncSemaphoreBulkhead("test", max_concurrent=1)

        # 백그라운드에서 잠시 후 release
        async def release_after_delay():
            await asyncio.sleep(0.1)
            await bulkhead.release()

        # 첫 번째 획득
        acquired = await bulkhead.try_acquire()
        assert acquired

        # 백그라운드 태스크 시작
        release_task = asyncio.create_task(release_after_delay())

        # 타임아웃 내에 획득 시도
        async with bulkhead.acquire(timeout=1.0):
            pass

        await release_task

    @pytest.mark.asyncio
    async def test_acquire_with_timeout_failure(self):
        """타임아웃 초과 시 실패."""
        bulkhead = AsyncSemaphoreBulkhead("test", max_concurrent=1)

        async with bulkhead.acquire():
            with pytest.raises(BulkheadFullException):
                async with bulkhead.acquire(timeout=0.05):
                    pass


class TestAsyncSemaphoreBulkheadState:
    """상태 조회 테스트."""

    @pytest.mark.asyncio
    async def test_get_state_returns_correct_values(self):
        """상태 조회가 올바른 값 반환."""
        bulkhead = AsyncSemaphoreBulkhead("test", max_concurrent=5)

        state = bulkhead.get_state()
        assert isinstance(state, BulkheadState)
        assert state.name == "test"
        assert state.bulkhead_type == BulkheadType.SEMAPHORE
        assert state.max_concurrent == 5

    @pytest.mark.asyncio
    async def test_utilization_percent_calculation(self):
        """사용률 계산."""
        bulkhead = AsyncSemaphoreBulkhead("test", max_concurrent=4)

        state = bulkhead.get_state()
        assert state.utilization_percent == 0.0

        async with bulkhead.acquire():
            async with bulkhead.acquire():
                state = bulkhead.get_state()
                assert state.utilization_percent == 50.0  # 2/4 = 50%


class TestAsyncSemaphoreBulkheadTryAcquire:
    """try_acquire 메서드 테스트."""

    @pytest.mark.asyncio
    async def test_try_acquire_success(self):
        """try_acquire 성공."""
        bulkhead = AsyncSemaphoreBulkhead("test", max_concurrent=2)

        result = await bulkhead.try_acquire()
        assert result is True
        state = bulkhead.get_state()
        assert state.active_count == 1

        # 정리
        await bulkhead.release()

    @pytest.mark.asyncio
    async def test_try_acquire_failure_when_full(self):
        """격벽이 가득 찬 경우 try_acquire 실패."""
        bulkhead = AsyncSemaphoreBulkhead("test", max_concurrent=1)

        result = await bulkhead.try_acquire()
        assert result is True

        # 두 번째 시도는 실패
        result = await bulkhead.try_acquire()
        assert result is False

        # 정리
        await bulkhead.release()
