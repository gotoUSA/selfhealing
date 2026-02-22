"""
Thread Pool Bulkhead - 스레드 풀 기반 격리.

CPU 바운드 작업에 적합한 스레드 풀 격벽입니다.
독립적인 스레드 풀에서 작업을 실행하여 완전한 격리를 제공합니다.

ContextVar 전파:
- contextvars.copy_context()를 사용하여 부모 스레드의 컨텍스트 변수를 워커 스레드로 전파
- 테스트 모드 컨텍스트(_is_synthetic_request), 요청 오버라이드 등이 유지됨

Usage:
    bulkhead = ThreadPoolBulkhead("external_api", max_workers=5)

    # Future 반환 (비동기 실행)
    future = bulkhead.submit(api_call, arg1, arg2)
    result = future.result(timeout=10.0)

    # 동기 실행 (결과 대기)
    result = bulkhead.execute(api_call, arg1, timeout=10.0)
"""

from __future__ import annotations

import contextvars
import structlog
import threading
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Generator, TypeVar

from selfhealing.resilience.bulkhead.base import (
    Bulkhead,
    BulkheadState,
    BulkheadType,
)
from selfhealing.resilience.bulkhead.exceptions import (
    BulkheadFullError,
    BulkheadTimeoutError,
)

logger = structlog.get_logger()

T = TypeVar("T")


class ThreadPoolBulkhead(Bulkhead):
    """
    스레드 풀 기반 격벽.

    독립적인 스레드 풀에서 작업을 실행하여 완전한 격리를 제공합니다.
    contextvars.copy_context()로 요청 ID, 트레이싱 컨텍스트 등이 워커 스레드로 전파됩니다.

    Features:
    - 독립 스레드 풀로 완전한 격리
    - ContextVar 자동 전파 (테스트 모드, 요청 오버라이드 등)
    - 대기 큐 크기 제한
    - 타임아웃 지원
    """

    def __init__(
        self,
        name: str,
        max_workers: int = 5,
        queue_size: int = 10,
        thread_name_prefix: str | None = None,
    ):
        """
        Args:
            name: 격벽 이름 (도메인 식별자)
            max_workers: 최대 워커 스레드 수
            queue_size: 대기 큐 크기 (초과 시 요청 거부)
            thread_name_prefix: 스레드 이름 접두사
        """
        self._name = name
        self._max_workers = max_workers
        self._queue_size = queue_size
        self._thread_name_prefix = thread_name_prefix or f"bulkhead_{name}"

        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=self._thread_name_prefix,
        )
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
    def acquire(self, timeout: float | None = None) -> Generator[None, None, None]:  # noqa: ARG002 - 인터페이스 호환성
        """
        Thread Pool Bulkhead에서는 submit/execute 사용 권장.
        이 메서드는 Bulkhead 인터페이스 호환성을 위해 제공됩니다.

        Args:
            timeout: 미사용 (인터페이스 호환성)

        Raises:
            BulkheadFullError: 큐가 가득 찬 경우
        """
        with self._lock:
            if self._active_count >= self._max_workers + self._queue_size:
                self._rejected_count += 1
                self._last_rejection_time = datetime.now(timezone.utc)
                raise BulkheadFullError(
                    bulkhead_name=self._name,
                    max_concurrent=self._max_workers,
                    active_count=self._active_count,
                )
            self._active_count += 1

        try:
            yield
        finally:
            with self._lock:
                self._active_count -= 1

    def try_acquire(self) -> bool:
        """논블로킹 획득 시도."""
        with self._lock:
            if self._active_count >= self._max_workers + self._queue_size:
                return False
            self._active_count += 1
            return True

    def release(self) -> None:
        """리소스 반환."""
        with self._lock:
            self._active_count = max(0, self._active_count - 1)

    def submit(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> Future[T]:
        """
        작업 비동기 제출 (ContextVar 전파 포함).

        contextvars.copy_context()를 사용하여 현재 스레드의 ContextVar들을
        워커 스레드로 전파합니다. 이를 통해 테스트 모드 플래그, 요청 오버라이드 등이
        스레드 풀 내에서도 유지됩니다.

        Args:
            fn: 실행할 함수
            *args: 위치 인자
            **kwargs: 키워드 인자

        Returns:
            Future 객체

        Raises:
            BulkheadFullError: 대기 큐가 가득 찬 경우
        """
        with self._lock:
            if self._waiting_count >= self._queue_size:
                self._rejected_count += 1
                self._last_rejection_time = datetime.now(timezone.utc)
                raise BulkheadFullError(
                    bulkhead_name=self._name,
                    max_concurrent=self._max_workers,
                    active_count=self._active_count,
                )
            self._waiting_count += 1

        # 현재 컨텍스트 복사 (ContextVar 전파)
        ctx = contextvars.copy_context()

        def wrapped() -> T:
            with self._lock:
                self._waiting_count -= 1
                self._active_count += 1
            try:
                # 복사된 컨텍스트 내에서 실행
                return ctx.run(fn, *args, **kwargs)
            finally:
                with self._lock:
                    self._active_count -= 1

        return self._executor.submit(wrapped)

    def execute(
        self,
        fn: Callable[..., T],
        *args: Any,
        timeout: float = 30.0,
        **kwargs: Any,
    ) -> T:
        """
        작업 동기 실행.

        submit()을 호출하고 결과를 대기합니다.

        Args:
            fn: 실행할 함수
            *args: 위치 인자
            timeout: 실행 타임아웃 (초)
            **kwargs: 키워드 인자

        Returns:
            함수 실행 결과

        Raises:
            BulkheadFullError: 대기 큐가 가득 찬 경우
            BulkheadTimeoutError: 타임아웃 발생 시
        """
        future = self.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            future.cancel()
            raise BulkheadTimeoutError(
                bulkhead_name=self._name,
                timeout=timeout,
            )

    def get_state(self) -> BulkheadState:
        """현재 상태 반환."""
        with self._lock:
            return BulkheadState(
                name=self._name,
                bulkhead_type=BulkheadType.THREAD_POOL,
                max_concurrent=self._max_workers,
                active_count=self._active_count,
                waiting_count=self._waiting_count,
                rejected_count=self._rejected_count,
                last_rejection_time=self._last_rejection_time,
            )

    def shutdown(self, wait: bool = True) -> None:
        """
        스레드 풀 종료.

        Args:
            wait: True이면 대기 중인 작업 완료 후 종료
        """
        self._executor.shutdown(wait=wait)
