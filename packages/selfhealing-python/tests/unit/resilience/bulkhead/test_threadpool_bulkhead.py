"""
ThreadPoolBulkhead 단위 테스트.

스레드 풀 기반 격벽의 동작을 검증합니다:
- 독립 스레드 풀에서의 작업 실행
- ContextVar 전파
- 대기 큐 제한
- 타임아웃 처리
"""

from __future__ import annotations

import contextvars
import threading
import time

import pytest

from selfhealing.resilience.bulkhead.base import BulkheadState, BulkheadType
from selfhealing.resilience.bulkhead.exceptions import (
    BulkheadFullException,
    BulkheadTimeoutException,
)
from selfhealing.resilience.bulkhead.threadpool import ThreadPoolBulkhead


# 테스트용 ContextVar
test_context_var: contextvars.ContextVar[str] = contextvars.ContextVar("test_context", default="default")


class TestThreadPoolBulkheadBasic:
    """기본 동작 테스트."""

    def test_create_bulkhead_with_default_values(self):
        """기본값으로 격벽 생성."""
        bulkhead = ThreadPoolBulkhead("test")

        assert bulkhead.name == "test"
        state = bulkhead.get_state()
        assert state.max_concurrent == 5  # 기본 max_workers
        assert state.active_count == 0
        assert state.bulkhead_type == BulkheadType.THREAD_POOL

        bulkhead.shutdown()

    def test_create_bulkhead_with_custom_workers(self):
        """커스텀 워커 수로 격벽 생성."""
        bulkhead = ThreadPoolBulkhead("test", max_workers=10, queue_size=5)

        state = bulkhead.get_state()
        assert state.max_concurrent == 10

        bulkhead.shutdown()

    def test_submit_and_get_result(self):
        """작업 제출 및 결과 획득."""
        bulkhead = ThreadPoolBulkhead("test", max_workers=2)

        def work():
            return "result"

        future = bulkhead.submit(work)
        result = future.result(timeout=5.0)
        assert result == "result"

        bulkhead.shutdown()

    def test_execute_sync(self):
        """동기 실행."""
        bulkhead = ThreadPoolBulkhead("test", max_workers=2)

        def work(x: int, y: int) -> int:
            return x + y

        result = bulkhead.execute(work, 2, 3, timeout=5.0)
        assert result == 5

        bulkhead.shutdown()


class TestThreadPoolBulkheadContextVarPropagation:
    """ContextVar 전파 테스트."""

    def test_context_var_propagated_to_worker(self):
        """ContextVar가 워커 스레드로 전파됨."""
        bulkhead = ThreadPoolBulkhead("test", max_workers=2)

        # 부모 스레드에서 ContextVar 설정
        test_context_var.set("parent_value")

        def check_context():
            # 워커 스레드에서 ContextVar 확인
            return test_context_var.get()

        future = bulkhead.submit(check_context)
        result = future.result(timeout=5.0)

        assert result == "parent_value"

        bulkhead.shutdown()

    def test_context_var_isolated_between_calls(self):
        """각 호출의 ContextVar가 격리됨."""
        bulkhead = ThreadPoolBulkhead("test", max_workers=2)
        results = []

        def check_context():
            return test_context_var.get()

        # 첫 번째 호출
        test_context_var.set("value1")
        future1 = bulkhead.submit(check_context)

        # 두 번째 호출 (다른 컨텍스트)
        test_context_var.set("value2")
        future2 = bulkhead.submit(check_context)

        results.append(future1.result(timeout=5.0))
        results.append(future2.result(timeout=5.0))

        # 각 호출이 자신의 컨텍스트를 유지
        assert results[0] == "value1"
        assert results[1] == "value2"

        bulkhead.shutdown()


class TestThreadPoolBulkheadQueueLimit:
    """대기 큐 제한 테스트."""

    def test_reject_when_queue_full(self):
        """대기 큐가 가득 찬 경우 거부."""
        bulkhead = ThreadPoolBulkhead("test", max_workers=1, queue_size=1)

        # 긴 작업 제출
        def slow_work():
            time.sleep(1.0)

        # 첫 번째: 워커에서 실행
        future1 = bulkhead.submit(slow_work)

        # 두 번째: 큐에서 대기
        future2 = bulkhead.submit(slow_work)

        # 세 번째: 큐가 가득 차서 거부
        with pytest.raises(BulkheadFullException) as exc_info:
            bulkhead.submit(slow_work)

        assert exc_info.value.bulkhead_name == "test"

        # 정리
        future1.cancel()
        future2.cancel()
        bulkhead.shutdown(wait=False)


class TestThreadPoolBulkheadTimeout:
    """타임아웃 테스트."""

    def test_execute_timeout(self):
        """execute 타임아웃."""
        bulkhead = ThreadPoolBulkhead("test", max_workers=2)

        def slow_work():
            time.sleep(5.0)
            return "done"

        with pytest.raises(BulkheadTimeoutException) as exc_info:
            bulkhead.execute(slow_work, timeout=0.1)

        assert exc_info.value.bulkhead_name == "test"
        assert exc_info.value.timeout == 0.1

        bulkhead.shutdown(wait=False)


class TestThreadPoolBulkheadState:
    """상태 조회 테스트."""

    def test_get_state_returns_correct_values(self):
        """상태 조회가 올바른 값 반환."""
        bulkhead = ThreadPoolBulkhead("test", max_workers=3, queue_size=5)

        state = bulkhead.get_state()
        assert isinstance(state, BulkheadState)
        assert state.name == "test"
        assert state.bulkhead_type == BulkheadType.THREAD_POOL
        assert state.max_concurrent == 3

        bulkhead.shutdown()

    def test_active_count_tracking(self):
        """활성 작업 수 추적."""
        bulkhead = ThreadPoolBulkhead("test", max_workers=2)
        ready_event = threading.Event()
        continue_event = threading.Event()

        def wait_work():
            ready_event.set()
            continue_event.wait()

        # 작업 시작
        future = bulkhead.submit(wait_work)

        # 작업이 시작될 때까지 대기
        ready_event.wait()

        state = bulkhead.get_state()
        assert state.active_count == 1

        # 작업 완료 허용
        continue_event.set()
        future.result(timeout=5.0)

        bulkhead.shutdown()


class TestThreadPoolBulkheadAcquire:
    """acquire 컨텍스트 매니저 테스트 (호환성)."""

    def test_acquire_context_manager(self):
        """acquire 컨텍스트 매니저 동작."""
        bulkhead = ThreadPoolBulkhead("test", max_workers=2, queue_size=5)

        with bulkhead.acquire():
            state = bulkhead.get_state()
            assert state.active_count == 1

        state = bulkhead.get_state()
        assert state.active_count == 0

        bulkhead.shutdown()

    def test_acquire_rejects_when_full(self):
        """acquire가 용량 초과 시 거부."""
        bulkhead = ThreadPoolBulkhead("test", max_workers=1, queue_size=0)

        with bulkhead.acquire():
            # 이미 1개 사용 중, queue_size=0이므로 즉시 거부
            with pytest.raises(BulkheadFullException):
                with bulkhead.acquire():
                    pass

        bulkhead.shutdown()


class TestThreadPoolBulkheadShutdown:
    """셧다운 테스트."""

    def test_shutdown_waits_for_completion(self):
        """shutdown(wait=True)가 작업 완료 대기."""
        bulkhead = ThreadPoolBulkhead("test", max_workers=2)
        completed = []

        def work():
            time.sleep(0.1)
            completed.append(True)

        bulkhead.submit(work)
        bulkhead.submit(work)

        bulkhead.shutdown(wait=True)

        assert len(completed) == 2

    def test_shutdown_no_wait(self):
        """shutdown(wait=False)가 즉시 반환."""
        bulkhead = ThreadPoolBulkhead("test", max_workers=2)

        def slow_work():
            time.sleep(10.0)

        bulkhead.submit(slow_work)

        # 즉시 반환
        bulkhead.shutdown(wait=False)
