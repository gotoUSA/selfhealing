"""
Tests for core backoff strategies.
core/backoff.py의 ExponentialBackoff, LinearBackoff, ConstantBackoff,
DecorrelatedJitterBackoff, LegacyBackoffCalculator, get_backoff_calculator 팩토리 함수에 대한 단위 테스트.
"""

import pytest
from unittest.mock import patch, MagicMock

from selfhealing.core.backoff import (
    ExponentialBackoff,
    LinearBackoff,
    ConstantBackoff,
    DecorrelatedJitterBackoff,
    LegacyBackoffCalculator,
    LegacyBackoffConfig,
    get_backoff_calculator,
    calculate_backoff,
    BackoffStrategy,
)


# =============================================================================
# ExponentialBackoff Tests
# =============================================================================


class TestExponentialBackoff:
    """Exponential backoff strategy 테스트."""

    def test_basic_exponential_growth(self):
        """Basic exponential growth
        jitter 없이 기본 지수 증가가 올바른지 확인.
        """
        backoff = ExponentialBackoff(base_delay=1.0, multiplier=2.0, jitter=False)
        assert backoff.calculate(1) == 1.0  # 1 * 2^0
        assert backoff.calculate(2) == 2.0  # 1 * 2^1
        assert backoff.calculate(3) == 4.0  # 1 * 2^2
        assert backoff.calculate(4) == 8.0  # 1 * 2^3

    def test_max_delay_cap(self):
        """Max delay cap
        max_delay를 초과하지 않는지 확인.
        """
        backoff = ExponentialBackoff(base_delay=1.0, multiplier=10.0, max_delay=50.0, jitter=False)
        # attempt=3: 1 * 10^2 = 100 → capped to 50
        assert backoff.calculate(3) == 50.0

    def test_jitter_within_range(self):
        """Jitter within range
        jitter가 적용되면 결과가 범위 내에 있는지 확인.
        """
        backoff = ExponentialBackoff(
            base_delay=10.0,
            multiplier=2.0,
            max_delay=300.0,
            jitter=True,
            jitter_factor=0.2,
        )
        for _ in range(100):
            delay = backoff.calculate(1)
            # base_delay=10, jitter_factor=0.2 → 10 ± 2 → [8, 12]
            assert 8.0 <= delay <= 12.0

    def test_jitter_non_negative(self):
        """Jitter non-negative
        jitter 적용 후에도 음수가 되지 않는지 확인.
        """
        backoff = ExponentialBackoff(
            base_delay=0.1,
            multiplier=1.0,
            jitter=True,
            jitter_factor=0.99,
        )
        for _ in range(100):
            delay = backoff.calculate(1)
            assert delay >= 0.0

    def test_reset_is_noop(self):
        """Reset is no-op
        상태 없는 전략이므로 reset이 예외를 발생시키지 않는지 확인.
        """
        backoff = ExponentialBackoff()
        backoff.reset()  # 예외 없이 통과

    def test_from_settings(self):
        """From settings factory
        Settings 기반 팩토리 메서드가 올바르게 동작하는지 확인.
        """
        mock_settings = MagicMock()
        mock_settings.exponential_base_delay = 2.0
        mock_settings.exponential_max_delay = 120.0
        mock_settings.exponential_multiplier = 3.0
        mock_settings.exponential_jitter_factor = 0.1
        backoff = ExponentialBackoff.from_settings(settings=mock_settings)
        assert backoff.base_delay == 2.0
        assert backoff.max_delay == 120.0
        assert backoff.multiplier == 3.0

    def test_from_settings_with_overrides(self):
        """From settings with overrides
        오버라이드 파라미터가 Settings 값보다 우선하는지 확인.
        """
        mock_settings = MagicMock()
        mock_settings.exponential_base_delay = 2.0
        mock_settings.exponential_max_delay = 120.0
        mock_settings.exponential_multiplier = 3.0
        mock_settings.exponential_jitter_factor = 0.1
        backoff = ExponentialBackoff.from_settings(settings=mock_settings, base_delay=5.0)
        assert backoff.base_delay == 5.0  # 오버라이드됨
        assert backoff.max_delay == 120.0  # Settings 값 유지


# =============================================================================
# LinearBackoff Tests
# =============================================================================


class TestLinearBackoff:
    """Linear backoff strategy 테스트."""

    def test_basic_linear_growth(self):
        """Basic linear growth
        지정된 increment만큼 선형 증가하는지 확인.
        """
        backoff = LinearBackoff(base_delay=1.0, increment=2.0, jitter=False)
        assert backoff.calculate(1) == 1.0  # 1 + 2*0
        assert backoff.calculate(2) == 3.0  # 1 + 2*1
        assert backoff.calculate(3) == 5.0  # 1 + 2*2

    def test_max_delay_cap(self):
        """Max delay cap
        max_delay를 초과하지 않는지 확인.
        """
        backoff = LinearBackoff(base_delay=1.0, increment=100.0, max_delay=50.0, jitter=False)
        assert backoff.calculate(2) == 50.0  # 1 + 100*1 = 101 → capped

    def test_with_jitter(self):
        """With jitter
        jitter가 적용되면 결과가 범위 내에 있는지 확인.
        """
        backoff = LinearBackoff(
            base_delay=10.0,
            increment=0.0,
            max_delay=100.0,
            jitter=True,
            jitter_factor=0.1,
        )
        for _ in range(100):
            delay = backoff.calculate(1)
            assert 9.0 <= delay <= 11.0

    def test_from_settings(self):
        """From settings factory
        Settings 기반 팩토리 메서드가 올바르게 동작하는지 확인.
        """
        mock_settings = MagicMock()
        mock_settings.linear_base_delay = 2.0
        mock_settings.linear_increment = 1.5
        mock_settings.linear_max_delay = 60.0
        mock_settings.linear_jitter_factor = 0.1
        backoff = LinearBackoff.from_settings(settings=mock_settings)
        assert backoff.base_delay == 2.0
        assert backoff.increment == 1.5


# =============================================================================
# ConstantBackoff Tests
# =============================================================================


class TestConstantBackoff:
    """Constant backoff strategy 테스트."""

    def test_constant_delay(self):
        """Constant delay
        모든 attempt에서 동일한 delay를 반환하는지 확인.
        """
        backoff = ConstantBackoff(delay=5.0, jitter=False)
        assert backoff.calculate(1) == 5.0
        assert backoff.calculate(2) == 5.0
        assert backoff.calculate(100) == 5.0

    def test_with_jitter(self):
        """With jitter
        jitter가 적용되면 결과가 범위 내에 있는지 확인.
        """
        backoff = ConstantBackoff(delay=10.0, jitter=True, jitter_factor=0.1)
        for _ in range(100):
            delay = backoff.calculate(1)
            assert 9.0 <= delay <= 11.0

    def test_from_settings(self):
        """From settings factory
        Settings 기반 팩토리 메서드가 올바르게 동작하는지 확인.
        """
        mock_settings = MagicMock()
        mock_settings.constant_delay = 7.0
        mock_settings.constant_jitter_factor = 0.05
        backoff = ConstantBackoff.from_settings(settings=mock_settings)
        assert backoff.delay == 7.0


# =============================================================================
# DecorrelatedJitterBackoff Tests
# =============================================================================


class TestDecorrelatedJitterBackoff:
    """Decorrelated jitter backoff (AWS-style) 테스트."""

    def test_first_attempt_returns_base(self):
        """First attempt returns base
        첫 번째 시도에서는 base_delay를 반환하는지 확인.
        """
        backoff = DecorrelatedJitterBackoff(base_delay=1.0, max_delay=300.0)
        assert backoff.calculate(1) == 1.0

    def test_subsequent_attempts_use_previous(self):
        """Subsequent attempts use previous delay
        두 번째 양쪽부터 이전 지연을 기반으로 랜덤 값을 사용하는지 확인.
        """
        backoff = DecorrelatedJitterBackoff(base_delay=1.0, max_delay=300.0)
        backoff.calculate(1)  # sets _previous_delay = 1.0
        delay2 = backoff.calculate(2)
        # delay2는 [1.0, 3.0] 범위 (base_delay ~ previous*3)
        assert 1.0 <= delay2 <= 3.0

    def test_max_delay_cap(self):
        """Max delay cap
        max_delay를 초과하지 않는지 확인.
        """
        backoff = DecorrelatedJitterBackoff(base_delay=100.0, max_delay=200.0)
        backoff.calculate(1)  # _previous_delay = 100
        for _ in range(100):
            d = backoff.calculate(2)
            assert d <= 200.0

    def test_reset_clears_previous(self):
        """Reset clears previous delay
        reset 후 새로운 시퀀스가 시작되는지 확인.
        """
        backoff = DecorrelatedJitterBackoff(base_delay=1.0, max_delay=300.0)
        backoff.calculate(1)
        backoff.calculate(2)
        backoff.reset()
        # reset 후 attempt=1은 다시 base_delay 반환
        assert backoff.calculate(1) == 1.0

    def test_from_settings(self):
        """From settings factory
        Settings 기반 팩토리 메서드가 올바르게 동작하는지 확인.
        """
        mock_settings = MagicMock()
        mock_settings.decorrelated_base_delay = 2.0
        mock_settings.decorrelated_max_delay = 150.0
        backoff = DecorrelatedJitterBackoff.from_settings(settings=mock_settings)
        assert backoff.base_delay == 2.0
        assert backoff.max_delay == 150.0


# =============================================================================
# Factory Function Tests
# =============================================================================


class TestGetBackoffCalculator:
    """get_backoff_calculator 팩토리 함수 테스트."""

    def test_create_exponential(self):
        """Create exponential strategy
        'exponential' 전략 인스턴스 생성 확인.
        """
        calc = get_backoff_calculator("exponential", base_delay=2.0)
        assert isinstance(calc, ExponentialBackoff)
        assert calc.base_delay == 2.0

    def test_create_linear(self):
        """Create linear strategy
        'linear' 전략 인스턴스 생성 확인.
        """
        calc = get_backoff_calculator("linear", base_delay=1.0, increment=3.0)
        assert isinstance(calc, LinearBackoff)

    def test_create_constant(self):
        """Create constant strategy
        'constant' 전략 인스턴스 생성 확인.
        """
        calc = get_backoff_calculator("constant", delay=5.0)
        assert isinstance(calc, ConstantBackoff)

    def test_create_decorrelated(self):
        """Create decorrelated strategy
        'decorrelated' 전략 인스턴스 생성 확인.
        """
        calc = get_backoff_calculator("decorrelated", base_delay=1.0)
        assert isinstance(calc, DecorrelatedJitterBackoff)

    def test_unknown_strategy_raises(self):
        """Unknown strategy raises ValueError
        존재하지 않는 전략 이름을 지정하면 ValueError가 발생하는지 확인.
        """
        with pytest.raises(ValueError, match="Unknown backoff strategy"):
            get_backoff_calculator("unknown_strategy")


# =============================================================================
# LegacyBackoffCalculator Tests
# =============================================================================


class TestLegacyBackoffCalculator:
    """LegacyBackoffCalculator 호환성 테스트."""

    def test_basic_exponential(self):
        """Basic exponential backoff
        기본 지수 백오프 계산이 올바른지 확인. base^attempt 패턴.
        """
        config = LegacyBackoffConfig(base=4, max_delay=180, jitter_percent=0, min_delay=1)
        calc = LegacyBackoffCalculator(config)
        assert calc.calculate(1, with_jitter=False) == 4  # 4^1
        assert calc.calculate(2, with_jitter=False) == 16  # 4^2
        assert calc.calculate(3, with_jitter=False) == 64  # 4^3

    def test_max_delay_cap(self):
        """Max delay cap
        max_delay를 초과하지 않는지 확인.
        """
        config = LegacyBackoffConfig(base=4, max_delay=50, jitter_percent=0, min_delay=1)
        calc = LegacyBackoffCalculator(config)
        assert calc.calculate(4, with_jitter=False) == 50  # 4^4=256 → 50

    def test_min_delay_for_zero_attempt(self):
        """Min delay for zero attempt
        attempt < 1이면 min_delay를 반환하는지 확인.
        """
        config = LegacyBackoffConfig(base=4, min_delay=2)
        calc = LegacyBackoffCalculator(config)
        assert calc.calculate(0) == 2

    def test_jitter_applied(self):
        """Jitter applied
        jitter가 적용되면 결과가 일정 범위 내에 있는지 확인.
        """
        config = LegacyBackoffConfig(base=4, max_delay=180, jitter_percent=25, min_delay=1)
        calc = LegacyBackoffCalculator(config)
        results = [calc.calculate(2, with_jitter=True) for _ in range(100)]
        # base 16, jitter_percent=25 → 16 ± 4 → [12, 20]
        assert all(r >= 1 for r in results)  # min_delay 보장
        assert len(set(results)) > 1  # jitter가 적용되었으므로 값이 다양해야 함

    def test_get_delays_sequence(self):
        """Get delays sequence
        여러 attempt에 대한 delay 시퀀스가 올바른지 확인.
        """
        config = LegacyBackoffConfig(base=2, max_delay=100, jitter_percent=0, min_delay=1)
        calc = LegacyBackoffCalculator(config)
        delays = calc.get_delays_sequence(4, with_jitter=False)
        assert delays == [2, 4, 8, 16]

    def test_from_settings(self):
        """From settings factory
        Settings 기반 팩토리 메서드가 올바르게 동작하는지 확인.
        """
        mock_settings = MagicMock()
        mock_settings.legacy_base = 3
        mock_settings.legacy_max_delay = 100
        mock_settings.legacy_jitter_percent = 10
        mock_settings.legacy_min_delay = 2
        config = LegacyBackoffConfig.from_settings(settings=mock_settings)
        assert config.base == 3
        assert config.max_delay == 100


# =============================================================================
# calculate_backoff Convenience Function Tests
# =============================================================================


class TestCalculateBackoff:
    """calculate_backoff 편의 함수 테스트."""

    def test_basic_usage(self):
        """Basic usage
        기본 사용법이 올바르게 동작하는지 확인.
        """
        delay = calculate_backoff(attempt=1, base=4, max_delay=180, jitter_percent=0)
        assert delay == 4

    def test_with_jitter(self):
        """With jitter
        jitter가 적용되면 결과가 1 이상인지 확인.
        """
        delay = calculate_backoff(attempt=2, base=4, max_delay=180, jitter_percent=25)
        assert delay >= 1  # min_delay 기본값


# =============================================================================
# Abstract BackoffStrategy Interface Tests
# =============================================================================


class TestBackoffStrategyInterface:
    """BackoffStrategy 추상 인터페이스 테스트."""

    def test_cannot_instantiate_abstract(self):
        """Cannot instantiate abstract class
        추상 클래스를 직접 인스턴스화할 수 없는지 확인.
        """
        with pytest.raises(TypeError):
            BackoffStrategy()
