"""
automation_gate 데코레이터 테스트.

데코레이터의 허용/차단 동작 및 functools.wraps 보존 테스트.
"""

from unittest.mock import patch

import pytest


class TestAutomationGateDecorator:
    """automation_gate 데코레이터 테스트."""

    def test_decorator_allows_when_budget_healthy(self):
        """예산 충분 시 함수 실행."""
        from selfhealing.services.error_budget_gate import (
            automation_gate,
            get_error_budget_gate,
        )

        @automation_gate(action="test_func")
        def test_func():
            return "executed"

        gate = get_error_budget_gate()

        with patch.object(gate, '_get_error_budget_percent', return_value=75.0):
            gate.clear_cache()
            result = test_func()

        assert result == "executed"

    def test_decorator_blocks_when_budget_critical(self):
        """예산 위험 시 함수 차단."""
        from selfhealing.services.error_budget_gate import (
            AutomationBlockedError,
            automation_gate,
            get_error_budget_gate,
        )

        @automation_gate(action="test_func")
        def test_func():
            return "executed"

        gate = get_error_budget_gate()

        with patch.object(gate, '_get_error_budget_percent', return_value=5.0):
            gate.clear_cache()
            with pytest.raises(AutomationBlockedError):
                test_func()


class TestAutomationGateFunctoolsWraps:
    """
    automation_gate 데코레이터의 functools.wraps 보존 테스트.
    
    리뷰 ②: Universal Decorator에서 functools.wraps가 정확히 적용되어
    inspect.iscoroutinefunction() 및 원래 함수의 메타데이터가 보존되는지 확인.
    """

    def test_sync_decorator_preserves_function_name(self):
        """Sync decorator should preserve original function name."""
        from selfhealing.services.error_budget_gate import automation_gate

        @automation_gate(action="test_action")
        def my_sync_gate_function():
            """My sync docstring for gate."""
            return True

        assert my_sync_gate_function.__name__ == "my_sync_gate_function"
        assert my_sync_gate_function.__doc__ == "My sync docstring for gate."

    def test_async_decorator_preserves_function_name(self):
        """Async decorator should preserve original function name."""
        from selfhealing.services.error_budget_gate import automation_gate

        @automation_gate(action="test_action")
        async def my_async_gate_function():
            """My async docstring for gate."""
            return True

        assert my_async_gate_function.__name__ == "my_async_gate_function"
        assert my_async_gate_function.__doc__ == "My async docstring for gate."

    def test_async_wrapper_is_still_coroutine_function(self):
        """Async wrapped function should still be recognized as coroutine function."""
        import asyncio
        import inspect

        from selfhealing.services.error_budget_gate import automation_gate

        @automation_gate(action="test_action")
        async def my_async_gate():
            return True

        # This is the key check from 리뷰 ②
        assert asyncio.iscoroutinefunction(my_async_gate)
        assert inspect.iscoroutinefunction(my_async_gate)

    def test_sync_wrapper_is_not_coroutine_function(self):
        """Sync wrapped function should NOT be recognized as coroutine function."""
        import asyncio
        import inspect

        from selfhealing.services.error_budget_gate import automation_gate

        @automation_gate(action="test_action")
        def my_sync_gate():
            return True

        assert not asyncio.iscoroutinefunction(my_sync_gate)
        assert not inspect.iscoroutinefunction(my_sync_gate)

    def test_decorator_uses_function_name_as_action(self):
        """데코레이터가 action 미지정 시 함수 이름을 사용하는지 확인."""
        from selfhealing.services.error_budget_gate import (
            automation_gate,
        )

        @automation_gate()  # action 미지정
        def my_auto_named_function():
            return "executed"

        # 함수 이름이 보존되어야 함
        assert my_auto_named_function.__name__ == "my_auto_named_function"
