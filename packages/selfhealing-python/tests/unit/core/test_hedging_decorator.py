"""
Hedging Decorator Unit Tests.

@hedged, @hedged_sync, @hedged_async 데코레이터 테스트:
- 동기/비동기 함수 자동 감지
- 데코레이터 설정 적용
- 함수 메타데이터 보존
- 예외 처리
"""

from __future__ import annotations

import asyncio
import functools
import time
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.core.hedging.config import HedgingMode
from selfhealing.core.hedging.decorator import (
    hedged,
    hedged_async,
    hedged_sync,
)


class TestHedgedSyncDecorator:
    """@hedged_sync 데코레이터 테스트."""

    def test_basic_decoration(self):
        """기본 데코레이션 테스트."""

        def fallback():
            return "fallback_result"

        @hedged_sync(fallback)
        def my_function():
            return "primary_result"

        result = my_function()

        assert result == "primary_result"

    def test_fast_fallback_wins(self):
        """빠른 fallback이 이기는 경우 테스트."""

        def fast_fallback():
            time.sleep(0.05)
            return "fallback_result"

        @hedged_sync(fast_fallback, mode=HedgingMode.IMMEDIATE, timeout=5.0)
        def slow_primary():
            time.sleep(0.5)
            return "primary_result"

        result = slow_primary()
        # IMMEDIATE 모드에서 빠른 fallback이 이김
        assert result == "fallback_result"

    def test_fallback_on_failure(self):
        """Primary 실패 시 Fallback 테스트."""

        def fallback():
            return "fallback_result"

        @hedged_sync(fallback, timeout=5.0)
        def failing_function():
            raise RuntimeError("Primary failed")

        # 실패 시에도 fallback이 성공하면 결과 반환
        result = failing_function()
        assert result == "fallback_result"

    def test_with_custom_config(self):
        """커스텀 설정 테스트."""

        @hedged_sync(
            mode=HedgingMode.DELAYED,
            delay=0.1,
            timeout=10.0,
        )
        def my_function():
            return "result"

        result = my_function()
        assert result == "result"

    def test_preserves_function_metadata(self):
        """함수 메타데이터 보존 테스트."""

        @hedged_sync()
        def documented_function():
            """This is the docstring."""
            return "result"

        assert documented_function.__name__ == "documented_function"
        assert documented_function.__doc__ == "This is the docstring."

    def test_with_arguments(self):
        """인자가 있는 함수 테스트."""

        @hedged_sync()
        def add(a: int, b: int) -> int:
            return a + b

        result = add(1, 2)
        assert result == 3

    def test_with_kwargs(self):
        """키워드 인자 테스트."""

        @hedged_sync()
        def greet(name: str, greeting: str = "Hello") -> str:
            return f"{greeting}, {name}!"

        result = greet("World", greeting="Hi")
        assert result == "Hi, World!"

    def test_multiple_fallbacks(self):
        """여러 fallback 테스트."""

        def fb1():
            time.sleep(0.3)
            return "fb1"

        def fb2():
            time.sleep(0.05)
            return "fb2"

        @hedged_sync(
            fb1,
            fb2,
            mode=HedgingMode.IMMEDIATE,
        )
        def slow_primary():
            time.sleep(0.5)
            return "primary"

        result = slow_primary()
        # 가장 빠른 결과가 반환됨
        assert result in ["primary", "fb1", "fb2"]


class TestHedgedAsyncDecorator:
    """@hedged_async 데코레이터 테스트."""

    @pytest.mark.asyncio
    async def test_basic_async_decoration(self):
        """기본 비동기 데코레이션 테스트."""

        async def fallback():
            return "fallback_result"

        @hedged_async(fallback)
        async def my_async_function():
            return "primary_result"

        result = await my_async_function()
        assert result == "primary_result"

    @pytest.mark.asyncio
    async def test_async_fallback_on_failure(self):
        """비동기 Primary 실패 시 Fallback 테스트."""

        async def fallback():
            return "fallback_result"

        @hedged_async(fallback, timeout=5.0)
        async def failing_async_function():
            raise RuntimeError("Async primary failed")

        result = await failing_async_function()
        assert result == "fallback_result"

    @pytest.mark.asyncio
    async def test_async_with_arguments(self):
        """비동기 인자 있는 함수 테스트."""

        @hedged_async()
        async def async_add(a: int, b: int) -> int:
            await asyncio.sleep(0.01)
            return a + b

        result = await async_add(5, 3)
        assert result == 8

    @pytest.mark.asyncio
    async def test_async_preserves_metadata(self):
        """비동기 함수 메타데이터 보존 테스트."""

        @hedged_async()
        async def documented_async_function():
            """Async docstring."""
            return "result"

        assert documented_async_function.__name__ == "documented_async_function"
        assert documented_async_function.__doc__ == "Async docstring."

    @pytest.mark.asyncio
    async def test_async_immediate_mode_fastest_wins(self):
        """비동기 IMMEDIATE 모드: 가장 빠른 응답 선택."""

        async def fast_fallback():
            await asyncio.sleep(0.05)
            return "fast"

        @hedged_async(
            fast_fallback,
            mode=HedgingMode.IMMEDIATE,
        )
        async def slow_primary():
            await asyncio.sleep(0.5)
            return "slow"

        result = await slow_primary()
        assert result == "fast"


class TestHedgedUniversalDecorator:
    """@hedged 범용 데코레이터 테스트."""

    def test_auto_detect_sync(self):
        """동기 함수 자동 감지 테스트."""

        @hedged()
        def sync_function():
            return "sync_result"

        result = sync_function()
        assert result == "sync_result"

    @pytest.mark.asyncio
    async def test_auto_detect_async(self):
        """비동기 함수 자동 감지 테스트."""

        @hedged()
        async def async_function():
            return "async_result"

        result = await async_function()
        assert result == "async_result"

    def test_sync_with_fallbacks(self):
        """동기 함수 + fallback 테스트."""

        def fb():
            return "fb"

        @hedged(fb)
        def sync_function():
            return "primary"

        result = sync_function()
        assert result in ["primary", "fb"]

    @pytest.mark.asyncio
    async def test_async_with_fallbacks(self):
        """비동기 함수 + fallback 테스트."""

        async def async_fallback():
            return "async_fb"

        @hedged(async_fallback)
        async def async_function():
            return "async_primary"

        result = await async_function()
        assert result in ["async_primary", "async_fb"]


class TestDecoratorConfiguration:
    """데코레이터 설정 테스트."""

    def test_mode_parameter(self):
        """mode 파라미터 테스트."""

        @hedged_sync(mode=HedgingMode.IMMEDIATE)
        def my_function():
            return "result"

        result = my_function()
        assert result == "result"

    def test_timeout_parameter(self):
        """timeout 파라미터 테스트."""

        @hedged_sync(timeout=10.0)
        def my_function():
            return "result"

        result = my_function()
        assert result == "result"

    def test_delay_parameter(self):
        """delay 파라미터 테스트."""

        @hedged_sync(delay=0.5, mode=HedgingMode.DELAYED)
        def my_function():
            return "result"

        result = my_function()
        assert result == "result"

    def test_bulkhead_parameter(self):
        """bulkhead_name 파라미터 테스트."""

        @hedged_sync(bulkhead_name="test_bulkhead")
        def my_function():
            return "result"

        result = my_function()
        assert result == "result"

    def test_disable_on_load_level_parameter(self):
        """disable_on_load_level 파라미터 테스트."""

        @hedged_sync(disable_on_load_level="critical")
        def my_function():
            return "result"

        result = my_function()
        assert result == "result"


class TestDecoratorErrorHandling:
    """데코레이터 에러 처리 테스트."""

    def test_all_fail_returns_default(self):
        """모든 후보 실패 시 default 반환 테스트."""

        def failing_fallback():
            raise RuntimeError("Fallback failed")

        @hedged_sync(failing_fallback, timeout=5.0, default="default_value")
        def failing_primary():
            raise RuntimeError("Primary failed")

        result = failing_primary()
        assert result == "default_value"

    def test_no_default_all_fail_raises_exception(self):
        """모든 후보 실패 시 예외 발생 테스트 (default 없음)."""

        def failing_fallback():
            raise RuntimeError("Fallback failed")

        @hedged_sync(failing_fallback, timeout=5.0)
        def failing_primary():
            raise RuntimeError("Primary failed")

        # default가 없으면 실패 시 RuntimeError 발생
        with pytest.raises(RuntimeError, match="Hedging failed"):
            failing_primary()


class TestDecoratorWithClass:
    """클래스 메서드 데코레이션 테스트."""

    def test_method_decoration(self):
        """인스턴스 메서드 데코레이션 테스트."""

        class MyService:
            def __init__(self, value: int):
                self.value = value

            @hedged_sync()
            def get_value(self) -> int:
                return self.value

        service = MyService(42)
        result = service.get_value()
        assert result == 42


class TestDecoratorStacking:
    """데코레이터 스택 테스트."""

    def test_with_functools_wraps(self):
        """functools.wraps와 함께 사용 테스트."""

        def my_decorator(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                return func(*args, **kwargs)

            return wrapper

        @my_decorator
        @hedged_sync()
        def my_function():
            """My docstring."""
            return "result"

        result = my_function()
        assert result == "result"
        assert my_function.__name__ == "my_function"


class TestDecoratorDynamicFallbacks:
    """동적 fallback 테스트."""

    def test_fallback_generator(self):
        """fallback 생성기 테스트."""
        fallback_results = ["fb1", "fb2", "fb3"]

        def make_fallback(idx):
            def fallback():
                time.sleep(0.05 * (idx + 1))
                return fallback_results[idx]

            return fallback

        fallbacks = [make_fallback(i) for i in range(3)]

        @hedged_sync(
            *fallbacks,
            mode=HedgingMode.IMMEDIATE,
        )
        def slow_primary():
            time.sleep(0.5)
            return "primary"

        result = slow_primary()
        assert result in ["primary", "fb1", "fb2", "fb3"]


class TestDecoratorCallableClass:
    """Callable 클래스 데코레이션 테스트."""

    def test_callable_class_as_fallback(self):
        """Callable 클래스를 fallback으로 사용 테스트."""

        class CallableFallback:
            def __init__(self, value: str):
                self.value = value

            def __call__(self):
                return self.value

        fallback_instance = CallableFallback("callable_result")

        @hedged_sync(fallback_instance)
        def primary():
            time.sleep(0.1)
            return "primary"

        result = primary()
        assert result in ["primary", "callable_result"]
