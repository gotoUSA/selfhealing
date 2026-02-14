"""
Bulkhead Decorator - 동기/비동기 자동 분기 데코레이터.

asyncio.iscoroutinefunction()을 사용하여 동기/비동기 함수를 자동으로 구분하고
적절한 격벽 유형을 적용합니다.

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

    @bulkhead("api", fallback=lambda: {"error": "service unavailable"})
    def api_call():
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
                "Example: compose(BulkheadPolicy(...), FallbackPolicy(fallback_fn=...)).execute(func)",
                DeprecationWarning,
                stacklevel=3,
            )

        # 비동기 함수인 경우
        if asyncio.iscoroutinefunction(fn):

            @wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> T:
                from selfhealing.resilience.bulkhead.exceptions import (
                    BulkheadFullError,
                )
                from selfhealing.resilience.bulkhead.registry import (
                    get_bulkhead_registry,
                )

                registry = get_bulkhead_registry()
                key = name.value if isinstance(name, ConnectionType) else name

                # 비동기 격벽 조회
                bh = registry.get_async(key)

                try:
                    async with bh.acquire(timeout=timeout):
                        return await fn(*args, **kwargs)
                except BulkheadFullError:
                    if fallback is not None:
                        # fallback도 async일 수 있음
                        if asyncio.iscoroutinefunction(fallback):
                            return await fallback(*args, **kwargs)
                        return fallback(*args, **kwargs)
                    raise

            return async_wrapper  # type: ignore

        # 동기 함수인 경우
        @wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> T:
            from selfhealing.resilience.bulkhead.exceptions import (
                BulkheadFullError,
            )
            from selfhealing.resilience.bulkhead.registry import (
                get_bulkhead_registry,
            )

            registry = get_bulkhead_registry()
            key = name.value if isinstance(name, ConnectionType) else name
            bh = registry.get(key)

            try:
                with bh.acquire(timeout=timeout):
                    return fn(*args, **kwargs)
            except BulkheadFullError:
                if fallback is not None:
                    return fallback(*args, **kwargs)
                raise

        return sync_wrapper

    return decorator


def bulkhead_for_database(
    alias: str = "default",
    timeout: float | None = None,
    fallback: Callable[..., T] | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    DB alias별 격벽 데코레이터.

    Args:
        alias: Django DB alias (default, replica, analytics 등)
        timeout: 리소스 획득 대기 타임아웃 (초)
        fallback: 격벽이 가득 찬 경우 호출할 대체 함수

    Examples:
        @bulkhead_for_database("default")
        def write_to_db():
            Model.objects.using("default").create(...)

        @bulkhead_for_database("replica")
        def read_from_replica():
            return Model.objects.using("replica").all()
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        if asyncio.iscoroutinefunction(fn):

            @wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> T:
                from selfhealing.resilience.bulkhead.exceptions import (
                    BulkheadFullError,
                )
                from selfhealing.resilience.bulkhead.registry import (
                    get_bulkhead_registry,
                )

                registry = get_bulkhead_registry()
                bh = registry.get_for_database(alias)
                async_bh = registry.get_async(bh.name)

                try:
                    async with async_bh.acquire(timeout=timeout):
                        return await fn(*args, **kwargs)
                except BulkheadFullError:
                    if fallback is not None:
                        if asyncio.iscoroutinefunction(fallback):
                            return await fallback(*args, **kwargs)
                        return fallback(*args, **kwargs)
                    raise

            return async_wrapper  # type: ignore

        @wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> T:
            from selfhealing.resilience.bulkhead.exceptions import (
                BulkheadFullError,
            )
            from selfhealing.resilience.bulkhead.registry import (
                get_bulkhead_registry,
            )

            registry = get_bulkhead_registry()
            bh = registry.get_for_database(alias)

            try:
                with bh.acquire(timeout=timeout):
                    return fn(*args, **kwargs)
            except BulkheadFullError:
                if fallback is not None:
                    return fallback(*args, **kwargs)
                raise

        return sync_wrapper

    return decorator


def bulkhead_for_cache(
    name: str = "default",
    timeout: float | None = None,
    fallback: Callable[..., T] | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    캐시 인스턴스별 격벽 데코레이터.

    Args:
        name: 캐시 이름 (default, session 등)
        timeout: 리소스 획득 대기 타임아웃 (초)
        fallback: 격벽이 가득 찬 경우 호출할 대체 함수

    Examples:
        @bulkhead_for_cache("default")
        def get_cached_value(key: str):
            return cache.get(key)

        @bulkhead_for_cache("session")
        def get_session_data(session_id: str):
            return session_cache.get(session_id)
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        if asyncio.iscoroutinefunction(fn):

            @wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> T:
                from selfhealing.resilience.bulkhead.exceptions import (
                    BulkheadFullError,
                )
                from selfhealing.resilience.bulkhead.registry import (
                    get_bulkhead_registry,
                )

                registry = get_bulkhead_registry()
                bh = registry.get_for_cache(name)
                async_bh = registry.get_async(bh.name)

                try:
                    async with async_bh.acquire(timeout=timeout):
                        return await fn(*args, **kwargs)
                except BulkheadFullError:
                    if fallback is not None:
                        if asyncio.iscoroutinefunction(fallback):
                            return await fallback(*args, **kwargs)
                        return fallback(*args, **kwargs)
                    raise

            return async_wrapper  # type: ignore

        @wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> T:
            from selfhealing.resilience.bulkhead.exceptions import (
                BulkheadFullError,
            )
            from selfhealing.resilience.bulkhead.registry import (
                get_bulkhead_registry,
            )

            registry = get_bulkhead_registry()
            bh = registry.get_for_cache(name)

            try:
                with bh.acquire(timeout=timeout):
                    return fn(*args, **kwargs)
            except BulkheadFullError:
                if fallback is not None:
                    return fallback(*args, **kwargs)
                raise

        return sync_wrapper

    return decorator
