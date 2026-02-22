"""
Semaphore Bulkhead - 세마포어 기반 동시 실행 제한.

I/O 바운드 작업에 적합한 동기 격벽 구현입니다.
threading.BoundedSemaphore를 사용하여 동시 실행 수를 제한합니다.

Usage:
    bulkhead = SemaphoreBulkhead("database", max_concurrent=10)

    # 타임아웃 없이 즉시 실패 (논블로킹)
    with bulkhead.acquire():
        do_database_work()

    # 최대 1초 대기 후 실패
    with bulkhead.acquire(timeout=1.0):
        do_database_work()
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

import structlog

from selfhealing.resilience.bulkhead.base import (
    Bulkhead,
    BulkheadState,
    BulkheadType,
)
from selfhealing.resilience.bulkhead.exceptions import BulkheadFullError

logger = structlog.get_logger()


class SemaphoreBulkhead(Bulkhead):
    """
    세마포어 기반 격벽.

    동시 실행 수를 제한하여 리소스 고갈을 방지합니다.
    I/O 바운드 작업(DB 쿼리, 캐시 조회 등)에 적합합니다.

    Features:
    - 최대 동시 실행 수 제한
    - 타임아웃 기반 대기 지원
    - 거부 통계 추적
    """

    def __init__(
        self,
        name: str,
        max_concurrent: int = 10,
        fair: bool = True,  # noqa: ARG002 - 향후 공정 스케줄링 구현용
    ):
        """
        Args:
            name: 격벽 이름 (도메인 식별자)
            max_concurrent: 최대 동시 실행 수
            fair: True이면 FIFO 순서 보장 (향후 구현)
        """
        self._name = name
        self._max_concurrent = max_concurrent
        self._semaphore = threading.BoundedSemaphore(max_concurrent)
        self._lock = threading.Lock()

        # 통계
        self._active_count = 0
        self._waiting_count = 0
        self._rejected_count = 0
        self._last_rejection_time: datetime | None = None

    @property
    def name(self) -> str:
        """격벽 이름."""
        return self._name

    @contextmanager
    def acquire(self, timeout: float | None = None) -> Generator[None, None, None]:
        """
        리소스 획득.

        Args:
            timeout: 대기 타임아웃 (초). None이면 즉시 실패 (논블로킹).

        Yields:
            None

        Raises:
            BulkheadFullError: 리소스 획득 실패 시
        """
        acquired = False
        try:
            with self._lock:
                self._waiting_count += 1

            # timeout=None이면 blocking=False로 즉시 시도
            if timeout is None:
                acquired = self._semaphore.acquire(blocking=False)
            else:
                acquired = self._semaphore.acquire(blocking=True, timeout=timeout)

            with self._lock:
                self._waiting_count -= 1
                if acquired:
                    self._active_count += 1
                else:
                    self._rejected_count += 1
                    self._last_rejection_time = datetime.now(timezone.utc)

            if not acquired:
                raise BulkheadFullError(
                    bulkhead_name=self._name,
                    max_concurrent=self._max_concurrent,
                    active_count=self._active_count,
                )

            yield

        finally:
            if acquired:
                self._semaphore.release()
                with self._lock:
                    self._active_count -= 1

    def try_acquire(self, timeout: float | None = None) -> bool:
        """
        획득 시도. timeout이 None이면 논블로킹, 지정 시 해당 시간까지 대기.

        Args:
            timeout: 대기 타임아웃 (초). None이면 즉시 성공/실패.

        Returns:
            획득 성공 시 True, 실패 시 False
        """
        if timeout is None:
            acquired = self._semaphore.acquire(blocking=False)
        else:
            acquired = self._semaphore.acquire(blocking=True, timeout=timeout)
        if acquired:
            with self._lock:
                self._active_count += 1
        return acquired

    def release(self) -> None:
        """리소스 반환."""
        self._semaphore.release()
        with self._lock:
            self._active_count = max(0, self._active_count - 1)

    def get_state(self) -> BulkheadState:
        """현재 상태 반환."""
        with self._lock:
            return BulkheadState(
                name=self._name,
                bulkhead_type=BulkheadType.SEMAPHORE,
                max_concurrent=self._max_concurrent,
                active_count=self._active_count,
                waiting_count=self._waiting_count,
                rejected_count=self._rejected_count,
                last_rejection_time=self._last_rejection_time,
            )
