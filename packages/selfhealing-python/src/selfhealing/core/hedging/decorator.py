"""
Hedging Decorator - 동기/비동기 자동 분기.

함수에 @hedged 데코레이터를 적용하면 자동으로 헷징을 수행합니다.
동기/비동기 함수를 자동으로 감지하여 적절한 전략을 사용합니다.

Usage:
    # 동기 함수
    @hedged(fetch_from_region_b, fetch_from_region_c)
    def fetch_from_region_a():
        return api_call_a()

    # 비동기 함수 - 자동 감지
    @hedged(async_fetch_b, async_fetch_c)
    async def async_fetch_a():
        return await async_api_call_a()
"""

from __future__ import annotations

import asyncio
from functools import wraps
from typing import Any, Callable, TypeVar

from selfhealing.core.hedging.async_strategy import AsyncHedgingStrategy
from selfhealing.core.hedging.config import HedgingConfig, HedgingMode
from selfhealing.core.hedging.strategy import HedgingStrategy

T = TypeVar("T")


def hedged(
    *candidates: Callable[..., T],
    mode: HedgingMode = HedgingMode.DELAYED,
    timeout: float = 5.0,
    delay: float = 0.1,
    default: T | None = None,
    bulkhead_name: str | None = None,
    disable_on_load_level: str = "high",
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    헷징 데코레이터 - 동기/비동기 자동 분기.

    데코레이터로 함수를 감싸면 자동으로 헷징을 수행합니다.
    동기/비동기 함수를 자동으로 감지하여 적절한 전략을 사용합니다.

    Args:
        *candidates: 추가 후보 함수들 (Primary 함수 외에 실행할 함수들)
        mode: 헷징 모드 (기본: DELAYED)
        timeout: 전체 타임아웃 (초)
        delay: DELAYED 모드에서 Secondary 실행 전 대기 시간 (초)
        default: 모든 후보 실패 시 기본값
        bulkhead_name: 사용할 격벽 이름 (선택)
        disable_on_load_level: 이 부하 레벨 이상에서 헷징 비활성화

    Returns:
        데코레이터 함수

    Usage:
        # 동기 함수
        @hedged(fetch_from_region_b, fetch_from_region_c)
        def fetch_from_region_a():
            return api_call_a()

        # 비동기 함수 - 자동 감지
        @hedged(async_fetch_b, async_fetch_c)
        async def async_fetch_a():
            return await async_api_call_a()

        # Bulkhead 연동
        @hedged(fallback_fn, bulkhead_name="api_bulkhead")
        def api_call():
            return api.fetch()
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        config = HedgingConfig(
            mode=mode,
            timeout=timeout,
            delay=delay,
            bulkhead_name=bulkhead_name,
            disable_on_load_level=disable_on_load_level,
        )

        # 비동기 함수인 경우
        if asyncio.iscoroutinefunction(fn):

            @wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> T:
                """비동기 래퍼."""

                async def primary() -> T:
                    return await fn(*args, **kwargs)

                # 후보들도 같은 인자로 호출되도록 래핑
                # 클로저 문제 방지를 위해 c=c 사용
                async_candidates = []
                for c in candidates:
                    if asyncio.iscoroutinefunction(c):

                        async def wrapped_candidate(cand=c) -> T:
                            return await cand(*args, **kwargs)

                        async_candidates.append(wrapped_candidate)
                    else:

                        def sync_wrapped(cand=c) -> T:
                            return cand(*args, **kwargs)

                        async_candidates.append(sync_wrapped)

                strategy = AsyncHedgingStrategy(
                    candidates=async_candidates,
                    config=config,
                    default_value=default,
                )

                result = await strategy.execute(primary_fn=primary)

                if result.value is None and result.fallback_mode == "fail_fast":
                    raise RuntimeError(f"Hedging failed: {result.original_error}")

                return result.value

            return async_wrapper

        # 동기 함수인 경우
        @wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> T:
            """동기 래퍼."""

            def primary() -> T:
                return fn(*args, **kwargs)

            # 후보들도 같은 인자로 호출되도록 래핑
            # 클로저 문제 방지를 위해 c=c 사용
            wrapped_candidates = []
            for c in candidates:

                def wrapped_candidate(cand=c) -> T:
                    return cand(*args, **kwargs)

                wrapped_candidates.append(wrapped_candidate)

            strategy = HedgingStrategy(
                candidates=wrapped_candidates,
                config=config,
                default_value=default,
            )

            result = strategy.execute(primary_fn=primary)

            if result.value is None and result.fallback_mode == "fail_fast":
                raise RuntimeError(f"Hedging failed: {result.original_error}")

            return result.value

        return sync_wrapper

    return decorator


def hedged_sync(
    *candidates: Callable[..., T],
    mode: HedgingMode = HedgingMode.DELAYED,
    timeout: float = 5.0,
    delay: float = 0.1,
    default: T | None = None,
    bulkhead_name: str | None = None,
    disable_on_load_level: str = "high",
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    동기 전용 헷징 데코레이터.

    명시적으로 동기 함수에만 사용할 때 사용합니다.

    Args:
        *candidates: 추가 후보 함수들
        mode: 헷징 모드
        timeout: 전체 타임아웃 (초)
        delay: DELAYED 모드에서 Secondary 실행 전 대기 시간 (초)
        default: 모든 후보 실패 시 기본값
        bulkhead_name: 사용할 격벽 이름
        disable_on_load_level: 이 부하 레벨 이상에서 헷징 비활성화

    Returns:
        데코레이터 함수
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        config = HedgingConfig(
            mode=mode,
            timeout=timeout,
            delay=delay,
            bulkhead_name=bulkhead_name,
            disable_on_load_level=disable_on_load_level,
        )

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            def primary() -> T:
                return fn(*args, **kwargs)

            wrapped_candidates = []
            for c in candidates:

                def wrapped_candidate(cand=c) -> T:
                    return cand(*args, **kwargs)

                wrapped_candidates.append(wrapped_candidate)

            strategy = HedgingStrategy(
                candidates=wrapped_candidates,
                config=config,
                default_value=default,
            )

            result = strategy.execute(primary_fn=primary)

            if result.value is None and result.fallback_mode == "fail_fast":
                raise RuntimeError(f"Hedging failed: {result.original_error}")

            return result.value

        return wrapper

    return decorator


def hedged_async(
    *candidates: Callable[..., T],
    mode: HedgingMode = HedgingMode.DELAYED,
    timeout: float = 5.0,
    delay: float = 0.1,
    default: T | None = None,
    bulkhead_name: str | None = None,
    disable_on_load_level: str = "high",
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    비동기 전용 헷징 데코레이터.

    명시적으로 비동기 함수에만 사용할 때 사용합니다.

    Args:
        *candidates: 추가 후보 코루틴 함수들
        mode: 헷징 모드
        timeout: 전체 타임아웃 (초)
        delay: DELAYED 모드에서 Secondary 실행 전 대기 시간 (초)
        default: 모든 후보 실패 시 기본값
        bulkhead_name: 사용할 격벽 이름
        disable_on_load_level: 이 부하 레벨 이상에서 헷징 비활성화

    Returns:
        데코레이터 함수
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        config = HedgingConfig(
            mode=mode,
            timeout=timeout,
            delay=delay,
            bulkhead_name=bulkhead_name,
            disable_on_load_level=disable_on_load_level,
        )

        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            async def primary() -> T:
                return await fn(*args, **kwargs)

            async_candidates = []
            for c in candidates:
                if asyncio.iscoroutinefunction(c):

                    async def wrapped_candidate(cand=c) -> T:
                        return await cand(*args, **kwargs)

                    async_candidates.append(wrapped_candidate)
                else:

                    def sync_wrapped(cand=c) -> T:
                        return cand(*args, **kwargs)

                    async_candidates.append(sync_wrapped)

            strategy = AsyncHedgingStrategy(
                candidates=async_candidates,
                config=config,
                default_value=default,
            )

            result = await strategy.execute(primary_fn=primary)

            if result.value is None and result.fallback_mode == "fail_fast":
                raise RuntimeError(f"Hedging failed: {result.original_error}")

            return result.value

        return wrapper

    return decorator
