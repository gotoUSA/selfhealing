"""
BulkheadPolicy / AsyncBulkheadPolicy 단위 테스트 (#228).

테스트 대상:
- resilience/bulkhead/policy.py (BulkheadPolicy, AsyncBulkheadPolicy,
  bulkhead_policy, async_bulkhead_policy)
- resilience/bulkhead/__init__.py (export 검증)

UNIT_TEST_GUIDELINES.md 준수:
- 계약 검증(Contract): 하드코딩 기대값 (name, outcome, executed_policies)
- 동작 검증(Behavior): 소스 참조 (BulkheadFullError, BulkheadTimeoutError 등)
- conftest.py 배치: 1개 파일 전용 fixture → 파일 내부 (§5.1)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from selfhealing.interfaces.resilience_policy import (
    AsyncResiliencePolicy,
    PolicyContext,
    PolicyOutcome,
    PolicyResult,
    ResiliencePolicy,
)
from selfhealing.resilience.bulkhead.async_semaphore import AsyncSemaphoreBulkhead
from selfhealing.resilience.bulkhead.base import Bulkhead, BulkheadState, BulkheadType
from selfhealing.resilience.bulkhead.exceptions import (
    BulkheadFullError,
    BulkheadTimeoutError,
)
from selfhealing.resilience.bulkhead.policy import (
    AsyncBulkheadPolicy,
    BulkheadPolicy,
    async_bulkhead_policy,
    bulkhead_policy,
)
from selfhealing.resilience.bulkhead.semaphore import SemaphoreBulkhead
from selfhealing.resilience.bulkhead.threadpool import ThreadPoolBulkhead


# =============================================================================
# Fixtures — 1개 파일 전용이므로 파일 내부 배치 (§5.1)
# =============================================================================


@pytest.fixture
def semaphore_bulkhead():
    """SemaphoreBulkhead 실제 인스턴스 (max_concurrent=2)."""
    return SemaphoreBulkhead("test_semaphore", max_concurrent=2)


@pytest.fixture
def semaphore_policy(semaphore_bulkhead):
    """SemaphoreBulkhead 기반 BulkheadPolicy 인스턴스."""
    return BulkheadPolicy(bulkhead=semaphore_bulkhead)


@pytest.fixture
def threadpool_bulkhead():
    """ThreadPoolBulkhead 실제 인스턴스 (max_workers=2, queue_size=2)."""
    bh = ThreadPoolBulkhead("test_threadpool", max_workers=2, queue_size=2)
    yield bh
    bh.shutdown(wait=True)


@pytest.fixture
def threadpool_policy(threadpool_bulkhead):
    """ThreadPoolBulkhead 기반 BulkheadPolicy 인스턴스."""
    return BulkheadPolicy(bulkhead=threadpool_bulkhead, timeout=5.0)


@pytest.fixture
def async_bulkhead():
    """AsyncSemaphoreBulkhead 실제 인스턴스 (max_concurrent=2)."""
    return AsyncSemaphoreBulkhead("test_async", max_concurrent=2)


@pytest.fixture
def async_policy(async_bulkhead):
    """AsyncBulkheadPolicy 인스턴스."""
    return AsyncBulkheadPolicy(async_bulkhead=async_bulkhead)


def _mock_bulkhead_state(
    name: str = "test",
    active_count: int = 0,
    max_concurrent: int = 10,
) -> BulkheadState:
    """BulkheadState 헬퍼."""
    return BulkheadState(
        name=name,
        bulkhead_type=BulkheadType.SEMAPHORE,
        max_concurrent=max_concurrent,
        active_count=active_count,
        waiting_count=0,
        rejected_count=0,
    )


# =============================================================================
# 계약 검증 (Contract) — BulkheadPolicy
# =============================================================================


class TestBulkheadPolicyContract:
    """BulkheadPolicy 고정 식별자 및 결과 구조 계약 검증."""

    def test_name_is_bulkhead(self, semaphore_policy):
        """name property는 'bulkhead'이다."""
        assert semaphore_policy.name == "bulkhead"

    def test_bulkhead_name_matches_inner_bulkhead(self, semaphore_policy, semaphore_bulkhead):
        """bulkhead_name은 내부 Bulkhead.name과 동일하다."""
        assert semaphore_policy.bulkhead_name == semaphore_bulkhead.name

    def test_success_result_has_bulkhead_in_executed_policies(self, semaphore_policy):
        """성공 결과의 executed_policies에 'bulkhead'가 포함된다."""
        result = semaphore_policy.execute(lambda: "ok")
        assert "bulkhead" in result.executed_policies

    def test_rejected_result_has_bulkhead_in_executed_policies(self):
        """거부 결과의 executed_policies에 'bulkhead'가 포함된다."""
        bh = SemaphoreBulkhead("full_test", max_concurrent=1)
        policy = BulkheadPolicy(bulkhead=bh)
        # 1번째 acquire로 slot 점유
        bh.try_acquire()
        try:
            result = policy.execute(lambda: "should_not_run")
            assert "bulkhead" in result.executed_policies
        finally:
            bh.release()

    def test_success_outcome_is_success(self, semaphore_policy):
        """성공 시 outcome은 PolicyOutcome.SUCCESS이다."""
        result = semaphore_policy.execute(lambda: 42)
        assert result.outcome == PolicyOutcome.SUCCESS

    def test_rejected_outcome_is_rejected(self):
        """거부 시 outcome은 PolicyOutcome.REJECTED이다."""
        bh = SemaphoreBulkhead("reject_test", max_concurrent=1)
        policy = BulkheadPolicy(bulkhead=bh)
        bh.try_acquire()
        try:
            result = policy.execute(lambda: "nope")
            assert result.outcome == PolicyOutcome.REJECTED
        finally:
            bh.release()

    def test_timeout_outcome_is_timeout(self):
        """ThreadPool 타임아웃 시 outcome은 PolicyOutcome.TIMEOUT이다."""
        import time

        bh = ThreadPoolBulkhead("timeout_test", max_workers=1, queue_size=5)
        policy = BulkheadPolicy(bulkhead=bh, timeout=0.1)
        try:
            result = policy.execute(lambda: time.sleep(5))
            assert result.outcome == PolicyOutcome.TIMEOUT
        finally:
            bh.shutdown(wait=False)

    def test_success_result_is_policy_result_instance(self, semaphore_policy):
        """반환 타입은 PolicyResult이다."""
        result = semaphore_policy.execute(lambda: "ok")
        assert isinstance(result, PolicyResult)

    def test_default_timeout_is_none(self, semaphore_bulkhead):
        """timeout 미지정 시 기본값은 None이다 (Fast Fail)."""
        policy = BulkheadPolicy(bulkhead=semaphore_bulkhead)
        assert policy._timeout is None

    def test_explicit_timeout_stored(self, semaphore_bulkhead):
        """명시적 timeout이 저장된다."""
        policy = BulkheadPolicy(bulkhead=semaphore_bulkhead, timeout=3.5)
        assert policy._timeout == 3.5


# =============================================================================
# BulkheadPolicy — Protocol 호환 계약 검증 (Contract)
# =============================================================================


class TestBulkheadPolicyProtocolContract:
    """ResiliencePolicy Protocol 호환성 검증 — 228 §8 체크리스트."""

    def test_bulkhead_policy_is_resilience_policy(self, semaphore_policy):
        """BulkheadPolicy는 ResiliencePolicy Protocol과 isinstance 호환이다."""
        assert isinstance(semaphore_policy, ResiliencePolicy)

    def test_threadpool_policy_is_resilience_policy(self, threadpool_policy):
        """ThreadPoolBulkhead 기반 BulkheadPolicy도 ResiliencePolicy 호환이다."""
        assert isinstance(threadpool_policy, ResiliencePolicy)


# =============================================================================
# BulkheadPolicy 성공 경로 동작 검증 (Behavior)
# =============================================================================


class TestBulkheadPolicySemaphoreSuccessBehavior:
    """SemaphoreBulkhead 경로 성공 동작 검증."""

    def test_success_returns_function_value(self, semaphore_policy):
        """성공 시 func의 반환값이 result.value에 담긴다."""
        result = semaphore_policy.execute(lambda: "success_value")
        assert result.value == "success_value"

    def test_success_passes_args(self, semaphore_policy):
        """args가 함수에 정확히 전달된다."""

        def add(a, b):
            return a + b

        result = semaphore_policy.execute(add, 3, 7)
        assert result.value == 10

    def test_success_passes_kwargs(self, semaphore_policy):
        """kwargs가 함수에 정확히 전달된다."""

        def greet(name, prefix="Hello"):
            return f"{prefix}, {name}"

        result = semaphore_policy.execute(greet, "world", prefix="Hi")
        assert result.value == "Hi, world"

    def test_success_result_property_true(self, semaphore_policy):
        """성공 result의 .success property는 True이다."""
        result = semaphore_policy.execute(lambda: "ok")
        assert result.success is True

    def test_success_result_rejected_property_false(self, semaphore_policy):
        """성공 result의 .rejected property는 False이다."""
        result = semaphore_policy.execute(lambda: "ok")
        assert result.rejected is False

    def test_success_result_error_is_none(self, semaphore_policy):
        """성공 시 error는 None이다."""
        result = semaphore_policy.execute(lambda: "ok")
        assert result.error is None

    def test_success_metadata_contains_bulkhead_name(self, semaphore_policy, semaphore_bulkhead):
        """성공 metadata에 bulkhead_name이 포함된다."""
        result = semaphore_policy.execute(lambda: "ok")
        assert result.metadata["bulkhead_name"] == semaphore_bulkhead.name

    def test_success_metadata_contains_state(self, semaphore_policy):
        """성공 metadata에 state dict가 포함된다."""
        result = semaphore_policy.execute(lambda: "ok")
        state = result.metadata["state"]
        assert "active_count" in state
        assert "max_concurrent" in state
        assert "available_permits" in state
        assert "utilization_percent" in state


# =============================================================================
# BulkheadPolicy — ThreadPoolBulkhead 분기 동작 검증 (Behavior)
# =============================================================================


class TestBulkheadPolicyThreadPoolSuccessBehavior:
    """ThreadPoolBulkhead isinstance 분기 성공 동작 검증."""

    def test_threadpool_success_returns_value(self, threadpool_policy):
        """ThreadPoolBulkhead 경로에서 성공 시 반환값이 result.value에 담긴다."""
        result = threadpool_policy.execute(lambda: "thread_result")
        assert result.value == "thread_result"

    def test_threadpool_isinstance_check(self, threadpool_bulkhead):
        """ThreadPoolBulkhead는 isinstance(bh, ThreadPoolBulkhead)가 True이다."""
        assert isinstance(threadpool_bulkhead, ThreadPoolBulkhead)
        # Bulkhead ABC도 상속
        assert isinstance(threadpool_bulkhead, Bulkhead)

    def test_threadpool_success_outcome(self, threadpool_policy):
        """ThreadPoolBulkhead 경로 성공 시 outcome은 SUCCESS이다."""
        result = threadpool_policy.execute(lambda: 42)
        assert result.outcome == PolicyOutcome.SUCCESS

    def test_threadpool_passes_args_kwargs(self, threadpool_policy):
        """ThreadPoolBulkhead 경로에서 args, kwargs가 전달된다."""

        def compute(a, b, multiplier=1):
            return (a + b) * multiplier

        result = threadpool_policy.execute(compute, 3, 4, multiplier=2)
        assert result.value == 14

    def test_threadpool_default_timeout_30(self):
        """timeout=None일 때 ThreadPool에 30.0이 전달된다."""
        bh = ThreadPoolBulkhead("default_timeout_test", max_workers=1, queue_size=5)
        policy = BulkheadPolicy(bulkhead=bh, timeout=None)
        try:
            # 정상 실행되면 30초 이내이므로 SUCCESS
            result = policy.execute(lambda: "ok")
            assert result.outcome == PolicyOutcome.SUCCESS
        finally:
            bh.shutdown(wait=True)


# =============================================================================
# BulkheadPolicy — REJECTED 동작 검증 (Behavior)
# =============================================================================


class TestBulkheadPolicyRejectedBehavior:
    """BulkheadFullError → REJECTED 동작 검증."""

    def test_semaphore_full_returns_rejected(self):
        """SemaphoreBulkhead가 가득 차면 REJECTED를 반환한다."""
        bh = SemaphoreBulkhead("full_test", max_concurrent=1)
        policy = BulkheadPolicy(bulkhead=bh)
        # slot 점유
        bh.try_acquire()
        try:
            result = policy.execute(lambda: "should_not_run")
            assert result.outcome == PolicyOutcome.REJECTED
            assert result.rejected is True
        finally:
            bh.release()

    def test_rejected_error_is_bulkhead_full_error(self):
        """거부 시 error는 BulkheadFullError 인스턴스이다."""
        bh = SemaphoreBulkhead("error_test", max_concurrent=1)
        policy = BulkheadPolicy(bulkhead=bh)
        bh.try_acquire()
        try:
            result = policy.execute(lambda: "nope")
            assert isinstance(result.error, BulkheadFullError)
        finally:
            bh.release()

    def test_rejected_error_has_bulkhead_name(self):
        """거부 시 error.bulkhead_name은 Bulkhead의 name과 동일하다."""
        bh = SemaphoreBulkhead("name_check", max_concurrent=1)
        policy = BulkheadPolicy(bulkhead=bh)
        bh.try_acquire()
        try:
            result = policy.execute(lambda: "nope")
            assert result.error.bulkhead_name == bh.name
        finally:
            bh.release()

    def test_rejected_error_has_max_concurrent(self):
        """거부 시 error.max_concurrent가 설정값과 일치한다."""
        max_conc = 3
        bh = SemaphoreBulkhead("max_check", max_concurrent=max_conc)
        policy = BulkheadPolicy(bulkhead=bh)
        # 모든 slot 점유
        acquired = [bh.try_acquire() for _ in range(max_conc)]
        try:
            result = policy.execute(lambda: "nope")
            assert result.error.max_concurrent == max_conc
        finally:
            for _ in filter(None, acquired):
                bh.release()

    def test_rejected_value_is_none(self):
        """거부 시 value는 None이다."""
        bh = SemaphoreBulkhead("val_none", max_concurrent=1)
        policy = BulkheadPolicy(bulkhead=bh)
        bh.try_acquire()
        try:
            result = policy.execute(lambda: "nope")
            assert result.value is None
        finally:
            bh.release()

    def test_rejected_does_not_execute_function(self):
        """거부 시 func은 실행되지 않는다."""
        bh = SemaphoreBulkhead("no_exec", max_concurrent=1)
        policy = BulkheadPolicy(bulkhead=bh)
        bh.try_acquire()
        func = MagicMock()
        try:
            policy.execute(func)
            func.assert_not_called()
        finally:
            bh.release()

    def test_rejected_metadata_contains_bulkhead_name(self):
        """거부 metadata에 bulkhead_name이 포함된다."""
        bh = SemaphoreBulkhead("meta_test", max_concurrent=1)
        policy = BulkheadPolicy(bulkhead=bh)
        bh.try_acquire()
        try:
            result = policy.execute(lambda: "nope")
            assert result.metadata["bulkhead_name"] == bh.name
        finally:
            bh.release()

    def test_rejected_metadata_contains_state(self):
        """거부 metadata에 state dict가 포함된다."""
        bh = SemaphoreBulkhead("state_meta", max_concurrent=1)
        policy = BulkheadPolicy(bulkhead=bh)
        bh.try_acquire()
        try:
            result = policy.execute(lambda: "nope")
            state = result.metadata["state"]
            assert "active_count" in state
            assert "max_concurrent" in state
        finally:
            bh.release()


# =============================================================================
# BulkheadPolicy — TIMEOUT 동작 검증 (Behavior)
# =============================================================================


class TestBulkheadPolicyTimeoutBehavior:
    """ThreadPoolBulkhead 타임아웃 동작 검증."""

    def test_timeout_error_is_bulkhead_timeout_error(self):
        """타임아웃 시 error는 BulkheadTimeoutError 인스턴스이다."""
        import time

        bh = ThreadPoolBulkhead("timeout_err", max_workers=1, queue_size=5)
        policy = BulkheadPolicy(bulkhead=bh, timeout=0.1)
        try:
            result = policy.execute(lambda: time.sleep(5))
            assert isinstance(result.error, BulkheadTimeoutError)
        finally:
            bh.shutdown(wait=False)

    def test_timeout_metadata_contains_timeout_value(self):
        """타임아웃 metadata에 timeout 값이 포함된다."""
        import time

        timeout_val = 0.1
        bh = ThreadPoolBulkhead("timeout_meta", max_workers=1, queue_size=5)
        policy = BulkheadPolicy(bulkhead=bh, timeout=timeout_val)
        try:
            result = policy.execute(lambda: time.sleep(5))
            assert result.metadata["timeout"] == timeout_val
        finally:
            bh.shutdown(wait=False)

    def test_timeout_metadata_contains_bulkhead_name(self):
        """타임아웃 metadata에 bulkhead_name이 포함된다."""
        import time

        bh = ThreadPoolBulkhead("timeout_name", max_workers=1, queue_size=5)
        policy = BulkheadPolicy(bulkhead=bh, timeout=0.1)
        try:
            result = policy.execute(lambda: time.sleep(5))
            assert result.metadata["bulkhead_name"] == bh.name
        finally:
            bh.shutdown(wait=False)

    def test_timeout_value_is_none(self):
        """타임아웃 시 value는 None이다."""
        import time

        bh = ThreadPoolBulkhead("timeout_val", max_workers=1, queue_size=5)
        policy = BulkheadPolicy(bulkhead=bh, timeout=0.1)
        try:
            result = policy.execute(lambda: time.sleep(5))
            assert result.value is None
        finally:
            bh.shutdown(wait=False)


# =============================================================================
# BulkheadPolicy — 예외 재전파 동작 검증 (Behavior)
# =============================================================================


class TestBulkheadPolicyExceptionPropagationBehavior:
    """비즈니스 예외 재전파 동작 검증."""

    def test_business_exception_reraises(self, semaphore_policy):
        """비즈니스 예외(ValueError 등)는 catch하지 않고 상위로 재전파한다."""
        with pytest.raises(ValueError, match="business error"):
            semaphore_policy.execute(self._raise_value_error)

    def test_runtime_error_reraises(self, semaphore_policy):
        """RuntimeError도 상위로 재전파된다."""
        with pytest.raises(RuntimeError, match="runtime fail"):
            semaphore_policy.execute(self._raise_runtime_error)

    def test_threadpool_business_exception_reraises(self, threadpool_policy):
        """ThreadPoolBulkhead 경로에서도 비즈니스 예외는 재전파된다."""
        with pytest.raises(ValueError, match="business error"):
            threadpool_policy.execute(self._raise_value_error)

    @staticmethod
    def _raise_value_error():
        raise ValueError("business error")

    @staticmethod
    def _raise_runtime_error():
        raise RuntimeError("runtime fail")


# =============================================================================
# BulkheadPolicy — _get_state_dict 동작 검증 (Behavior)
# =============================================================================


class TestBulkheadPolicyStateDictBehavior:
    """_get_state_dict() 반환값 검증."""

    def test_state_dict_has_four_keys(self, semaphore_policy):
        """_get_state_dict()는 4개 필드를 포함한다."""
        state_dict = semaphore_policy._get_state_dict()
        expected_keys = {"active_count", "max_concurrent", "available_permits", "utilization_percent"}
        assert set(state_dict.keys()) == expected_keys

    def test_state_dict_matches_bulkhead_state(self, semaphore_policy, semaphore_bulkhead):
        """_get_state_dict()의 값은 bulkhead.get_state()와 일치한다."""
        state = semaphore_bulkhead.get_state()
        state_dict = semaphore_policy._get_state_dict()
        assert state_dict["active_count"] == state.active_count
        assert state_dict["max_concurrent"] == state.max_concurrent
        assert state_dict["available_permits"] == state.available_permits
        assert state_dict["utilization_percent"] == state.utilization_percent

    def test_state_dict_reflects_active_count(self):
        """slot 점유 후 state_dict의 active_count가 반영된다."""
        bh = SemaphoreBulkhead("state_active", max_concurrent=3)
        policy = BulkheadPolicy(bulkhead=bh)
        bh.try_acquire()
        try:
            state_dict = policy._get_state_dict()
            assert state_dict["active_count"] == 1
            assert state_dict["available_permits"] == 2
        finally:
            bh.release()


# =============================================================================
# BulkheadPolicy — PolicyContext 전달 동작 검증 (Behavior)
# =============================================================================


class TestBulkheadPolicyContextBehavior:
    """PolicyContext 전달 동작 검증."""

    def test_execute_accepts_context_parameter(self, semaphore_policy):
        """execute()에 context 파라미터를 전달할 수 있다."""
        ctx = PolicyContext(order_id="order-123", trace_id="trace-abc")
        result = semaphore_policy.execute(lambda: "with_context", context=ctx)
        assert result.value == "with_context"
        assert result.outcome == PolicyOutcome.SUCCESS

    def test_execute_works_without_context(self, semaphore_policy):
        """context=None (기본값)으로도 정상 동작한다."""
        result = semaphore_policy.execute(lambda: "no_context")
        assert result.value == "no_context"
        assert result.outcome == PolicyOutcome.SUCCESS


# =============================================================================
# 계약 검증 (Contract) — AsyncBulkheadPolicy
# =============================================================================


class TestAsyncBulkheadPolicyContract:
    """AsyncBulkheadPolicy 고정 식별자 및 결과 구조 계약 검증."""

    def test_name_is_bulkhead(self, async_policy):
        """name property는 'bulkhead'이다."""
        assert async_policy.name == "bulkhead"

    def test_bulkhead_name_matches_inner_bulkhead(self, async_policy, async_bulkhead):
        """bulkhead_name은 내부 AsyncSemaphoreBulkhead.name과 동일하다."""
        assert async_policy.bulkhead_name == async_bulkhead.name

    def test_default_timeout_is_none(self, async_bulkhead):
        """timeout 미지정 시 기본값은 None이다."""
        policy = AsyncBulkheadPolicy(async_bulkhead=async_bulkhead)
        assert policy._timeout is None

    def test_explicit_timeout_stored(self, async_bulkhead):
        """명시적 timeout이 저장된다."""
        policy = AsyncBulkheadPolicy(async_bulkhead=async_bulkhead, timeout=2.0)
        assert policy._timeout == 2.0


# =============================================================================
# AsyncBulkheadPolicy — Protocol 호환 계약 검증 (Contract)
# =============================================================================


class TestAsyncBulkheadPolicyProtocolContract:
    """AsyncResiliencePolicy Protocol 호환성 검증 — 228 §8 체크리스트."""

    def test_async_bulkhead_policy_is_async_resilience_policy(self, async_policy):
        """AsyncBulkheadPolicy는 AsyncResiliencePolicy Protocol과 isinstance 호환이다."""
        assert isinstance(async_policy, AsyncResiliencePolicy)


# =============================================================================
# AsyncBulkheadPolicy — 성공 경로 동작 검증 (Behavior)
# =============================================================================


class TestAsyncBulkheadPolicySuccessBehavior:
    """비동기 성공 경로 동작 검증."""

    @pytest.mark.asyncio
    async def test_success_returns_function_value(self, async_policy):
        """성공 시 func의 반환값이 result.value에 담긴다."""

        async def async_func():
            return "async_success"

        result = await async_policy.execute(async_func)
        assert result.value == "async_success"

    @pytest.mark.asyncio
    async def test_success_outcome_is_success(self, async_policy):
        """성공 시 outcome은 PolicyOutcome.SUCCESS이다."""

        async def async_func():
            return 42

        result = await async_policy.execute(async_func)
        assert result.outcome == PolicyOutcome.SUCCESS

    @pytest.mark.asyncio
    async def test_success_has_bulkhead_in_executed_policies(self, async_policy):
        """성공 결과의 executed_policies에 'bulkhead'가 포함된다."""

        async def async_func():
            return "ok"

        result = await async_policy.execute(async_func)
        assert "bulkhead" in result.executed_policies

    @pytest.mark.asyncio
    async def test_success_passes_args(self, async_policy):
        """args가 비동기 함수에 정확히 전달된다."""

        async def add(a, b):
            return a + b

        result = await async_policy.execute(add, 5, 8)
        assert result.value == 13

    @pytest.mark.asyncio
    async def test_success_passes_kwargs(self, async_policy):
        """kwargs가 비동기 함수에 정확히 전달된다."""

        async def greet(name, prefix="Hello"):
            return f"{prefix}, {name}"

        result = await async_policy.execute(greet, "world", prefix="Async")
        assert result.value == "Async, world"

    @pytest.mark.asyncio
    async def test_success_result_property_true(self, async_policy):
        """성공 result의 .success property는 True이다."""

        async def async_func():
            return "ok"

        result = await async_policy.execute(async_func)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_success_metadata_contains_bulkhead_name(self, async_policy, async_bulkhead):
        """성공 metadata에 bulkhead_name이 포함된다."""

        async def async_func():
            return "ok"

        result = await async_policy.execute(async_func)
        assert result.metadata["bulkhead_name"] == async_bulkhead.name

    @pytest.mark.asyncio
    async def test_success_metadata_contains_state(self, async_policy):
        """성공 metadata에 state dict가 포함된다."""

        async def async_func():
            return "ok"

        result = await async_policy.execute(async_func)
        state = result.metadata["state"]
        assert "active_count" in state
        assert "max_concurrent" in state
        assert "available_permits" in state
        assert "utilization_percent" in state

    @pytest.mark.asyncio
    async def test_success_error_is_none(self, async_policy):
        """성공 시 error는 None이다."""

        async def async_func():
            return "ok"

        result = await async_policy.execute(async_func)
        assert result.error is None


# =============================================================================
# AsyncBulkheadPolicy — REJECTED 동작 검증 (Behavior)
# =============================================================================


class TestAsyncBulkheadPolicyRejectedBehavior:
    """비동기 BulkheadFullError → REJECTED 동작 검증."""

    @pytest.mark.asyncio
    async def test_async_full_returns_rejected(self):
        """비동기 격벽이 가득 차면 REJECTED를 반환한다."""
        bh = AsyncSemaphoreBulkhead("async_full", max_concurrent=1)
        policy = AsyncBulkheadPolicy(async_bulkhead=bh)
        # slot 점유
        await bh.try_acquire()
        try:

            async def should_not_run():
                return "nope"

            result = await policy.execute(should_not_run)
            assert result.outcome == PolicyOutcome.REJECTED
            assert result.rejected is True
        finally:
            await bh.release()

    @pytest.mark.asyncio
    async def test_rejected_error_is_bulkhead_full_error(self):
        """비동기 거부 시 error는 BulkheadFullError 인스턴스이다."""
        bh = AsyncSemaphoreBulkhead("async_err", max_concurrent=1)
        policy = AsyncBulkheadPolicy(async_bulkhead=bh)
        await bh.try_acquire()
        try:

            async def noop():
                return "nope"

            result = await policy.execute(noop)
            assert isinstance(result.error, BulkheadFullError)
        finally:
            await bh.release()

    @pytest.mark.asyncio
    async def test_rejected_value_is_none(self):
        """비동기 거부 시 value는 None이다."""
        bh = AsyncSemaphoreBulkhead("async_val_none", max_concurrent=1)
        policy = AsyncBulkheadPolicy(async_bulkhead=bh)
        await bh.try_acquire()
        try:

            async def noop():
                return "nope"

            result = await policy.execute(noop)
            assert result.value is None
        finally:
            await bh.release()

    @pytest.mark.asyncio
    async def test_rejected_has_bulkhead_in_executed_policies(self):
        """비동기 거부 결과의 executed_policies에 'bulkhead'가 포함된다."""
        bh = AsyncSemaphoreBulkhead("async_ep", max_concurrent=1)
        policy = AsyncBulkheadPolicy(async_bulkhead=bh)
        await bh.try_acquire()
        try:

            async def noop():
                return "nope"

            result = await policy.execute(noop)
            assert "bulkhead" in result.executed_policies
        finally:
            await bh.release()

    @pytest.mark.asyncio
    async def test_rejected_metadata_contains_bulkhead_name(self):
        """비동기 거부 metadata에 bulkhead_name이 포함된다."""
        bh = AsyncSemaphoreBulkhead("async_meta_name", max_concurrent=1)
        policy = AsyncBulkheadPolicy(async_bulkhead=bh)
        await bh.try_acquire()
        try:

            async def noop():
                return "nope"

            result = await policy.execute(noop)
            assert result.metadata["bulkhead_name"] == bh.name
        finally:
            await bh.release()


# =============================================================================
# AsyncBulkheadPolicy — 예외 재전파 동작 검증 (Behavior)
# =============================================================================


class TestAsyncBulkheadPolicyExceptionPropagationBehavior:
    """비동기 비즈니스 예외 재전파 동작 검증."""

    @pytest.mark.asyncio
    async def test_async_business_exception_reraises(self, async_policy):
        """비동기 비즈니스 예외(ValueError)는 catch하지 않고 상위로 재전파한다."""

        async def raise_error():
            raise ValueError("async business error")

        with pytest.raises(ValueError, match="async business error"):
            await async_policy.execute(raise_error)

    @pytest.mark.asyncio
    async def test_async_runtime_error_reraises(self, async_policy):
        """비동기 RuntimeError도 상위로 재전파된다."""

        async def raise_error():
            raise RuntimeError("async runtime fail")

        with pytest.raises(RuntimeError, match="async runtime fail"):
            await async_policy.execute(raise_error)


# =============================================================================
# AsyncBulkheadPolicy — _get_state_dict 동작 검증 (Behavior)
# =============================================================================


class TestAsyncBulkheadPolicyStateDictBehavior:
    """AsyncBulkheadPolicy._get_state_dict() 반환값 검증."""

    def test_async_state_dict_has_four_keys(self, async_policy):
        """_get_state_dict()는 4개 필드를 포함한다."""
        state_dict = async_policy._get_state_dict()
        expected_keys = {"active_count", "max_concurrent", "available_permits", "utilization_percent"}
        assert set(state_dict.keys()) == expected_keys

    def test_async_state_dict_matches_bulkhead_state(self, async_policy, async_bulkhead):
        """_get_state_dict()의 값은 async_bulkhead.get_state()와 일치한다."""
        state = async_bulkhead.get_state()
        state_dict = async_policy._get_state_dict()
        assert state_dict["active_count"] == state.active_count
        assert state_dict["max_concurrent"] == state.max_concurrent
        assert state_dict["available_permits"] == state.available_permits
        assert state_dict["utilization_percent"] == state.utilization_percent


# =============================================================================
# AsyncBulkheadPolicy — PolicyContext 전달 동작 검증 (Behavior)
# =============================================================================


class TestAsyncBulkheadPolicyContextBehavior:
    """AsyncBulkheadPolicy PolicyContext 전달 동작 검증."""

    @pytest.mark.asyncio
    async def test_execute_accepts_context_parameter(self, async_policy):
        """execute()에 context 파라미터를 전달할 수 있다."""
        ctx = PolicyContext(order_id="order-async", trace_id="trace-async")

        async def async_func():
            return "with_context"

        result = await async_policy.execute(async_func, context=ctx)
        assert result.value == "with_context"
        assert result.outcome == PolicyOutcome.SUCCESS

    @pytest.mark.asyncio
    async def test_execute_works_without_context(self, async_policy):
        """context=None (기본값)으로도 정상 동작한다."""

        async def async_func():
            return "no_context"

        result = await async_policy.execute(async_func)
        assert result.value == "no_context"


# =============================================================================
# bulkhead_policy() 팩토리 동작 검증 (Behavior)
# =============================================================================


class TestBulkheadPolicyFactoryBehavior:
    """bulkhead_policy() 팩토리 함수 동작 검증."""

    def test_factory_returns_bulkhead_policy_instance(self):
        """팩토리 반환 타입은 BulkheadPolicy이다."""
        policy = bulkhead_policy("factory_test", max_concurrent=5)
        assert isinstance(policy, BulkheadPolicy)

    def test_factory_sets_bulkhead_name(self):
        """팩토리로 생성한 policy의 bulkhead_name이 인자와 일치한다."""
        policy = bulkhead_policy("my_domain", max_concurrent=5)
        assert policy.bulkhead_name == "my_domain"

    def test_factory_sets_timeout(self):
        """팩토리의 timeout 인자가 policy에 전달된다."""
        policy = bulkhead_policy("timeout_test", max_concurrent=5, timeout=3.0)
        assert policy._timeout == 3.0

    def test_factory_default_timeout_is_none(self):
        """팩토리의 기본 timeout은 None이다."""
        policy = bulkhead_policy("default_timeout", max_concurrent=5)
        assert policy._timeout is None

    def test_factory_semaphore_type_default(self):
        """기본 bulkhead_type은 'semaphore'이다."""
        policy = bulkhead_policy("sem_default", max_concurrent=5)
        # 내부 bulkhead가 SemaphoreBulkhead인지 확인
        assert isinstance(policy._bulkhead, SemaphoreBulkhead)

    def test_factory_thread_pool_type(self):
        """bulkhead_type='thread_pool'이면 ThreadPoolBulkhead를 생성한다."""
        policy = bulkhead_policy("tp_test", max_concurrent=3, bulkhead_type="thread_pool")
        try:
            assert isinstance(policy._bulkhead, ThreadPoolBulkhead)
        finally:
            if isinstance(policy._bulkhead, ThreadPoolBulkhead):
                policy._bulkhead.shutdown(wait=True)

    def test_factory_registry_singleton(self):
        """동일 name으로 두 번 호출 시 같은 Bulkhead 인스턴스를 재사용한다."""
        p1 = bulkhead_policy("singleton_test", max_concurrent=5)
        p2 = bulkhead_policy("singleton_test", max_concurrent=5)
        assert p1._bulkhead is p2._bulkhead

    def test_factory_execute_works(self):
        """팩토리로 생성한 policy가 정상 실행된다."""
        policy = bulkhead_policy("exec_test", max_concurrent=5)
        result = policy.execute(lambda: "factory_ok")
        assert result.value == "factory_ok"
        assert result.outcome == PolicyOutcome.SUCCESS


# =============================================================================
# async_bulkhead_policy() 팩토리 동작 검증 (Behavior)
# =============================================================================


class TestAsyncBulkheadPolicyFactoryBehavior:
    """async_bulkhead_policy() 팩토리 함수 동작 검증."""

    def test_factory_returns_async_bulkhead_policy_instance(self):
        """팩토리 반환 타입은 AsyncBulkheadPolicy이다."""
        policy = async_bulkhead_policy("async_factory_test", max_concurrent=5)
        assert isinstance(policy, AsyncBulkheadPolicy)

    def test_factory_sets_bulkhead_name(self):
        """팩토리로 생성한 async_policy의 bulkhead_name이 인자와 일치한다."""
        policy = async_bulkhead_policy("async_domain", max_concurrent=5)
        assert policy.bulkhead_name == "async_domain"

    def test_factory_sets_timeout(self):
        """팩토리의 timeout 인자가 async_policy에 전달된다."""
        policy = async_bulkhead_policy("async_timeout", max_concurrent=5, timeout=2.0)
        assert policy._timeout == 2.0

    def test_factory_default_timeout_is_none(self):
        """팩토리의 기본 timeout은 None이다."""
        policy = async_bulkhead_policy("async_default", max_concurrent=5)
        assert policy._timeout is None

    def test_factory_inner_is_async_semaphore_bulkhead(self):
        """팩토리 내부는 AsyncSemaphoreBulkhead 인스턴스이다."""
        policy = async_bulkhead_policy("async_inner", max_concurrent=5)
        assert isinstance(policy._async_bulkhead, AsyncSemaphoreBulkhead)

    @pytest.mark.asyncio
    async def test_factory_execute_works(self):
        """팩토리로 생성한 async_policy가 정상 실행된다."""
        policy = async_bulkhead_policy("async_exec", max_concurrent=5)

        async def async_func():
            return "async_factory_ok"

        result = await policy.execute(async_func)
        assert result.value == "async_factory_ok"
        assert result.outcome == PolicyOutcome.SUCCESS


# =============================================================================
# __init__.py export 계약 검증 (Contract)
# =============================================================================


class TestBulkheadPackageExportContract:
    """resilience/bulkhead/__init__.py export 검증."""

    def test_bulkhead_policy_exported(self):
        """BulkheadPolicy가 __init__.py에서 export된다."""
        from selfhealing.resilience.bulkhead import BulkheadPolicy as Exported

        assert Exported is BulkheadPolicy

    def test_async_bulkhead_policy_exported(self):
        """AsyncBulkheadPolicy가 __init__.py에서 export된다."""
        from selfhealing.resilience.bulkhead import AsyncBulkheadPolicy as Exported

        assert Exported is AsyncBulkheadPolicy

    def test_bulkhead_policy_factory_exported(self):
        """bulkhead_policy 팩토리가 __init__.py에서 export된다."""
        from selfhealing.resilience.bulkhead import bulkhead_policy as exported_factory

        assert exported_factory is bulkhead_policy

    def test_async_bulkhead_policy_factory_exported(self):
        """async_bulkhead_policy 팩토리가 __init__.py에서 export된다."""
        from selfhealing.resilience.bulkhead import async_bulkhead_policy as exported_factory

        assert exported_factory is async_bulkhead_policy

    def test_policy_classes_in_all(self):
        """Policy 클래스/팩토리가 __all__에 포함된다."""
        import selfhealing.resilience.bulkhead as pkg

        assert "BulkheadPolicy" in pkg.__all__
        assert "AsyncBulkheadPolicy" in pkg.__all__
        assert "bulkhead_policy" in pkg.__all__
        assert "async_bulkhead_policy" in pkg.__all__
