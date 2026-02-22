"""
Bulkhead Policy — 리소스 격리 기반 함수 래핑 Policy.

기존 Bulkhead ABC 구현체(SemaphoreBulkhead, ThreadPoolBulkhead)를
ResiliencePolicy.execute() 인터페이스로 래핑한다.

생성자에서 Bulkhead 인스턴스를 DI로 주입받아 Registry 미의존 테스트를 가능하게 하고,
bulkhead_policy() / async_bulkhead_policy() 팩토리 함수가 Registry 연동을 전담한다.

예외 처리 컨트랙트 (CircuitBreakerPolicy와 동일):
- BulkheadFullError → PolicyResult(outcome=REJECTED) 흡수
- BulkheadTimeoutError → PolicyResult(outcome=TIMEOUT) 흡수 (ThreadPool 전용)
- 함수 실행 중 비즈니스 예외 → raise 재전파 (상위 Policy에서 처리)

구성:
- BulkheadPolicy: 동기 격벽 (SemaphoreBulkhead, ThreadPoolBulkhead)
- AsyncBulkheadPolicy: 비동기 격벽 (AsyncSemaphoreBulkhead)
- bulkhead_policy(): 동기 팩토리 (BulkheadRegistry 싱글톤 통합)
- async_bulkhead_policy(): 비동기 팩토리 (BulkheadRegistry 싱글톤 통합)
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

import structlog

from selfhealing.interfaces.resilience_policy import (
    PolicyContext,
    PolicyOutcome,
    PolicyResult,
    ResiliencePolicy,
)
from selfhealing.resilience.bulkhead.async_semaphore import AsyncSemaphoreBulkhead
from selfhealing.resilience.bulkhead.base import Bulkhead
from selfhealing.resilience.bulkhead.exceptions import (
    BulkheadFullError,
    BulkheadTimeoutError,
)

logger = structlog.get_logger()

T = TypeVar("T")


# =============================================================================
# BulkheadPolicy — 동기 격벽 Policy
# =============================================================================


class BulkheadPolicy(ResiliencePolicy[T]):
    """
    동기 Bulkhead Policy — 리소스 격리.

    내부적으로 기존 Bulkhead ABC 구현체를 재사용한다.
    SemaphoreBulkhead는 acquire() 컨텍스트 매니저로,
    ThreadPoolBulkhead는 execute() 메서드로 실행한다.

    예외 처리 컨트랙트 (CircuitBreakerPolicy와 동일):
    - BulkheadFullError → PolicyResult(outcome=REJECTED) 흡수
    - BulkheadTimeoutError → PolicyResult(outcome=TIMEOUT) 흡수 (ThreadPool 전용)
    - 함수 실행 중 비즈니스 예외 → raise 재전파
    """

    def __init__(
        self,
        bulkhead: Bulkhead,
        timeout: float | None = None,
    ):
        """
        Args:
            bulkhead: Bulkhead ABC 구현체 (SemaphoreBulkhead, ThreadPoolBulkhead).
                      DI로 주입. Registry 조회는 bulkhead_policy() 팩토리를 사용한다.
            timeout: 리소스 획득 타임아웃 (초).
                     None이면 즉시 실패 (Fast Fail).
                     ThreadPoolBulkhead의 경우 execute() 실행 타임아웃으로 사용.
        """
        self._bulkhead = bulkhead
        self._timeout = timeout

    @property
    def name(self) -> str:
        """Policy 이름."""
        return "bulkhead"

    @property
    def bulkhead_name(self) -> str:
        """내부 Bulkhead 인스턴스의 도메인 식별자."""
        return self._bulkhead.name

    def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        context: PolicyContext | None = None,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        격벽 리소스 내에서 함수 실행.

        ThreadPoolBulkhead일 때:
        - isinstance 체크 → ThreadPoolBulkhead.execute() 호출
        - 독립 스레드 풀 격리 + ContextVar 전파 활용
        - BulkheadTimeoutError → PolicyOutcome.TIMEOUT 매핑

        SemaphoreBulkhead일 때:
        - with acquire(timeout) 컨텍스트 매니저
        - BulkheadFullError → PolicyOutcome.REJECTED 매핑

        함수 실행 중 비즈니스 예외는 raise로 재전파한다.
        """
        from selfhealing.resilience.bulkhead.threadpool import ThreadPoolBulkhead

        try:
            if isinstance(self._bulkhead, ThreadPoolBulkhead):
                # ThreadPool: 독립 스레드 풀에서 실행 + ContextVar 전파
                result = self._bulkhead.execute(
                    func,
                    *args,
                    timeout=self._timeout or 30.0,
                    **kwargs,
                )
            else:
                # Semaphore: 현재 스레드에서 실행
                with self._bulkhead.acquire(timeout=self._timeout):
                    result = func(*args, **kwargs)

            return PolicyResult(
                value=result,
                outcome=PolicyOutcome.SUCCESS,
                executed_policies=["bulkhead"],
                metadata={
                    "bulkhead_name": self._bulkhead.name,
                    "state": self._get_state_dict(),
                },
            )
        except BulkheadFullError as e:
            return PolicyResult(
                outcome=PolicyOutcome.REJECTED,
                error=e,
                executed_policies=["bulkhead"],
                metadata={
                    "bulkhead_name": self._bulkhead.name,
                    "state": self._get_state_dict(),
                },
            )
        except BulkheadTimeoutError as e:
            return PolicyResult(
                outcome=PolicyOutcome.TIMEOUT,
                error=e,
                executed_policies=["bulkhead"],
                metadata={
                    "bulkhead_name": self._bulkhead.name,
                    "timeout": self._timeout,
                    "state": self._get_state_dict(),
                },
            )
        # 함수 실행 중 비즈니스 예외는 catch하지 않고 상위로 전파 (raise)

    def _get_state_dict(self) -> dict:
        """Bulkhead 상태를 직렬화 가능한 딕셔너리로 변환."""
        state = self._bulkhead.get_state()
        return {
            "active_count": state.active_count,
            "max_concurrent": state.max_concurrent,
            "available_permits": state.available_permits,
            "utilization_percent": state.utilization_percent,
        }


# =============================================================================
# AsyncBulkheadPolicy — 비동기 격벽 Policy
# =============================================================================


class AsyncBulkheadPolicy:
    """
    비동기 Bulkhead Policy — AsyncResiliencePolicy Protocol 구현.

    AsyncSemaphoreBulkhead를 래핑하여 PolicyResult 형태로 반환한다.
    동기 BulkheadPolicy와 동일한 예외 처리 컨트랙트를 따른다.

    AsyncSemaphoreBulkhead는 Bulkhead(ABC)를 상속하지 않는 별도 클래스이므로,
    동기 BulkheadPolicy와 완전히 분리된 클래스로 구현한다.
    """

    def __init__(
        self,
        async_bulkhead: AsyncSemaphoreBulkhead,
        timeout: float | None = None,
    ):
        """
        Args:
            async_bulkhead: AsyncSemaphoreBulkhead 인스턴스 (DI).
            timeout: 리소스 획득 타임아웃 (초). None이면 즉시 실패.
        """
        self._async_bulkhead = async_bulkhead
        self._timeout = timeout

    @property
    def name(self) -> str:
        """Policy 이름."""
        return "bulkhead"

    @property
    def bulkhead_name(self) -> str:
        """내부 AsyncSemaphoreBulkhead의 도메인 식별자."""
        return self._async_bulkhead.name

    async def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        context: PolicyContext | None = None,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        비동기 격벽 리소스 내에서 함수 실행.

        BulkheadFullError → PolicyResult(outcome=REJECTED) 흡수.
        함수 실행 중 비즈니스 예외는 raise로 재전파.
        """
        try:
            async with self._async_bulkhead.acquire(timeout=self._timeout):
                result = await func(*args, **kwargs)
                return PolicyResult(
                    value=result,
                    outcome=PolicyOutcome.SUCCESS,
                    executed_policies=["bulkhead"],
                    metadata={
                        "bulkhead_name": self._async_bulkhead.name,
                        "state": self._get_state_dict(),
                    },
                )
        except BulkheadFullError as e:
            return PolicyResult(
                outcome=PolicyOutcome.REJECTED,
                error=e,
                executed_policies=["bulkhead"],
                metadata={
                    "bulkhead_name": self._async_bulkhead.name,
                    "state": self._get_state_dict(),
                },
            )
        # 함수 실행 중 비즈니스 예외는 catch하지 않고 상위로 전파

    def _get_state_dict(self) -> dict:
        """AsyncSemaphoreBulkhead 상태를 직렬화 가능한 딕셔너리로 변환."""
        state = self._async_bulkhead.get_state()
        return {
            "active_count": state.active_count,
            "max_concurrent": state.max_concurrent,
            "available_permits": state.available_permits,
            "utilization_percent": state.utilization_percent,
        }


# =============================================================================
# 팩토리 함수 — BulkheadRegistry 싱글톤 통합
# =============================================================================


def bulkhead_policy(
    name: str,
    max_concurrent: int | None = None,
    timeout: float | None = None,
    bulkhead_type: str = "semaphore",
) -> BulkheadPolicy:
    """
    BulkheadPolicy 팩토리 — BulkheadRegistry 싱글톤 통합.

    Registry의 get_or_create()를 호출하여 동일 name에 대해
    전역 단일 Bulkhead 인스턴스를 보장한다.

    Registry 의존성은 이 팩토리 함수에서만 발생한다.
    BulkheadPolicy 클래스 자체는 Registry를 모른다 (테스트 용이).

    Args:
        name: 도메인 이름 (Registry 키)
        max_concurrent: 최대 동시 실행 수 (None이면 Registry 기본값)
        timeout: 리소스 획득 타임아웃 (None이면 즉시 실패)
        bulkhead_type: "semaphore" 또는 "thread_pool"

    Returns:
        BulkheadPolicy 인스턴스 (Registry 싱글톤 Bulkhead 사용)
    """
    from selfhealing.resilience.bulkhead.registry import get_bulkhead_registry

    registry = get_bulkhead_registry()
    bulkhead = registry.get_or_create(
        name=name,
        max_concurrent=max_concurrent,
        bulkhead_type=bulkhead_type,
    )
    return BulkheadPolicy(bulkhead=bulkhead, timeout=timeout)


def async_bulkhead_policy(
    name: str,
    max_concurrent: int | None = None,
    timeout: float | None = None,
) -> AsyncBulkheadPolicy:
    """
    AsyncBulkheadPolicy 팩토리 — BulkheadRegistry 싱글톤 통합.

    Registry의 get_async()를 호출하여 동일 name에 대해
    전역 단일 AsyncSemaphoreBulkhead 인스턴스를 보장한다.

    Args:
        name: 도메인 이름 (Registry 키)
        max_concurrent: 최대 동시 실행 수 (None이면 Registry 기본값).
                        지정 시 동기 Bulkhead를 먼저 생성하여 비동기 인스턴스의 설정 기반으로 사용.
        timeout: 리소스 획득 타임아웃 (None이면 즉시 실패)

    Returns:
        AsyncBulkheadPolicy 인스턴스 (Registry 싱글톤 AsyncSemaphoreBulkhead 사용)
    """
    from selfhealing.resilience.bulkhead.registry import get_bulkhead_registry

    registry = get_bulkhead_registry()

    # 동기 Bulkhead가 없으면 먼저 생성 (비동기는 동기 설정 기반)
    if max_concurrent is not None:
        registry.get_or_create(name=name, max_concurrent=max_concurrent)

    async_bh = registry.get_async(name)
    return AsyncBulkheadPolicy(async_bulkhead=async_bh, timeout=timeout)
