"""
Bulkhead Decorator - 동기/비동기 자동 분기 데코레이터.

내부적으로 BulkheadPolicy/AsyncBulkheadPolicy를 사용하여
PolicyResult 기반으로 격벽 제어를 수행한다.

fallback 파라미터는 deprecated이며,
FallbackPolicy/AsyncFallbackPolicy + PolicyComposer/AsyncPolicyComposer
조합으로 전환되었다. BulkheadFullError일 때만 fallback이 활성화되며,
비즈니스 예외는 fallback 없이 재전파한다.

Usage:
    @bulkhead(ConnectionType.DATABASE)
    def db_operation():
        pass

    @bulkhead(ConnectionType.DATABASE)
    async def async_db_operation():
        pass

    @bulkhead("custom_domain", timeout=5.0)
    def custom_operation():
        pass
"""

from __future__ import annotations

import asyncio
import logging
import warnings
from functools import wraps
from typing import Any, Callable, TypeVar

from selfhealing.core.connection_health import ConnectionType

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _bulkhead_full_predicate(result: Any) -> bool:
    """BulkheadFullError일 때만 Fallback 활성화.

    기존 @bulkhead 데코레이터 계열의 예외 필터링과 동일하게
    BulkheadFullError에만 fallback을 적용한다.
    비즈니스 예외는 Fallback 없이 재전파한다.
    """
    from selfhealing.resilience.bulkhead.exceptions import BulkheadFullError

    return isinstance(result.error, BulkheadFullError)


def bulkhead(
    name: str | ConnectionType,
    timeout: float | None = None,
    fallback: Callable[..., T] | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    격벽 데코레이터 (동기/비동기 자동 분기).

    asyncio.iscoroutinefunction()으로 함수 타입을 감지하여
    적절한 격벽(동기/비동기)을 자동으로 적용합니다.

    Args:
        name: 도메인 이름 또는 ConnectionType
        timeout: 리소스 획득 대기 타임아웃 (초). None이면 즉시 실패.
        fallback: 격벽이 가득 찬 경우 호출할 대체 함수

    Returns:
        격벽이 적용된 데코레이터

    Examples:
        # 동기 함수
        @bulkhead(ConnectionType.DATABASE)
        def db_query():
            return execute_query()

        # 비동기 함수
        @bulkhead(ConnectionType.DATABASE)
        async def async_db_query():
            return await execute_async_query()

        # 타임아웃 설정
        @bulkhead("external_api", timeout=5.0)
        def call_external_api():
            return requests.get(url)

        # fallback 사용
        @bulkhead("api", fallback=lambda: {"status": "unavailable"})
        def get_data():
            return fetch_data()
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        if fallback is not None:
            warnings.warn(
                "@bulkhead(fallback=...) is deprecated. "
                "Use BulkheadPolicy + FallbackPolicy composition instead. "
                "Example: compose(FallbackPolicy(fallback_fn=...), BulkheadPolicy(...)).execute(func)",
                DeprecationWarning,
                stacklevel=3,
            )

        if asyncio.iscoroutinefunction(fn):

            @wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> T:
                from selfhealing.resilience.bulkhead.policy import (
                    AsyncBulkheadPolicy,
                )
                from selfhealing.resilience.bulkhead.registry import (
                    get_bulkhead_registry,
                )

                registry = get_bulkhead_registry()
                key = name.value if isinstance(name, ConnectionType) else name
                async_bh = registry.get_async(key)
                bp = AsyncBulkheadPolicy(async_bulkhead=async_bh, timeout=timeout)

                if fallback is not None:
                    from selfhealing.resilience.policies.composer import (
                        compose_async,
                    )
                    from selfhealing.resilience.policies.fallback import (
                        AsyncFallbackPolicy,
                    )

                    if asyncio.iscoroutinefunction(fallback):

                        async def fb_fn() -> T:
                            return await fallback(*args, **kwargs)

                    else:

                        async def fb_fn() -> T:
                            return fallback(*args, **kwargs)

                    fb_policy = AsyncFallbackPolicy(
                        fallback_fn=fb_fn,
                        predicate=_bulkhead_full_predicate,
                    )
                    result = await compose_async(fb_policy, bp).execute(
                        fn,
                        *args,
                        **kwargs,
                    )
                else:
                    result = await bp.execute(fn, *args, **kwargs)

                if result.success:
                    return result.value  # type: ignore[return-value]
                if result.error:
                    raise result.error

            return async_wrapper  # type: ignore

        @wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> T:
            from selfhealing.resilience.bulkhead.policy import BulkheadPolicy
            from selfhealing.resilience.bulkhead.registry import (
                get_bulkhead_registry,
            )

            registry = get_bulkhead_registry()
            key = name.value if isinstance(name, ConnectionType) else name
            bh = registry.get(key)
            bp = BulkheadPolicy(bulkhead=bh, timeout=timeout)

            if fallback is not None:
                from selfhealing.resilience.policies.composer import compose
                from selfhealing.resilience.policies.fallback import (
                    FallbackPolicy,
                )

                fb_policy = FallbackPolicy(
                    fallback_fn=lambda: fallback(*args, **kwargs),
                    predicate=_bulkhead_full_predicate,
                )
                result = compose(fb_policy, bp).execute(fn, *args, **kwargs)
            else:
                result = bp.execute(fn, *args, **kwargs)

            if result.success:
                return result.value  # type: ignore[return-value]
            if result.error:
                raise result.error

        return sync_wrapper

    return decorator


def bulkhead_for_database(
    alias: str = "default",
    timeout: float | None = None,
    fallback: Callable[..., T] | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    DB alias별 격벽 데코레이터.

    내부적으로 BulkheadPolicy/AsyncBulkheadPolicy를 사용하며,
    Registry의 get_for_database()로 해당 alias의 격벽을 조회한다.

    Args:
        alias: Django DB alias (default, replica, analytics 등)
        timeout: 리소스 획득 대기 타임아웃 (초)
        fallback: (deprecated) 격벽이 가득 찬 경우 호출할 대체 함수.
                  BulkheadPolicy + FallbackPolicy 조합을 직접 사용할 것을 권장.

    Examples:
        @bulkhead_for_database("default")
        def write_to_db():
            Model.objects.using("default").create(...)

        @bulkhead_for_database("replica")
        def read_from_replica():
            return Model.objects.using("replica").all()
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        if fallback is not None:
            warnings.warn(
                "@bulkhead_for_database(fallback=...) is deprecated. "
                "Use BulkheadPolicy + FallbackPolicy composition instead.",
                DeprecationWarning,
                stacklevel=3,
            )

        if asyncio.iscoroutinefunction(fn):

            @wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> T:
                from selfhealing.resilience.bulkhead.policy import (
                    AsyncBulkheadPolicy,
                )
                from selfhealing.resilience.bulkhead.registry import (
                    get_bulkhead_registry,
                )

                registry = get_bulkhead_registry()
                bh = registry.get_for_database(alias)
                async_bh = registry.get_async(bh.name)
                bp = AsyncBulkheadPolicy(async_bulkhead=async_bh, timeout=timeout)

                if fallback is not None:
                    from selfhealing.resilience.policies.composer import (
                        compose_async,
                    )
                    from selfhealing.resilience.policies.fallback import (
                        AsyncFallbackPolicy,
                    )

                    if asyncio.iscoroutinefunction(fallback):

                        async def fb_fn() -> T:
                            return await fallback(*args, **kwargs)

                    else:

                        async def fb_fn() -> T:
                            return fallback(*args, **kwargs)

                    fb_policy = AsyncFallbackPolicy(
                        fallback_fn=fb_fn,
                        predicate=_bulkhead_full_predicate,
                    )
                    result = await compose_async(fb_policy, bp).execute(
                        fn,
                        *args,
                        **kwargs,
                    )
                else:
                    result = await bp.execute(fn, *args, **kwargs)

                if result.success:
                    return result.value  # type: ignore[return-value]
                if result.error:
                    raise result.error

            return async_wrapper  # type: ignore

        @wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> T:
            from selfhealing.resilience.bulkhead.policy import BulkheadPolicy
            from selfhealing.resilience.bulkhead.registry import (
                get_bulkhead_registry,
            )

            registry = get_bulkhead_registry()
            bh = registry.get_for_database(alias)
            bp = BulkheadPolicy(bulkhead=bh, timeout=timeout)

            if fallback is not None:
                from selfhealing.resilience.policies.composer import compose
                from selfhealing.resilience.policies.fallback import (
                    FallbackPolicy,
                )

                fb_policy = FallbackPolicy(
                    fallback_fn=lambda: fallback(*args, **kwargs),
                    predicate=_bulkhead_full_predicate,
                )
                result = compose(fb_policy, bp).execute(fn, *args, **kwargs)
            else:
                result = bp.execute(fn, *args, **kwargs)

            if result.success:
                return result.value  # type: ignore[return-value]
            if result.error:
                raise result.error

        return sync_wrapper

    return decorator


def bulkhead_for_cache(
    name: str = "default",
    timeout: float | None = None,
    fallback: Callable[..., T] | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    캐시 인스턴스별 격벽 데코레이터.

    내부적으로 BulkheadPolicy/AsyncBulkheadPolicy를 사용하며,
    Registry의 get_for_cache()로 해당 캐시의 격벽을 조회한다.

    Args:
        name: 캐시 이름 (default, session 등)
        timeout: 리소스 획득 대기 타임아웃 (초)
        fallback: (deprecated) 격벽이 가득 찬 경우 호출할 대체 함수.
                  BulkheadPolicy + FallbackPolicy 조합을 직접 사용할 것을 권장.

    Examples:
        @bulkhead_for_cache("default")
        def get_cached_value(key: str):
            return cache.get(key)

        @bulkhead_for_cache("session")
        def get_session_data(session_id: str):
            return session_cache.get(session_id)
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        if fallback is not None:
            warnings.warn(
                "@bulkhead_for_cache(fallback=...) is deprecated. " "Use BulkheadPolicy + FallbackPolicy composition instead.",
                DeprecationWarning,
                stacklevel=3,
            )

        if asyncio.iscoroutinefunction(fn):

            @wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> T:
                from selfhealing.resilience.bulkhead.policy import (
                    AsyncBulkheadPolicy,
                )
                from selfhealing.resilience.bulkhead.registry import (
                    get_bulkhead_registry,
                )

                registry = get_bulkhead_registry()
                bh = registry.get_for_cache(name)
                async_bh = registry.get_async(bh.name)
                bp = AsyncBulkheadPolicy(async_bulkhead=async_bh, timeout=timeout)

                if fallback is not None:
                    from selfhealing.resilience.policies.composer import (
                        compose_async,
                    )
                    from selfhealing.resilience.policies.fallback import (
                        AsyncFallbackPolicy,
                    )

                    if asyncio.iscoroutinefunction(fallback):

                        async def fb_fn() -> T:
                            return await fallback(*args, **kwargs)

                    else:

                        async def fb_fn() -> T:
                            return fallback(*args, **kwargs)

                    fb_policy = AsyncFallbackPolicy(
                        fallback_fn=fb_fn,
                        predicate=_bulkhead_full_predicate,
                    )
                    result = await compose_async(fb_policy, bp).execute(
                        fn,
                        *args,
                        **kwargs,
                    )
                else:
                    result = await bp.execute(fn, *args, **kwargs)

                if result.success:
                    return result.value  # type: ignore[return-value]
                if result.error:
                    raise result.error

            return async_wrapper  # type: ignore

        @wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> T:
            from selfhealing.resilience.bulkhead.policy import BulkheadPolicy
            from selfhealing.resilience.bulkhead.registry import (
                get_bulkhead_registry,
            )

            registry = get_bulkhead_registry()
            bh = registry.get_for_cache(name)
            bp = BulkheadPolicy(bulkhead=bh, timeout=timeout)

            if fallback is not None:
                from selfhealing.resilience.policies.composer import compose
                from selfhealing.resilience.policies.fallback import (
                    FallbackPolicy,
                )

                fb_policy = FallbackPolicy(
                    fallback_fn=lambda: fallback(*args, **kwargs),
                    predicate=_bulkhead_full_predicate,
                )
                result = compose(fb_policy, bp).execute(fn, *args, **kwargs)
            else:
                result = bp.execute(fn, *args, **kwargs)

            if result.success:
                return result.value  # type: ignore[return-value]
            if result.error:
                raise result.error

        return sync_wrapper

    return decorator
