"""
Bulkhead Base - 리소스 격리 추상 인터페이스.

격벽(Bulkhead) 패턴의 기본 인터페이스를 정의합니다.
선박 설계에서 유래한 패턴으로, 한 구역의 장애가 다른 구역으로 전파되지 않도록 격리합니다.

지원 유형:
- SEMAPHORE: 세마포어 기반 동시 실행 제한 (I/O 바운드 작업에 적합)
- THREAD_POOL: 스레드 풀 기반 격리 (CPU 바운드 작업에 적합)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from functools import wraps
from typing import Any, Callable, Generator, TypeVar

T = TypeVar("T")


class BulkheadType(str, Enum):
    """격벽 유형."""

    SEMAPHORE = "semaphore"
    """세마포어 기반 - 동시 실행 수만 제한"""

    THREAD_POOL = "thread_pool"
    """스레드 풀 기반 - 독립 스레드 풀에서 격리 실행"""


@dataclass
class BulkheadState:
    """격벽 현재 상태 데이터."""

    name: str
    """격벽 이름 (도메인 식별자)"""

    bulkhead_type: BulkheadType
    """격벽 유형"""

    max_concurrent: int
    """최대 동시 실행 허용 수"""

    active_count: int
    """현재 실행 중인 작업 수"""

    waiting_count: int
    """대기 중인 작업 수"""

    rejected_count: int
    """거부된 요청 총 수"""

    last_rejection_time: datetime | None = None
    """마지막 거부 시각"""

    @property
    def available_permits(self) -> int:
        """사용 가능한 허가 수."""
        return max(0, self.max_concurrent - self.active_count)

    @property
    def utilization_percent(self) -> float:
        """사용률 (0-100%)."""
        if self.max_concurrent == 0:
            return 0.0
        return (self.active_count / self.max_concurrent) * 100


class Bulkhead(ABC):
    """
    격벽(Bulkhead) 추상 인터페이스.

    리소스 격리를 통해 한 컴포넌트의 장애가 다른 컴포넌트로 전파되지 않도록 방지합니다.

    Usage (컨텍스트 매니저):
        bulkhead = SemaphoreBulkhead("database", max_concurrent=10)

        with bulkhead.acquire(timeout=5.0):
            do_database_work()

    Usage (데코레이터):
        @bulkhead.wrap
        def do_work():
            pass
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """격벽 이름 (도메인 식별자)."""
        pass

    @abstractmethod
    @contextmanager
    def acquire(self, timeout: float | None = None) -> Generator[None, None, None]:
        """
        리소스 획득 (컨텍스트 매니저).

        Args:
            timeout: 대기 타임아웃 (초). None이면 즉시 실패 (논블로킹).

        Yields:
            None

        Raises:
            BulkheadFullException: 리소스 획득 실패 시
        """
        pass

    @abstractmethod
    def try_acquire(self) -> bool:
        """
        리소스 획득 시도 (논블로킹).

        Returns:
            True이면 획득 성공, False이면 실패
        """
        pass

    @abstractmethod
    def release(self) -> None:
        """리소스 반환."""
        pass

    @abstractmethod
    def get_state(self) -> BulkheadState:
        """현재 상태 반환."""
        pass

    def wrap(self, fn: Callable[..., T]) -> Callable[..., T]:
        """
        함수를 격벽으로 감싸는 데코레이터.

        Args:
            fn: 감쌀 함수

        Returns:
            격벽이 적용된 함수
        """

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            with self.acquire():
                return fn(*args, **kwargs)

        return wrapper
