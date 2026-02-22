"""
Async Semaphore Bulkhead - asyncio.Semaphore 기반 비동기 격벽.

I/O 바운드 비동기 작업에 적합합니다.
threading.Semaphore와 달리 이벤트 루프를 블로킹하지 않습니다.

Usage:
    bulkhead = AsyncSemaphoreBulkhead("database", max_concurrent=10)

    async with bulkhead.acquire(timeout=1.0):
        await async_db_operation()
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator

import structlog

from selfhealing.resilience.bulkhead.base import (
    BulkheadState,
    BulkheadType,
)
from selfhealing.resilience.bulkhead.exceptions import BulkheadFullError

logger = structlog.get_logger()


class AsyncSemaphoreBulkhead:
    """
    비동기 세마포어 기반 격벽.

    asyncio 환경에서 동시 실행 수를 제한하여 리소스 고갈을 방지합니다.
    이벤트 루프를 블로킹하지 않으며, 비동기 I/O 작업에 적합합니다.

    Features:
    - asyncio.Semaphore 기반 논블로킹 대기
    - asyncio.wait_for 기반 타임아웃
    - 거부 통계 추적
    """

    def __init__(
        self,
        name: str,
        max_concurrent: int = 10,
    ):
        """
        Args:
            name: 격벽 이름 (도메인 식별자)
            max_concurrent: 최대 동시 실행 수
        """
        self._name = name
        self._max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._lock = asyncio.Lock()

        # 통계
        self._active_count = 0
        self._waiting_count = 0
        self._rejected_count = 0
        self._last_rejection_time: datetime | None = None

    @property
    def name(self) -> str:
        """격벽 이름."""
        return self._name

    @asynccontextmanager
    async def acquire(self, timeout: float | None = None) -> AsyncGenerator[None, None]:
        """
        비동기 리소스 획득.

        Args:
            timeout: 대기 타임아웃 (초). None이면 즉시 실패 (논블로킹).

        Yields:
            None

        Raises:
            BulkheadFullError: 리소스 획득 실패 시
        """
        acquired = False
        try:
            async with self._lock:
                self._waiting_count += 1

            # timeout=None이면 즉시 시도 (논블로킹)
            if timeout is None:
                # locked()가 True면 세마포어가 0이므로 즉시 실패
                if self._semaphore.locked():
                    acquired = False
                else:
                    # 즉시 획득 시도
                    try:
                        await asyncio.wait_for(
                            self._semaphore.acquire(),
                            timeout=0.001,  # 거의 즉시
                        )
                        acquired = True
                    except asyncio.TimeoutError:
                        acquired = False
            else:
                try:
                    await asyncio.wait_for(
                        self._semaphore.acquire(),
                        timeout=timeout,
                    )
                    acquired = True
                except asyncio.TimeoutError:
                    acquired = False

            async with self._lock:
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
                async with self._lock:
                    self._active_count -= 1

    async def try_acquire(self) -> bool:
        """
        논블로킹 획득 시도.

        Returns:
            획득 성공 시 True, 실패 시 False
        """
        if self._semaphore.locked():
            return False
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=0.001)
            async with self._lock:
                self._active_count += 1
            return True
        except asyncio.TimeoutError:
            return False

    async def release(self) -> None:
        """리소스 반환."""
        self._semaphore.release()
        async with self._lock:
            self._active_count = max(0, self._active_count - 1)

    def get_state(self) -> BulkheadState:
        """현재 상태 반환 (동기 메서드)."""
        return BulkheadState(
            name=self._name,
            bulkhead_type=BulkheadType.SEMAPHORE,
            max_concurrent=self._max_concurrent,
            active_count=self._active_count,
            waiting_count=self._waiting_count,
            rejected_count=self._rejected_count,
            last_rejection_time=self._last_rejection_time,
        )
