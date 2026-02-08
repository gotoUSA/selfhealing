"""
Hedging Strategy Unit Tests.

헷징 전략의 핵심 기능을 테스트합니다:
- IMMEDIATE/DELAYED/ADAPTIVE 모드
- 첫 성공 응답 선택
- 모든 후보 실패 시 예외
- 타임아웃 처리
- 확정적 에러 시 즉시 중단
- ContextVar 전파
"""

from __future__ import annotations

import asyncio
import contextvars
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.core.hedging.config import (
    HedgingCandidate,
    HedgingConfig,
    HedgingMode,
)
from selfhealing.core.hedging.executor import HedgingExecutor
from selfhealing.core.hedging.async_executor import AsyncHedgingExecutor
from selfhealing.core.hedging.exceptions import (
    HedgingAllFailedError,
    HedgingTimeoutError,
    NonRetryableHedgingError,
)
from selfhealing.core.hedging.result import HedgingResult
from selfhealing.core.hedging.latency_tracker import HedgingLatencyTracker


class TestHedgingConfig:
    """HedgingConfig 테스트."""

    def test_default_config(self):
        """기본 설정 테스트."""
        config = HedgingConfig()

        assert config.mode == HedgingMode.DELAYED
        assert config.timeout == 5.0
        assert config.delay == 0.1
        assert config.max_candidates == 3
        assert config.cancel_on_success is True

    def test_custom_config(self):
        """커스텀 설정 테스트."""
        config = HedgingConfig(
            mode=HedgingMode.IMMEDIATE,
            timeout=10.0,
            delay=0.5,
            max_candidates=5,
        )

        assert config.mode == HedgingMode.IMMEDIATE
        assert config.timeout == 10.0
        assert config.delay == 0.5
        assert config.max_candidates == 5

    def test_non_retryable_exceptions_default(self):
        """재시도 불가 예외 기본값 테스트."""
        config = HedgingConfig()

        assert PermissionError in config.non_retryable_exceptions
        assert KeyError in config.non_retryable_exceptions
        assert ValueError in config.non_retryable_exceptions

    def test_non_retryable_http_codes_default(self):
        """재시도 불가 HTTP 코드 기본값 테스트."""
        config = HedgingConfig()

        assert 400 in config.non_retryable_http_codes
        assert 401 in config.non_retryable_http_codes
        assert 403 in config.non_retryable_http_codes
        assert 404 in config.non_retryable_http_codes


class TestHedgingCandidate:
    """HedgingCandidate 테스트."""

    def test_candidate_creation(self):
        """후보 생성 테스트."""
        fn = lambda: "test"
        candidate = HedgingCandidate(
            name="test_candidate",
            fn=fn,
            priority=1,
        )

        assert candidate.name == "test_candidate"
        assert candidate.fn == fn
        assert candidate.priority == 1
        assert candidate.metadata == {}


class TestHedgingResult:
    """HedgingResult 테스트."""

    def test_result_creation(self):
        """결과 생성 테스트."""
        result = HedgingResult(
            value="test_value",
            success=True,
            source="primary",
            latency_ms=100.0,
            hedged=False,
            candidates_tried=2,
            candidates_succeeded=1,
            candidates_failed=1,
        )

        assert result.value == "test_value"
        assert result.success is True
        assert result.source == "primary"
        assert result.latency_ms == 100.0
        assert result.hedged is False

    def test_hedging_benefit_when_hedged(self):
        """헷징 이득 계산 테스트 (헷징 발생 시)."""
        result = HedgingResult(
            value="test",
            success=True,
            source="secondary",
            latency_ms=100.0,
            hedged=True,
            candidates_tried=2,
            candidates_succeeded=1,
            candidates_failed=0,
            metadata={"primary_latency_ms": 500.0},
        )

        # Primary가 500ms, Secondary가 100ms → 400ms 이득
        assert result.hedging_benefit_ms == 400.0

    def test_hedging_benefit_when_not_hedged(self):
        """헷징 이득 계산 테스트 (헷징 미발생 시)."""
        result = HedgingResult(
            value="test",
            success=True,
            source="primary",
            latency_ms=100.0,
            hedged=False,
            candidates_tried=1,
            candidates_succeeded=1,
            candidates_failed=0,
        )

        # Primary 선택 시 이득 없음
        assert result.hedging_benefit_ms is None

    def test_latency_seconds(self):
        """초 단위 지연시간 테스트."""
        result = HedgingResult(
            value="test",
            success=True,
            source="primary",
            latency_ms=1500.0,
            hedged=False,
            candidates_tried=1,
            candidates_succeeded=1,
            candidates_failed=0,
        )

        assert result.latency_seconds == 1.5


class TestHedgingExecutorImmediate:
    """HedgingExecutor IMMEDIATE 모드 테스트."""

    def test_immediate_mode_fast_candidate_wins(self):
        """IMMEDIATE 모드: 빠른 후보 선택 테스트."""
        config = HedgingConfig(mode=HedgingMode.IMMEDIATE, timeout=5.0)
        executor = HedgingExecutor(config)

        def slow_fn():
            time.sleep(0.5)
            return "slow"

        def fast_fn():
            time.sleep(0.05)
            return "fast"

        candidates = [
            HedgingCandidate("slow", slow_fn),
            HedgingCandidate("fast", fast_fn),
        ]

        result = executor.execute(candidates)

        assert result.success is True
        assert result.value == "fast"
        assert result.source == "fast"
        assert result.hedged is True  # Primary가 아닌 후보 선택됨

    def test_immediate_mode_all_executed(self):
        """IMMEDIATE 모드: 모든 후보 실행 테스트."""
        config = HedgingConfig(
            mode=HedgingMode.IMMEDIATE,
            timeout=5.0,
            cancel_on_success=False,  # 취소하지 않음
        )
        executor = HedgingExecutor(config)

        execution_count = {"count": 0}

        def counting_fn():
            execution_count["count"] += 1
            return "result"

        candidates = [
            HedgingCandidate("c1", counting_fn),
            HedgingCandidate("c2", counting_fn),
            HedgingCandidate("c3", counting_fn),
        ]

        result = executor.execute(candidates)
        time.sleep(0.2)  # 다른 후보들 완료 대기

        assert result.success is True
        # cancel_on_success=False라도 첫 성공 후 다른 후보들이 실행될 수 있음
        assert execution_count["count"] >= 1

    def test_single_candidate(self):
        """단일 후보 테스트."""
        config = HedgingConfig(mode=HedgingMode.IMMEDIATE)
        executor = HedgingExecutor(config)

        candidates = [
            HedgingCandidate("only", lambda: "only_result"),
        ]

        result = executor.execute(candidates)

        assert result.success is True
        assert result.value == "only_result"
        assert result.hedged is False

    def test_no_candidates_raises_error(self):
        """후보 없음 에러 테스트."""
        config = HedgingConfig(mode=HedgingMode.IMMEDIATE)
        executor = HedgingExecutor(config)

        with pytest.raises(ValueError, match="At least one candidate required"):
            executor.execute([])


class TestHedgingExecutorDelayed:
    """HedgingExecutor DELAYED 모드 테스트."""

    def test_delayed_mode_primary_fast_enough(self):
        """DELAYED 모드: Primary가 충분히 빠른 경우."""
        config = HedgingConfig(
            mode=HedgingMode.DELAYED,
            delay=0.3,  # 300ms
            timeout=5.0,
        )
        executor = HedgingExecutor(config)

        secondary_called = {"called": False}

        def primary_fn():
            time.sleep(0.05)  # 50ms - delay보다 빠름
            return "primary"

        def secondary_fn():
            secondary_called["called"] = True
            time.sleep(0.05)
            return "secondary"

        candidates = [
            HedgingCandidate("primary", primary_fn),
            HedgingCandidate("secondary", secondary_fn),
        ]

        result = executor.execute(candidates)

        assert result.success is True
        assert result.value == "primary"
        assert result.source == "primary"
        assert result.hedged is False
        # Secondary는 호출되지 않음
        assert secondary_called["called"] is False

    def test_delayed_mode_primary_slow_secondary_wins(self):
        """DELAYED 모드: Primary가 느려서 Secondary 승리."""
        config = HedgingConfig(
            mode=HedgingMode.DELAYED,
            delay=0.05,  # 50ms
            timeout=5.0,
        )
        executor = HedgingExecutor(config)

        def primary_fn():
            time.sleep(0.5)  # 500ms - delay보다 느림
            return "primary"

        def secondary_fn():
            time.sleep(0.1)  # 100ms
            return "secondary"

        candidates = [
            HedgingCandidate("primary", primary_fn),
            HedgingCandidate("secondary", secondary_fn),
        ]

        result = executor.execute(candidates)

        assert result.success is True
        assert result.value == "secondary"
        assert result.source == "secondary"
        assert result.hedged is True


class TestHedgingExecutorFailures:
    """HedgingExecutor 실패 케이스 테스트."""

    def test_all_candidates_fail(self):
        """모든 후보 실패 테스트."""
        config = HedgingConfig(mode=HedgingMode.IMMEDIATE, timeout=5.0)
        executor = HedgingExecutor(config)

        def failing_fn():
            raise RuntimeError("Test failure")

        candidates = [
            HedgingCandidate("c1", failing_fn),
            HedgingCandidate("c2", failing_fn),
        ]

        with pytest.raises(HedgingAllFailedError) as exc_info:
            executor.execute(candidates)

        assert exc_info.value.candidates_tried == 2
        assert len(exc_info.value.errors) == 2

    def test_timeout(self):
        """타임아웃 테스트."""
        config = HedgingConfig(
            mode=HedgingMode.IMMEDIATE,
            timeout=0.1,  # 100ms 타임아웃
        )
        executor = HedgingExecutor(config)

        def slow_fn():
            time.sleep(0.3)  # 300ms - 타임아웃보다 느림
            return "slow"

        candidates = [
            HedgingCandidate("slow", slow_fn),
        ]

        with pytest.raises(HedgingTimeoutError) as exc_info:
            executor.execute(candidates)

        assert exc_info.value.timeout == 0.1

    def test_non_retryable_error_aborts_immediately(self):
        """확정적 에러 시 즉시 중단 테스트."""
        config = HedgingConfig(
            mode=HedgingMode.IMMEDIATE,
            timeout=5.0,
            non_retryable_exceptions=(PermissionError,),
        )
        executor = HedgingExecutor(config)

        def permission_error_fn():
            raise PermissionError("Access denied")

        def slow_success_fn():
            time.sleep(0.5)
            return "success"

        candidates = [
            HedgingCandidate("perm_error", permission_error_fn),
            HedgingCandidate("slow_success", slow_success_fn),
        ]

        with pytest.raises(NonRetryableHedgingError):
            executor.execute(candidates)

    def test_partial_failure_still_succeeds(self):
        """일부 실패해도 성공하는 후보가 있으면 성공."""
        config = HedgingConfig(mode=HedgingMode.IMMEDIATE, timeout=5.0)
        executor = HedgingExecutor(config)

        def failing_fn():
            raise RuntimeError("Failure")

        def success_fn():
            time.sleep(0.1)
            return "success"

        candidates = [
            HedgingCandidate("fail", failing_fn),
            HedgingCandidate("success", success_fn),
        ]

        result = executor.execute(candidates)

        assert result.success is True
        assert result.value == "success"
        assert result.candidates_failed >= 1


class TestHedgingExecutorContextVar:
    """ContextVar 전파 테스트."""

    def test_contextvar_propagation(self):
        """ContextVar가 스레드 간 전파되는지 테스트."""
        request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")

        config = HedgingConfig(mode=HedgingMode.IMMEDIATE, timeout=5.0)
        executor = HedgingExecutor(config)

        captured_ids = []

        def capture_context_fn():
            captured_ids.append(request_id_var.get())
            return "done"

        # 메인 스레드에서 ContextVar 설정
        request_id_var.set("test-request-123")

        candidates = [
            HedgingCandidate("c1", capture_context_fn),
            HedgingCandidate("c2", capture_context_fn),
        ]

        result = executor.execute(candidates)
        time.sleep(0.1)  # 다른 후보 완료 대기

        assert result.success is True
        # 최소 1개의 후보가 ContextVar를 전파받았어야 함
        assert any(id == "test-request-123" for id in captured_ids)


class TestHedgingLatencyTracker:
    """HedgingLatencyTracker 테스트."""

    def test_record_and_get_p50(self):
        """지연시간 기록 및 P50 계산 테스트."""
        tracker = HedgingLatencyTracker(window_size=100)

        # 충분한 샘플 기록 (최소 10개)
        for i in range(20):
            tracker.record(float(i * 10))  # 0, 10, 20, ..., 190

        p50 = tracker.get_p50_delay()
        assert p50 is not None
        # P50은 중앙값, 대략 95ms (밀리초) = 0.095초
        assert 0.05 <= p50 <= 0.15

    def test_insufficient_samples_returns_none(self):
        """샘플 부족 시 None 반환 테스트."""
        tracker = HedgingLatencyTracker(window_size=100)

        # 5개만 기록 (최소 10개 필요)
        for i in range(5):
            tracker.record(float(i * 10))

        assert tracker.get_p50_delay() is None
        assert tracker.has_enough_samples is False

    def test_sliding_window(self):
        """슬라이딩 윈도우 테스트."""
        tracker = HedgingLatencyTracker(window_size=10)

        # 10개 기록
        for i in range(10):
            tracker.record(100.0)

        # 10개 더 기록 (윈도우 크기 초과)
        for i in range(10):
            tracker.record(200.0)

        # 이전 값들은 밀려나고 200만 남아야 함
        stats = tracker.get_stats()
        assert stats["min_ms"] == 200.0
        assert stats["max_ms"] == 200.0

    def test_get_stats(self):
        """통계 조회 테스트."""
        tracker = HedgingLatencyTracker(window_size=100)

        for i in range(50):
            tracker.record(float(i))

        stats = tracker.get_stats()

        assert stats["count"] == 50
        assert stats["min_ms"] == 0.0
        assert stats["max_ms"] == 49.0
        assert "p50_ms" in stats
        assert "p95_ms" in stats

    def test_clear(self):
        """윈도우 초기화 테스트."""
        tracker = HedgingLatencyTracker(window_size=100)

        for i in range(20):
            tracker.record(100.0)

        assert tracker.sample_count == 20

        tracker.clear()

        assert tracker.sample_count == 0
        assert tracker.get_stats() == {"count": 0}


class TestAsyncHedgingExecutor:
    """AsyncHedgingExecutor 테스트."""

    @pytest.mark.asyncio
    async def test_async_immediate_mode_fast_wins(self):
        """비동기 IMMEDIATE 모드: 빠른 후보 선택."""
        config = HedgingConfig(mode=HedgingMode.IMMEDIATE, timeout=5.0)
        executor = AsyncHedgingExecutor(config)

        async def slow_fn():
            await asyncio.sleep(0.5)
            return "slow"

        async def fast_fn():
            await asyncio.sleep(0.05)
            return "fast"

        candidates = [
            HedgingCandidate("slow", slow_fn),
            HedgingCandidate("fast", fast_fn),
        ]

        result = await executor.execute(candidates)

        assert result.success is True
        assert result.value == "fast"
        assert result.source == "fast"

    @pytest.mark.asyncio
    async def test_async_delayed_mode(self):
        """비동기 DELAYED 모드 테스트."""
        config = HedgingConfig(
            mode=HedgingMode.DELAYED,
            delay=0.1,
            timeout=5.0,
        )
        executor = AsyncHedgingExecutor(config)

        async def primary_fn():
            await asyncio.sleep(0.05)  # delay보다 빠름
            return "primary"

        async def secondary_fn():
            await asyncio.sleep(0.05)
            return "secondary"

        candidates = [
            HedgingCandidate("primary", primary_fn),
            HedgingCandidate("secondary", secondary_fn),
        ]

        result = await executor.execute(candidates)

        assert result.success is True
        assert result.value == "primary"
        assert result.hedged is False

    @pytest.mark.asyncio
    async def test_async_all_fail(self):
        """비동기 모든 후보 실패 테스트."""
        config = HedgingConfig(mode=HedgingMode.IMMEDIATE, timeout=5.0)
        executor = AsyncHedgingExecutor(config)

        async def failing_fn():
            raise RuntimeError("Async failure")

        candidates = [
            HedgingCandidate("c1", failing_fn),
            HedgingCandidate("c2", failing_fn),
        ]

        with pytest.raises(HedgingAllFailedError):
            await executor.execute(candidates)

    @pytest.mark.asyncio
    async def test_async_sync_function_support(self):
        """비동기 실행기에서 동기 함수 지원 테스트."""
        config = HedgingConfig(mode=HedgingMode.IMMEDIATE, timeout=5.0)
        executor = AsyncHedgingExecutor(config)

        def sync_fn():
            return "sync_result"

        candidates = [
            HedgingCandidate("sync", sync_fn),
        ]

        result = await executor.execute(candidates)

        assert result.success is True
        assert result.value == "sync_result"


class TestHedgingExecutorMaxCandidates:
    """max_candidates 제한 테스트."""

    def test_max_candidates_limits_execution(self):
        """max_candidates가 후보 수를 제한하는지 테스트."""
        config = HedgingConfig(
            mode=HedgingMode.IMMEDIATE,
            timeout=5.0,
            max_candidates=2,
        )
        executor = HedgingExecutor(config)

        executed = []

        def make_fn(name):
            def fn():
                executed.append(name)
                time.sleep(0.1)
                return name

            return fn

        candidates = [
            HedgingCandidate("c1", make_fn("c1")),
            HedgingCandidate("c2", make_fn("c2")),
            HedgingCandidate("c3", make_fn("c3")),  # 이건 실행되면 안 됨
            HedgingCandidate("c4", make_fn("c4")),  # 이건 실행되면 안 됨
        ]

        result = executor.execute(candidates)
        time.sleep(0.3)  # 모든 후보 완료 대기

        assert result.success is True
        # c3, c4는 실행되지 않아야 함
        assert "c3" not in executed
        assert "c4" not in executed


class TestHedgingModes:
    """HedgingMode enum 테스트."""

    def test_mode_values(self):
        """모드 값 테스트."""
        assert HedgingMode.IMMEDIATE.value == "immediate"
        assert HedgingMode.DELAYED.value == "delayed"
        assert HedgingMode.ADAPTIVE.value == "adaptive"

    def test_mode_from_string(self):
        """문자열에서 모드 생성 테스트."""
        assert HedgingMode("immediate") == HedgingMode.IMMEDIATE
        assert HedgingMode("delayed") == HedgingMode.DELAYED
        assert HedgingMode("adaptive") == HedgingMode.ADAPTIVE
