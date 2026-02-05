"""
@bulkhead 데코레이터 단위 테스트.

동기/비동기 자동 분기 데코레이터의 동작을 검증합니다:
- 동기 함수 자동 감지 및 래핑
- 비동기 함수 자동 감지 및 래핑
- fallback 함수 지원
"""

from __future__ import annotations

import asyncio

import pytest

from selfhealing.core.connection_health import ConnectionType
from selfhealing.resilience.bulkhead.decorator import (
    bulkhead,
    bulkhead_for_cache,
    bulkhead_for_database,
)
from selfhealing.resilience.bulkhead.exceptions import BulkheadFullException
from selfhealing.resilience.bulkhead.registry import (
    get_bulkhead_registry,
    reset_bulkhead_registry,
)
from selfhealing.settings.bulkhead import reset_bulkhead_settings


@pytest.fixture(autouse=True)
def reset_singletons():
    """각 테스트 전후로 싱글톤 초기화."""
    reset_bulkhead_registry()
    reset_bulkhead_settings()
    yield
    reset_bulkhead_registry()
    reset_bulkhead_settings()


class TestBulkheadDecoratorSync:
    """동기 함수 데코레이터 테스트."""

    def test_sync_function_wrapped(self):
        """동기 함수가 격벽으로 래핑됨."""
        call_count = 0

        @bulkhead(ConnectionType.DATABASE)
        def sync_work():
            nonlocal call_count
            call_count += 1
            return "result"

        result = sync_work()
        assert result == "result"
        assert call_count == 1

    def test_sync_function_with_args(self):
        """인자가 있는 동기 함수."""

        @bulkhead("database")
        def add(a: int, b: int) -> int:
            return a + b

        result = add(3, 5)
        assert result == 8

    def test_sync_function_with_kwargs(self):
        """키워드 인자가 있는 동기 함수."""

        @bulkhead(ConnectionType.CACHE)
        def greet(name: str, greeting: str = "Hello") -> str:
            return f"{greeting}, {name}!"

        result = greet("World", greeting="Hi")
        assert result == "Hi, World!"

    def test_sync_function_raises_when_full(self):
        """격벽이 가득 차면 예외 발생."""
        registry = get_bulkhead_registry()
        db_bulkhead = registry.get(ConnectionType.DATABASE)

        # 격벽의 모든 슬롯을 점유
        max_concurrent = db_bulkhead.get_state().max_concurrent
        for _ in range(max_concurrent):
            db_bulkhead.try_acquire()

        @bulkhead(ConnectionType.DATABASE)
        def will_fail():
            return "never"

        with pytest.raises(BulkheadFullException):
            will_fail()

        # 정리
        for _ in range(max_concurrent):
            db_bulkhead.release()


class TestBulkheadDecoratorAsync:
    """비동기 함수 데코레이터 테스트."""

    @pytest.mark.asyncio
    async def test_async_function_wrapped(self):
        """비동기 함수가 격벽으로 래핑됨."""
        call_count = 0

        @bulkhead(ConnectionType.DATABASE)
        async def async_work():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.01)
            return "async_result"

        result = await async_work()
        assert result == "async_result"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_async_function_with_args(self):
        """인자가 있는 비동기 함수."""

        @bulkhead("database")
        async def async_add(a: int, b: int) -> int:
            await asyncio.sleep(0.01)
            return a + b

        result = await async_add(10, 20)
        assert result == 30


class TestBulkheadDecoratorFallback:
    """fallback 함수 테스트."""

    def test_sync_fallback_called_when_full(self):
        """격벽 가득 찬 경우 동기 fallback 호출."""
        registry = get_bulkhead_registry()
        db_bulkhead = registry.get(ConnectionType.DATABASE)

        # 격벽의 모든 슬롯 점유
        max_concurrent = db_bulkhead.get_state().max_concurrent
        for _ in range(max_concurrent):
            db_bulkhead.try_acquire()

        def fallback_fn():
            return "fallback_result"

        @bulkhead(ConnectionType.DATABASE, fallback=fallback_fn)
        def primary_work():
            return "primary_result"

        result = primary_work()
        assert result == "fallback_result"

        # 정리
        for _ in range(max_concurrent):
            db_bulkhead.release()

    def test_sync_fallback_with_args(self):
        """인자를 받는 동기 fallback."""
        registry = get_bulkhead_registry()
        db_bulkhead = registry.get(ConnectionType.DATABASE)

        max_concurrent = db_bulkhead.get_state().max_concurrent
        for _ in range(max_concurrent):
            db_bulkhead.try_acquire()

        def fallback_fn(x: int) -> int:
            return x * 2  # 다른 로직

        @bulkhead(ConnectionType.DATABASE, fallback=fallback_fn)
        def compute(x: int) -> int:
            return x * 10  # 원래 로직

        result = compute(5)
        assert result == 10  # fallback: 5 * 2

        # 정리
        for _ in range(max_concurrent):
            db_bulkhead.release()

    @pytest.mark.asyncio
    async def test_async_fallback_called_when_full(self):
        """격벽 가득 찬 경우 비동기 fallback 호출."""
        registry = get_bulkhead_registry()

        # 비동기 격벽 점유
        async_bh = registry.get_async(ConnectionType.DATABASE)
        max_concurrent = async_bh.get_state().max_concurrent
        for _ in range(max_concurrent):
            await async_bh.try_acquire()

        async def fallback_fn():
            return "async_fallback"

        @bulkhead(ConnectionType.DATABASE, fallback=fallback_fn)
        async def async_primary():
            return "async_primary"

        result = await async_primary()
        assert result == "async_fallback"

        # 정리
        for _ in range(max_concurrent):
            await async_bh.release()


class TestBulkheadForDatabaseDecorator:
    """bulkhead_for_database 데코레이터 테스트."""

    def test_bulkhead_for_database_default(self):
        """기본 DB alias 데코레이터."""

        @bulkhead_for_database("default")
        def db_write():
            return "written"

        result = db_write()
        assert result == "written"

    def test_bulkhead_for_database_replica(self):
        """replica DB alias 데코레이터."""

        @bulkhead_for_database("replica")
        def db_read():
            return "read"

        result = db_read()
        assert result == "read"

    @pytest.mark.asyncio
    async def test_bulkhead_for_database_async(self):
        """비동기 DB 데코레이터."""

        @bulkhead_for_database("default")
        async def async_db_write():
            await asyncio.sleep(0.01)
            return "async_written"

        result = await async_db_write()
        assert result == "async_written"


class TestBulkheadForCacheDecorator:
    """bulkhead_for_cache 데코레이터 테스트."""

    def test_bulkhead_for_cache_default(self):
        """기본 캐시 인스턴스 데코레이터."""

        @bulkhead_for_cache("default")
        def cache_get(key: str):
            return f"value_for_{key}"

        result = cache_get("my_key")
        assert result == "value_for_my_key"

    def test_bulkhead_for_cache_session(self):
        """세션 캐시 인스턴스 데코레이터."""

        @bulkhead_for_cache("session")
        def session_get(session_id: str):
            return {"session_id": session_id}

        result = session_get("abc123")
        assert result == {"session_id": "abc123"}

    @pytest.mark.asyncio
    async def test_bulkhead_for_cache_async(self):
        """비동기 캐시 데코레이터."""

        @bulkhead_for_cache("default")
        async def async_cache_get(key: str):
            await asyncio.sleep(0.01)
            return f"async_value_{key}"

        result = await async_cache_get("test")
        assert result == "async_value_test"


class TestBulkheadDecoratorTimeout:
    """타임아웃 옵션 테스트."""

    def test_timeout_applied(self):
        """타임아웃 옵션이 적용됨."""

        # timeout이 적용되어도 정상 동작하는지 확인
        @bulkhead(ConnectionType.DATABASE, timeout=5.0)
        def quick_work():
            return "done"

        result = quick_work()
        assert result == "done"

    @pytest.mark.asyncio
    async def test_async_timeout_applied(self):
        """비동기 타임아웃 옵션."""

        @bulkhead(ConnectionType.DATABASE, timeout=5.0)
        async def async_quick_work():
            await asyncio.sleep(0.01)
            return "async_done"

        result = await async_quick_work()
        assert result == "async_done"


class TestBulkheadDecoratorPreservesFunctionMetadata:
    """함수 메타데이터 보존 테스트."""

    def test_preserves_function_name(self):
        """함수 이름 보존."""

        @bulkhead(ConnectionType.DATABASE)
        def my_named_function():
            """This is my docstring."""
            return "result"

        assert my_named_function.__name__ == "my_named_function"
        assert "docstring" in my_named_function.__doc__

    @pytest.mark.asyncio
    async def test_preserves_async_function_name(self):
        """비동기 함수 이름 보존."""

        @bulkhead(ConnectionType.DATABASE)
        async def my_async_function():
            """Async docstring."""
            return "result"

        assert my_async_function.__name__ == "my_async_function"
        assert "Async" in my_async_function.__doc__
