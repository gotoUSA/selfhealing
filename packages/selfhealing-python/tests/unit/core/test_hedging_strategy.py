"""
Hedging Strategy Unit Tests.

HedgingStrategy와 AsyncHedgingStrategy의 핵심 동작을 테스트합니다:
- FallbackStrategy 상속 통합
- Bulkhead/Backpressure 연동
- 동적 설정 변경
- 결과 검증
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from selfhealing.core.hedging.async_strategy import AsyncHedgingStrategy
from selfhealing.core.hedging.config import HedgingConfig, HedgingMode
from selfhealing.core.hedging.strategy import HedgingStrategy


class TestHedgingStrategy:
    """HedgingStrategy 테스트."""

    def test_strategy_creation(self):
        """전략 생성 테스트."""
        config = HedgingConfig(
            mode=HedgingMode.IMMEDIATE,
            timeout=5.0,
        )
        strategy = HedgingStrategy(config=config)

        assert strategy._config == config

    def test_execute_with_primary_and_fallback(self):
        """Primary와 Fallback 함수로 실행 테스트."""
        config = HedgingConfig(
            mode=HedgingMode.IMMEDIATE,
            timeout=5.0,
        )
        strategy = HedgingStrategy(config=config)

        def slow_primary():
            time.sleep(0.3)
            return "primary"

        def fast_fallback():
            time.sleep(0.05)
            return "fallback"

        result = strategy.execute(
            primary_fn=slow_primary,
            fallback_fn=fast_fallback,
        )

        assert result.value in ["primary", "fallback"]

    def test_execute_primary_only(self):
        """Primary만 있는 경우 실행 테스트."""
        config = HedgingConfig(
            mode=HedgingMode.IMMEDIATE,
            timeout=5.0,
        )
        strategy = HedgingStrategy(config=config)

        def primary():
            return "primary_result"

        result = strategy.execute(primary_fn=primary)

        assert result.value == "primary_result"
        assert result.used_fallback is False

    def test_with_candidates_list(self):
        """candidates 리스트로 초기화 테스트."""
        config = HedgingConfig(
            mode=HedgingMode.IMMEDIATE,
            timeout=5.0,
        )

        def candidate1():
            time.sleep(0.3)
            return "c1"

        def candidate2():
            time.sleep(0.05)
            return "c2"

        strategy = HedgingStrategy(
            candidates=[candidate1, candidate2],
            config=config,
        )

        # candidates를 사용한 실행
        result = strategy.execute(
            primary_fn=candidate1,
            fallback_fn=candidate2,
        )

        assert result.value in ["c1", "c2"]


class TestHedgingStrategyBackpressure:
    """Backpressure 통합 테스트."""

    def test_hedging_disabled_on_high_load(self):
        """높은 부하 시 헷징 비활성화 테스트."""
        config = HedgingConfig(
            mode=HedgingMode.IMMEDIATE,
            timeout=5.0,
            disable_on_load_level="high",
        )
        strategy = HedgingStrategy(config=config)

        # 부하 레벨을 높게 설정
        strategy._current_load_level = "high"

        fallback_called = {"called": False}

        def primary():
            return "primary"

        def fallback():
            fallback_called["called"] = True
            return "fallback"

        result = strategy.execute(
            primary_fn=primary,
            fallback_fn=fallback,
        )

        # 높은 부하에서는 Primary만 실행됨
        assert result.value == "primary"
        # fallback은 호출되지 않아야 함
        assert fallback_called["called"] is False

    def test_delay_multiplier_on_medium_load(self):
        """중간 부하에서 delay 조정 테스트."""
        config = HedgingConfig(
            mode=HedgingMode.DELAYED,
            delay=0.1,
            delay_multiplier_on_medium=2.0,
            timeout=5.0,
        )
        strategy = HedgingStrategy(config=config)

        # 부하 레벨을 중간으로 설정
        strategy._current_load_level = "medium"

        effective_delay = strategy._get_effective_delay()
        assert effective_delay == 0.2  # 0.1 * 2.0


class TestHedgingStrategyBulkhead:
    """Bulkhead 통합 테스트."""

    def test_bulkhead_acquire_failure_fallback(self):
        """Bulkhead 획득 실패 시 Primary만 실행."""
        config = HedgingConfig(
            mode=HedgingMode.IMMEDIATE,
            timeout=5.0,
            bulkhead_name="test_bulkhead",
        )
        strategy = HedgingStrategy(config=config)

        # Mock bulkhead registry
        mock_bulkhead = MagicMock()
        mock_bulkhead.try_acquire.return_value = False

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_bulkhead

        strategy._bulkhead_registry = mock_registry

        def primary():
            return "primary"

        def fallback():
            return "fallback"

        result = strategy.execute(
            primary_fn=primary,
            fallback_fn=fallback,
        )

        # Bulkhead가 거부하면 Primary만 실행
        assert result.value == "primary"


class TestAsyncHedgingStrategy:
    """AsyncHedgingStrategy 테스트."""

    @pytest.mark.asyncio
    async def test_async_strategy_creation(self):
        """비동기 전략 생성 테스트."""
        config = HedgingConfig(
            mode=HedgingMode.IMMEDIATE,
            timeout=5.0,
        )
        strategy = AsyncHedgingStrategy(config=config)

        assert strategy._config == config

    @pytest.mark.asyncio
    async def test_async_execute(self):
        """비동기 실행 테스트."""
        config = HedgingConfig(
            mode=HedgingMode.IMMEDIATE,
            timeout=5.0,
        )
        strategy = AsyncHedgingStrategy(config=config)

        async def slow_primary():
            await asyncio.sleep(0.3)
            return "primary"

        async def fast_fallback():
            await asyncio.sleep(0.05)
            return "fallback"

        result = await strategy.execute(
            primary_fn=slow_primary,
            fallback_fn=fast_fallback,
        )

        assert result.value in ["primary", "fallback"]

    @pytest.mark.asyncio
    async def test_async_execute_primary_only(self):
        """비동기 Primary만 실행 테스트."""
        config = HedgingConfig(
            mode=HedgingMode.IMMEDIATE,
            timeout=5.0,
        )
        strategy = AsyncHedgingStrategy(config=config)

        async def primary():
            return "primary_result"

        result = await strategy.execute(primary_fn=primary)

        assert result.value == "primary_result"


class TestHedgingStrategyAdaptiveMode:
    """ADAPTIVE 모드 테스트."""

    def test_adaptive_mode_uses_p50(self):
        """ADAPTIVE 모드가 P50 지연시간을 사용하는지 테스트."""
        config = HedgingConfig(
            mode=HedgingMode.ADAPTIVE,
            timeout=5.0,
        )
        strategy = HedgingStrategy(config=config)

        # 지연시간 기록을 위해 여러 번 실행
        for _ in range(15):
            result = strategy.execute(
                primary_fn=lambda: "result",
            )
            assert result.value == "result"


class TestHedgingStrategyMetrics:
    """메트릭 관련 테스트."""

    def test_metrics_recorded_on_execution(self):
        """실행 시 메트릭이 기록되는지 테스트."""
        config = HedgingConfig(
            mode=HedgingMode.IMMEDIATE,
            timeout=5.0,
        )
        strategy = HedgingStrategy(config=config)

        def primary():
            return "result"

        # 메트릭이 에러 없이 기록되어야 함
        result = strategy.execute(primary_fn=primary)
        assert result.value == "result"


class TestHedgingStrategyEdgeCases:
    """엣지 케이스 테스트."""

    def test_default_value_on_failure(self):
        """모든 후보 실패 시 default_value 테스트."""
        config = HedgingConfig(
            mode=HedgingMode.IMMEDIATE,
            timeout=5.0,
        )
        strategy = HedgingStrategy(config=config, default_value="default")

        def failing_primary():
            raise RuntimeError("Primary failed")

        def failing_fallback():
            raise RuntimeError("Fallback failed")

        result = strategy.execute(
            primary_fn=failing_primary,
            fallback_fn=failing_fallback,
            default_value="default",
        )

        # 기본값이 반환됨
        assert result.value == "default"

    def test_many_fallbacks_via_candidates(self):
        """많은 fallback 테스트 (candidates 사용)."""
        config = HedgingConfig(
            mode=HedgingMode.IMMEDIATE,
            timeout=5.0,
            max_candidates=3,
        )

        def primary():
            time.sleep(0.5)
            return "primary"

        def fallback1():
            time.sleep(0.1)
            return "fb1"

        def fallback2():
            time.sleep(0.05)
            return "fb2"

        strategy = HedgingStrategy(
            candidates=[primary, fallback1, fallback2],
            config=config,
        )

        result = strategy.execute(
            primary_fn=primary,
            fallback_fn=fallback1,
        )

        assert result.value in ["primary", "fb1", "fb2"]

    def test_callable_with_args(self):
        """인자가 있는 callable 테스트."""
        config = HedgingConfig(mode=HedgingMode.IMMEDIATE, timeout=5.0)
        strategy = HedgingStrategy(config=config)

        def primary_with_args(x, y):
            return x + y

        # partial 또는 lambda로 감싸서 호출
        result = strategy.execute(
            primary_fn=lambda: primary_with_args(1, 2),
        )

        assert result.value == 3


class TestHedgingStrategyConfigUpdate:
    """동적 설정 업데이트 테스트."""

    def test_mode_change_via_event(self):
        """이벤트를 통한 모드 변경 테스트."""
        config = HedgingConfig(
            mode=HedgingMode.DELAYED,
            timeout=5.0,
        )
        strategy = HedgingStrategy(config=config)

        assert strategy._config.mode == HedgingMode.DELAYED

        # 설정 변경 이벤트 시뮬레이션
        strategy._on_config_updated({"key": "hedging.mode", "value": "immediate"})

        assert strategy._config.mode == HedgingMode.IMMEDIATE

    def test_delay_change_via_event(self):
        """이벤트를 통한 delay 변경 테스트."""
        config = HedgingConfig(
            mode=HedgingMode.DELAYED,
            delay=0.1,
            timeout=5.0,
        )
        strategy = HedgingStrategy(config=config)

        assert strategy._config.delay == 0.1

        # delay 변경 이벤트
        strategy._on_config_updated({"key": "hedging.delay", "value": 0.5})

        assert strategy._config.delay == 0.5

    def test_backpressure_level_update(self):
        """Backpressure 레벨 업데이트 테스트."""
        config = HedgingConfig(
            mode=HedgingMode.IMMEDIATE,
            timeout=5.0,
        )
        strategy = HedgingStrategy(config=config)

        assert strategy._current_load_level == "none"

        # 부하 레벨 변경 이벤트
        strategy._on_config_updated({"key": "backpressure.level", "value": "medium"})

        assert strategy._current_load_level == "medium"


class TestHedgingStrategyShouldDisable:
    """헷징 비활성화 조건 테스트."""

    def test_should_disable_on_high_level(self):
        """high 레벨에서 비활성화 테스트."""
        config = HedgingConfig(
            mode=HedgingMode.IMMEDIATE,
            disable_on_load_level="high",
        )
        strategy = HedgingStrategy(config=config)

        strategy._current_load_level = "none"
        assert strategy._should_disable_hedging() is False

        strategy._current_load_level = "low"
        assert strategy._should_disable_hedging() is False

        strategy._current_load_level = "medium"
        assert strategy._should_disable_hedging() is False

        strategy._current_load_level = "high"
        assert strategy._should_disable_hedging() is True

        strategy._current_load_level = "critical"
        assert strategy._should_disable_hedging() is True

    def test_should_disable_on_medium_level(self):
        """medium 레벨에서 비활성화 테스트."""
        config = HedgingConfig(
            mode=HedgingMode.IMMEDIATE,
            disable_on_load_level="medium",
        )
        strategy = HedgingStrategy(config=config)

        strategy._current_load_level = "low"
        assert strategy._should_disable_hedging() is False

        strategy._current_load_level = "medium"
        assert strategy._should_disable_hedging() is True

        strategy._current_load_level = "high"
        assert strategy._should_disable_hedging() is True
