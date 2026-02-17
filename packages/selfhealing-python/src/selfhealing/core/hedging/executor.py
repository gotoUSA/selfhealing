"""
Hedging Executor - 병렬 실행 및 첫 응답 선택 (동기).

여러 후보 함수를 ThreadPoolExecutor로 병렬 실행하고,
가장 먼저 성공한 응답을 반환합니다. ContextVar 전파를 지원합니다.
"""

from __future__ import annotations

import contextvars
import logging
import threading
import time
from concurrent.futures import (
    CancelledError,
    Future,
    ThreadPoolExecutor,
    TimeoutError as FuturesTimeoutError,
    as_completed,
)
from typing import TypeVar

from selfhealing.core.hedging.config import (
    HedgingCandidate,
    HedgingConfig,
    HedgingMode,
)
from selfhealing.core.hedging.exceptions import (
    HedgingAllFailedError,
    HedgingTimeoutError,
    NonRetryableHedgingError,
)
from selfhealing.core.hedging.latency_tracker import HedgingLatencyTracker
from selfhealing.core.hedging.result import HedgingResult

logger = logging.getLogger(__name__)

T = TypeVar("T")


class HedgingExecutor:
    """
    헷징 실행기 (동기).

    여러 후보를 병렬로 실행하고 가장 빠른 성공 응답을 반환합니다.
    ContextVar 전파를 지원하여 요청 컨텍스트가 스레드 간 유지됩니다.

    Usage:
        executor = HedgingExecutor(config)

        candidates = [
            HedgingCandidate("primary", lambda: api_call_region_a()),
            HedgingCandidate("secondary", lambda: api_call_region_b()),
        ]

        result = executor.execute(candidates)
        print(f"Result from {result.source}: {result.value}")
    """

    # 공유 스레드 풀
    _executor: ThreadPoolExecutor | None = None
    _executor_lock = threading.Lock()

    @classmethod
    def _get_executor(cls, max_workers: int = 10) -> ThreadPoolExecutor:
        """공유 스레드 풀 반환."""
        if cls._executor is None:
            with cls._executor_lock:
                if cls._executor is None:
                    cls._executor = ThreadPoolExecutor(
                        max_workers=max_workers,
                        thread_name_prefix="hedging",
                    )
        return cls._executor

    @classmethod
    def shutdown_executor(cls) -> None:
        """스레드 풀 종료 (테스트/종료 시 사용)."""
        with cls._executor_lock:
            if cls._executor is not None:
                cls._executor.shutdown(wait=True)
                cls._executor = None

    def __init__(
        self,
        config: HedgingConfig | None = None,
        latency_tracker: HedgingLatencyTracker | None = None,
    ):
        """
        Args:
            config: 헷징 설정. None이면 기본값 사용.
            latency_tracker: ADAPTIVE 모드용 지연시간 추적기 (선택)
        """
        self._config = config or HedgingConfig()
        self._latency_tracker = latency_tracker

    def _submit_with_context(self, executor: ThreadPoolExecutor, fn: callable) -> Future:
        """
        ContextVar를 유지하면서 스레드 풀에 작업 제출.

        스레드 풀 작업에서 요청 컨텍스트(trace_id, user_id 등)가 유지되도록
        현재 컨텍스트를 복사하여 실행합니다.
        """
        ctx = contextvars.copy_context()

        def wrapper():
            return ctx.run(fn)

        return executor.submit(wrapper)

    def _is_non_retryable(self, exc: Exception) -> bool:
        """
        재시도 불가 예외인지 확인.

        PermissionError, ValueError 등 재시도해도 결과가 변하지 않는
        확정적 에러를 식별합니다.
        """
        # 타입 기반 체크
        if isinstance(exc, self._config.non_retryable_exceptions):
            return True

        # HTTP 상태 코드 기반 체크 (HTTPError 등)
        status_code = getattr(exc, "status_code", None)
        if status_code and status_code in self._config.non_retryable_http_codes:
            return True

        return False

    def execute(self, candidates: list[HedgingCandidate]) -> HedgingResult[T]:
        """
        헷징 실행.

        Args:
            candidates: 실행할 후보 목록

        Returns:
            HedgingResult: 첫 성공 응답

        Raises:
            HedgingAllFailedError: 모든 후보 실패
            HedgingTimeoutError: 타임아웃
            ValueError: 후보 없음
        """
        if not candidates:
            raise ValueError("At least one candidate required")

        # 후보 수 제한
        candidates = candidates[: self._config.max_candidates]

        # 모드에 따른 실행
        if self._config.mode == HedgingMode.IMMEDIATE:
            return self._execute_immediate(candidates)
        elif self._config.mode == HedgingMode.DELAYED:
            return self._execute_delayed(candidates)
        else:  # ADAPTIVE
            return self._execute_adaptive(candidates)

    def _execute_immediate(self, candidates: list[HedgingCandidate]) -> HedgingResult[T]:
        """
        IMMEDIATE 모드: 모든 후보 즉시 실행.

        모든 후보를 동시에 실행하고 가장 빠른 성공 응답을 반환합니다.
        """
        start_time = time.perf_counter()
        executor = self._get_executor()

        # 모든 후보 동시 제출 (ContextVar 전파)
        future_to_candidate: dict[Future, HedgingCandidate] = {}
        for candidate in candidates:
            future = self._submit_with_context(executor, candidate.fn)
            future_to_candidate[future] = candidate

        # 첫 성공 응답 대기
        return self._wait_for_first_success(
            future_to_candidate,
            start_time,
            hedged=(len(candidates) > 1),
        )

    def _execute_delayed(self, candidates: list[HedgingCandidate]) -> HedgingResult[T]:
        """
        DELAYED 모드: Primary 먼저, 지연 시 Secondary 추가.

        Primary를 먼저 실행하고, delay 시간 내 응답이 없으면
        Secondary 후보들을 추가 실행합니다.
        """
        if len(candidates) < 2:
            return self._execute_immediate(candidates)

        start_time = time.perf_counter()
        executor = self._get_executor()

        # Primary 먼저 실행 (ContextVar 전파)
        primary = candidates[0]
        primary_future = self._submit_with_context(executor, primary.fn)

        future_to_candidate: dict[Future, HedgingCandidate] = {primary_future: primary}

        try:
            # Primary 응답 대기 (delay 시간만큼)
            result = primary_future.result(timeout=self._config.delay)
            latency_ms = (time.perf_counter() - start_time) * 1000

            # ADAPTIVE 모드용 지연시간 기록
            if self._latency_tracker:
                self._latency_tracker.record(latency_ms)

            return HedgingResult(
                value=result,
                success=True,
                source=primary.name,
                latency_ms=latency_ms,
                hedged=False,
                candidates_tried=1,
                candidates_succeeded=1,
                candidates_failed=0,
            )

        except FuturesTimeoutError:
            # Primary가 delay 내에 응답하지 않음 → Secondary 추가 (ContextVar 전파)
            logger.debug(
                f"[Hedging] Primary '{primary.name}' did not respond within "
                f"{self._config.delay}s, adding secondary candidates"
            )
            for candidate in candidates[1:]:
                future = self._submit_with_context(executor, candidate.fn)
                future_to_candidate[future] = candidate

            return self._wait_for_first_success(
                future_to_candidate,
                start_time,
                hedged=True,
            )

        except Exception as e:
            # Primary가 delay 내에 실패함 → Secondary 추가
            logger.debug(f"[Hedging] Primary '{primary.name}' failed within delay: {e}")
            for candidate in candidates[1:]:
                future = self._submit_with_context(executor, candidate.fn)
                future_to_candidate[future] = candidate

            return self._wait_for_first_success(
                future_to_candidate,
                start_time,
                hedged=True,
            )

    def _execute_adaptive(self, candidates: list[HedgingCandidate]) -> HedgingResult[T]:
        """
        ADAPTIVE 모드: P50 기반 동적 delay 계산.

        과거 지연시간을 기반으로 delay를 동적으로 조정합니다.
        데이터가 충분하지 않으면 DELAYED 모드와 동일하게 동작합니다.
        """
        if self._latency_tracker and self._latency_tracker.has_enough_samples:
            adaptive_delay = self._latency_tracker.get_p50_delay()
            if adaptive_delay:
                # 기존 delay를 P50 기반으로 대체
                original_delay = self._config.delay
                self._config.delay = adaptive_delay
                logger.debug(f"[Hedging] ADAPTIVE delay: {original_delay}s -> " f"{adaptive_delay}s (P50)")

        return self._execute_delayed(candidates)

    def _process_completed_future(
        self,
        future: Future,
        candidate: HedgingCandidate,
        latency_ms: float,
        primary_candidate: HedgingCandidate,
        primary_latency_ms: float | None,
        hedged: bool,
        future_count: int,
        succeeded: int,
        failed: int,
    ) -> tuple[HedgingResult[T] | None, bool, int, int, float | None, str | None]:
        """완료된 Future 처리. 성공 시 결과 반환, 실패 시 None."""
        if candidate == primary_candidate:
            primary_latency_ms = latency_ms

        try:
            result = future.result(timeout=0)

            # ADAPTIVE 모드용 지연시간 기록
            if self._latency_tracker:
                self._latency_tracker.record(latency_ms)

            is_hedged = hedged and candidate != primary_candidate

            return (
                HedgingResult(
                    value=result,
                    success=True,
                    source=candidate.name,
                    latency_ms=latency_ms,
                    hedged=is_hedged,
                    candidates_tried=future_count,
                    candidates_succeeded=succeeded + 1,
                    candidates_failed=failed,
                    metadata={"primary_latency_ms": primary_latency_ms},
                ),
                True,
                succeeded + 1,
                failed,
                primary_latency_ms,
                None,
            )

        except CancelledError:
            return None, False, succeeded, failed, primary_latency_ms, None

        except Exception as e:
            error_msg = f"{candidate.name}: {e}"
            logger.warning(f"[Hedging] {candidate.name} failed: {e}")

            if self._is_non_retryable(e):
                return None, False, succeeded, failed + 1, primary_latency_ms, f"non_retryable:{error_msg}"

            return None, False, succeeded, failed + 1, primary_latency_ms, error_msg

    def _handle_completed_future(
        self,
        future: Future,
        future_to_candidate: dict[Future, HedgingCandidate],
        start_time: float,
        primary_candidate: HedgingCandidate,
        primary_latency_ms: float | None,
        hedged: bool,
        succeeded: int,
        failed: int,
        errors: list[str],
    ) -> tuple[HedgingResult[T] | None, int, int, float | None]:
        """단일 완료된 Future 처리. 성공 시 HedgingResult 반환."""
        candidate = future_to_candidate[future]
        latency_ms = (time.perf_counter() - start_time) * 1000

        result, success, succeeded, failed, primary_latency_ms, error = self._process_completed_future(
            future,
            candidate,
            latency_ms,
            primary_candidate,
            primary_latency_ms,
            hedged,
            len(future_to_candidate),
            succeeded,
            failed,
        )

        if success and result:
            if self._config.cancel_on_success:
                for f in future_to_candidate:
                    if f != future and not f.done():
                        f.cancel()
            return result, succeeded, failed, primary_latency_ms

        if error:
            if error.startswith("non_retryable:"):
                for f in future_to_candidate:
                    if not f.done():
                        f.cancel()
                raise NonRetryableHedgingError(error[14:])
            errors.append(error)

        return None, succeeded, failed, primary_latency_ms

    def _wait_for_first_success(
        self,
        future_to_candidate: dict[Future, HedgingCandidate],
        start_time: float,
        hedged: bool,
    ) -> HedgingResult[T]:
        """
        첫 성공 응답 대기.

        as_completed()를 사용하여 가장 먼저 완료된 Future부터 처리하고,
        첫 성공 시 나머지를 취소합니다.
        """
        remaining_timeout = self._config.timeout - (time.perf_counter() - start_time)

        succeeded = 0
        failed = 0
        errors: list[str] = []
        primary_candidate = list(future_to_candidate.values())[0]
        primary_latency_ms: float | None = None

        try:
            for future in as_completed(
                future_to_candidate.keys(),
                timeout=max(0.001, remaining_timeout),
            ):
                hedging_result, succeeded, failed, primary_latency_ms = self._handle_completed_future(
                    future,
                    future_to_candidate,
                    start_time,
                    primary_candidate,
                    primary_latency_ms,
                    hedged,
                    succeeded,
                    failed,
                    errors,
                )
                if hedging_result is not None:
                    return hedging_result

        except FuturesTimeoutError:
            # 전체 타임아웃
            for f in future_to_candidate:
                if not f.done():
                    f.cancel()
            raise HedgingTimeoutError(self._config.timeout)

        # 모든 후보 실패
        raise HedgingAllFailedError(
            candidates_tried=len(future_to_candidate),
            errors=errors,
        )
