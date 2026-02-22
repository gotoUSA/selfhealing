"""
Async Hedging Executor - 비동기 병렬 실행.

asyncio를 사용하여 코루틴 후보들을 병렬 실행하고,
가장 먼저 성공한 응답을 반환합니다.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, TypeVar

import structlog

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

logger = structlog.get_logger()

T = TypeVar("T")


class AsyncHedgingExecutor:
    """
    비동기 헷징 실행기.

    asyncio.wait(return_when=FIRST_COMPLETED) 패턴을 사용하여
    코루틴 후보들을 병렬 실행하고 가장 빠른 성공 응답을 반환합니다.

    Usage:
        executor = AsyncHedgingExecutor(config)

        candidates = [
            HedgingCandidate("primary", lambda: async_api_call_a()),
            HedgingCandidate("secondary", lambda: async_api_call_b()),
        ]

        result = await executor.execute(candidates)
    """

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

    def _is_non_retryable(self, exc: Exception) -> bool:
        """
        재시도 불가 예외인지 확인.

        PermissionError, ValueError 등 재시도해도 결과가 변하지 않는
        확정적 에러를 식별합니다.
        """
        if isinstance(exc, self._config.non_retryable_exceptions):
            return True
        status_code = getattr(exc, "status_code", None)
        if status_code and status_code in self._config.non_retryable_http_codes:
            return True
        return False

    async def execute(self, candidates: list[HedgingCandidate]) -> HedgingResult[T]:
        """
        비동기 헷징 실행.

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

        candidates = candidates[: self._config.max_candidates]

        if self._config.mode == HedgingMode.IMMEDIATE:
            return await self._execute_immediate(candidates)
        elif self._config.mode == HedgingMode.DELAYED:
            return await self._execute_delayed(candidates)
        else:  # ADAPTIVE
            return await self._execute_adaptive(candidates)

    async def _execute_immediate(self, candidates: list[HedgingCandidate]) -> HedgingResult[T]:
        """
        IMMEDIATE 모드: 모든 후보 즉시 실행.

        모든 후보를 동시에 Task로 생성하고 가장 빠른 성공 응답을 반환합니다.
        """
        start_time = time.perf_counter()

        # 모든 후보를 Task로 생성
        tasks: dict[asyncio.Task, HedgingCandidate] = {}
        for candidate in candidates:
            # async 함수인 경우 직접 호출, 그렇지 않으면 to_thread 사용
            if asyncio.iscoroutinefunction(candidate.fn):
                coro = candidate.fn()
            else:
                coro = asyncio.to_thread(candidate.fn)
            task = asyncio.create_task(coro)
            tasks[task] = candidate

        return await self._wait_for_first_success(tasks, start_time)

    async def _execute_delayed(self, candidates: list[HedgingCandidate]) -> HedgingResult[T]:
        """
        DELAYED 모드: Primary 먼저, 지연 시 Secondary 추가.

        Primary를 먼저 실행하고, delay 시간 내 응답이 없으면
        Secondary 후보들을 추가 실행합니다.
        """
        if len(candidates) < 2:
            return await self._execute_immediate(candidates)

        start_time = time.perf_counter()
        primary = candidates[0]

        # Primary Task 생성
        if asyncio.iscoroutinefunction(primary.fn):
            primary_coro = primary.fn()
        else:
            primary_coro = asyncio.to_thread(primary.fn)

        primary_task = asyncio.create_task(primary_coro)

        try:
            # Primary가 delay 내에 응답하면 바로 반환
            result = await asyncio.wait_for(
                asyncio.shield(primary_task),
                timeout=self._config.delay,
            )
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

        except asyncio.TimeoutError:
            # delay 초과 → Secondary 추가
            logger.debug(
                "hedging.primary_respond_within_adding",
                primary=primary.name,
                self=self._config.delay,
            )
            tasks: dict[asyncio.Task, HedgingCandidate] = {primary_task: primary}

            for candidate in candidates[1:]:
                if asyncio.iscoroutinefunction(candidate.fn):
                    coro = candidate.fn()
                else:
                    coro = asyncio.to_thread(candidate.fn)
                task = asyncio.create_task(coro)
                tasks[task] = candidate

            return await self._wait_for_first_success(tasks, start_time, hedged=True)

        except Exception as e:
            # Primary가 delay 내에 실패함 → Secondary 추가
            logger.debug(
                "hedging.primary_failed_within_delay",
                primary=primary.name,
                error=e,
            )
            tasks: dict[asyncio.Task, HedgingCandidate] = {primary_task: primary}

            for candidate in candidates[1:]:
                if asyncio.iscoroutinefunction(candidate.fn):
                    coro = candidate.fn()
                else:
                    coro = asyncio.to_thread(candidate.fn)
                task = asyncio.create_task(coro)
                tasks[task] = candidate

            return await self._wait_for_first_success(tasks, start_time, hedged=True)

    async def _execute_adaptive(self, candidates: list[HedgingCandidate]) -> HedgingResult[T]:
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
                logger.debug(
                    "hedging.adaptive_delay",
                    original_delay=original_delay,
                    adaptive_delay=adaptive_delay,
                )

        return await self._execute_delayed(candidates)

    def _process_completed_task(
        self,
        task: asyncio.Task,
        candidate: HedgingCandidate,
        latency_ms: float,
        primary_candidate: HedgingCandidate,
        primary_latency_ms: float | None,
        hedged: bool,
        task_count: int,
        succeeded: int,
        failed: int,
    ) -> tuple[HedgingResult[T] | None, bool, int, int, float | None, str | None]:
        """완료된 태스크 처리. 성공 시 결과 반환, 실패 시 None."""
        if candidate == primary_candidate:
            primary_latency_ms = latency_ms

        try:
            result = task.result()

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
                    candidates_tried=task_count,
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

        except asyncio.CancelledError:
            return None, False, succeeded, failed, primary_latency_ms, None

        except Exception as e:
            error_msg = f"{candidate.name}: {e}"
            logger.warning(
                "hedging.failed",
                candidate=candidate.name,
                error=e,
            )

            # 확정적 에러 시 즉시 중단 표시
            if self._is_non_retryable(e):
                return None, False, succeeded, failed + 1, primary_latency_ms, f"non_retryable:{error_msg}"

            return None, False, succeeded, failed + 1, primary_latency_ms, error_msg

    def _handle_completed_async_tasks(
        self,
        done: set[asyncio.Task],
        tasks: dict[asyncio.Task, HedgingCandidate],
        pending: set[asyncio.Task],
        start_time: float,
        primary_candidate: HedgingCandidate,
        primary_latency_ms: float | None,
        hedged: bool,
        succeeded: int,
        failed: int,
        errors: list[str],
    ) -> tuple[HedgingResult[T] | None, int, int, float | None]:
        """완료된 비동기 태스크들을 처리. 첫 성공 시 HedgingResult 반환."""
        for task in done:
            candidate = tasks[task]
            latency_ms = (time.perf_counter() - start_time) * 1000

            result, success, succeeded, failed, primary_latency_ms, error = self._process_completed_task(
                task, candidate, latency_ms, primary_candidate, primary_latency_ms, hedged, len(tasks), succeeded, failed
            )

            if success and result:
                if self._config.cancel_on_success:
                    for p in pending:
                        p.cancel()
                return result, succeeded, failed, primary_latency_ms

            if error:
                if error.startswith("non_retryable:"):
                    for p in pending:
                        p.cancel()
                    raise NonRetryableHedgingError(error[14:])
                errors.append(error)

        return None, succeeded, failed, primary_latency_ms

    async def _wait_for_first_success(
        self,
        tasks: dict[asyncio.Task, HedgingCandidate],
        start_time: float,
        hedged: bool = False,
    ) -> HedgingResult[T]:
        """
        첫 성공 응답 대기.

        asyncio.wait(return_when=FIRST_COMPLETED)을 사용하여
        완료된 Task부터 처리하고, 첫 성공 시 나머지를 취소합니다.
        """
        succeeded = 0
        failed = 0
        errors: list[str] = []
        pending = set(tasks.keys())
        primary_candidate = list(tasks.values())[0]
        primary_latency_ms: float | None = None

        remaining_timeout = self._config.timeout - (time.perf_counter() - start_time)

        while pending and remaining_timeout > 0:
            try:
                done, pending = await asyncio.wait(
                    pending,
                    timeout=remaining_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                for p in pending:
                    p.cancel()
                raise

            result, succeeded, failed, primary_latency_ms = self._handle_completed_async_tasks(
                done,
                tasks,
                pending,
                start_time,
                primary_candidate,
                primary_latency_ms,
                hedged,
                succeeded,
                failed,
                errors,
            )
            if result is not None:
                return result

            remaining_timeout = self._config.timeout - (time.perf_counter() - start_time)

        # 모든 후보 실패 또는 타임아웃
        for p in pending:
            p.cancel()

        if remaining_timeout <= 0 and pending:
            raise HedgingTimeoutError(self._config.timeout)

        raise HedgingAllFailedError(
            candidates_tried=len(tasks),
            errors=errors,
        )
