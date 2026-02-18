"""
TrafficGate bulkhead_timeout 파라미터 전달 단위 테스트.

테스트 항목:
- 동작: should_allow()의 bulkhead_timeout이 Bulkhead.try_acquire()에 전달
- 동작: bulkhead_timeout 미지정 시 None이 전달
"""

from unittest.mock import MagicMock, patch

import pytest

from selfhealing.scaling.config import (
    BackpressureSettings,
    reset_backpressure_settings,
)
from selfhealing.scaling.rate_controller import (
    RateController,
    reset_rate_controller,
)
from selfhealing.scaling.traffic_gate import TrafficGate, reset_traffic_gate


class TestBulkheadTimeoutPassthroughBehavior:
    """bulkhead_timeout 파라미터 전달 동작 검증."""

    @pytest.fixture(autouse=True)
    def reset_singletons(self):
        reset_rate_controller()
        reset_backpressure_settings()
        reset_traffic_gate()
        yield
        reset_rate_controller()
        reset_backpressure_settings()
        reset_traffic_gate()

    def test_timeout_forwarded_to_bulkhead(self):
        """bulkhead_timeout이 Bulkhead.try_acquire(timeout=)에 전달된다."""
        mock_bulkhead = MagicMock()
        mock_bulkhead.try_acquire.return_value = True

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_bulkhead

        settings = BackpressureSettings(
            backpressure_enabled=True,
            max_rate_per_second=10000.0,
        )
        controller = RateController(settings=settings)
        gate = TrafficGate(settings=settings, rate_controller=controller)

        with patch(
            "selfhealing.resilience.bulkhead.get_bulkhead_registry",
            return_value=mock_registry,
        ):
            gate.should_allow(
                priority=0,
                bulkhead_name="tier:critical",
                bulkhead_timeout=0.05,
            )

        mock_bulkhead.try_acquire.assert_called_once_with(timeout=0.05)

    def test_none_timeout_forwarded_as_none(self):
        """bulkhead_timeout 미지정 시 None이 try_acquire()에 전달된다."""
        mock_bulkhead = MagicMock()
        mock_bulkhead.try_acquire.return_value = True

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_bulkhead

        settings = BackpressureSettings(
            backpressure_enabled=True,
            max_rate_per_second=10000.0,
        )
        controller = RateController(settings=settings)
        gate = TrafficGate(settings=settings, rate_controller=controller)

        with patch(
            "selfhealing.resilience.bulkhead.get_bulkhead_registry",
            return_value=mock_registry,
        ):
            gate.should_allow(
                priority=0,
                bulkhead_name="tier:critical",
            )

        mock_bulkhead.try_acquire.assert_called_once_with(timeout=None)
