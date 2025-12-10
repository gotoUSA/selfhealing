"""
Unit Tests for Backoff Policy

Tests for exponential backoff calculation, jitter application,
and policy boundary conditions.

Migrated from: shopping/tests/unit/self_healing/test_backoff_policy.py
"""

import pytest

from selfhealing.core.backoff import (
    BackoffConfig,
    LegacyBackoffCalculator as BackoffCalculator,
    calculate_backoff,
)


@pytest.mark.tier1
class TestBackoffPolicyDefaults:
    """
    Tests for default backoff policy configuration.

    Purpose:
        Verify that default backoff settings match documented policy.
    """

    def test_default_base_is_4(self):
        """
        Purpose:
            Verify default backoff base matches architecture spec.

        Expected:
            - base = 4 (4^n progression: 4, 16, 64 seconds)
        """
        config = BackoffConfig()
        assert config.base == 4, "Policy Violation: Default backoff base must be 4. " "Check RETRY_BACKOFF_BASE configuration."

    def test_default_max_delay_is_180(self):
        """
        Purpose:
            Verify maximum delay cap matches architecture spec.

        Expected:
            - max_delay = 180 seconds (3 minutes)
        """
        config = BackoffConfig()
        assert config.max_delay == 180, (
            "Policy Violation: Default max delay must be 180 seconds. " "Check RETRY_BACKOFF_MAX configuration."
        )

    def test_default_jitter_is_25_percent(self):
        """
        Purpose:
            Verify jitter percentage prevents thundering herd.

        Expected:
            - jitter_percent = 25 (±25%)
        """
        config = BackoffConfig()
        assert config.jitter_percent == 25, (
            "Policy Violation: Default jitter must be 25%. " "Jitter prevents thundering herd problem."
        )

    def test_default_min_delay_is_1(self):
        """
        Purpose:
            Verify minimum delay is at least 1 second.

        Expected:
            - min_delay = 1 second
        """
        config = BackoffConfig()
        assert config.min_delay == 1, (
            "Policy Violation: Minimum delay must be at least 1 second. " "Zero or negative delays risk retry loops."
        )


@pytest.mark.tier1
class TestBackoffPolicyCalculation:
    """
    Tests for backoff delay calculation logic.

    Purpose:
        Validate mathematical correctness of exponential backoff.
    """

    def test_exponential_progression_without_jitter(self):
        """
        Purpose:
            Verify exponential progression: 4^1=4, 4^2=16, 4^3=64.

        Expected:
            - Attempt 1: 4 seconds
            - Attempt 2: 16 seconds
            - Attempt 3: 64 seconds
        """
        config = BackoffConfig(jitter_percent=0)  # Disable jitter
        calc = BackoffCalculator(config)

        assert calc.calculate(1, with_jitter=False) == 4
        assert calc.calculate(2, with_jitter=False) == 16
        assert calc.calculate(3, with_jitter=False) == 64

    def test_max_delay_capping(self):
        """
        Purpose:
            Verify delay is capped at max_delay.

        Expected:
            - Attempt 4: 256 would exceed 180, so capped at 180
        """
        config = BackoffConfig(max_delay=180, jitter_percent=0)
        calc = BackoffCalculator(config)

        # 4^4 = 256, should be capped at 180
        assert calc.calculate(4, with_jitter=False) == 180

    def test_min_delay_enforcement(self):
        """
        Purpose:
            Verify minimum delay is enforced.

        Expected:
            - Attempt 0 or negative should return min_delay
        """
        config = BackoffConfig(min_delay=5)
        calc = BackoffCalculator(config)

        assert calc.calculate(0) >= 5
        assert calc.calculate(-1) >= 5

    def test_custom_base(self):
        """
        Purpose:
            Verify custom base works correctly.

        Expected:
            - base=2: 2^1=2, 2^2=4, 2^3=8
        """
        config = BackoffConfig(base=2, jitter_percent=0)
        calc = BackoffCalculator(config)

        assert calc.calculate(1, with_jitter=False) == 2
        assert calc.calculate(2, with_jitter=False) == 4
        assert calc.calculate(3, with_jitter=False) == 8

    def test_jitter_within_bounds(self):
        """
        Purpose:
            Verify jitter stays within ±25% bounds.

        Expected:
            - For base delay of 16, jitter should be 12-20
        """
        config = BackoffConfig(jitter_percent=25)
        calc = BackoffCalculator(config)

        # Run many times to verify bounds
        for _ in range(100):
            delay = calc.calculate(2, with_jitter=True)
            assert 12 <= delay <= 20, f"Delay {delay} out of jitter bounds [12, 20]"


@pytest.mark.tier1
class TestBackoffConvenienceFunction:
    """Tests for calculate_backoff convenience function."""

    def test_default_calculation(self):
        """Verify convenience function works with defaults."""
        delay = calculate_backoff(1)
        # Should be around 4 with ±25% jitter = 3-5
        assert 3 <= delay <= 5

    def test_custom_parameters(self):
        """Verify convenience function accepts custom parameters."""
        delay = calculate_backoff(2, base=2, max_delay=100, jitter_percent=0)
        assert delay == 4  # 2^2 = 4


@pytest.mark.tier1
class TestBackoffDelaySequence:
    """Tests for delay sequence generation."""

    def test_get_delays_sequence(self):
        """Verify sequence generation."""
        config = BackoffConfig(jitter_percent=0)
        calc = BackoffCalculator(config)

        delays = calc.get_delays_sequence(3, with_jitter=False)

        assert delays == [4, 16, 64]

    def test_sequence_respects_max_delay(self):
        """Verify sequence respects max_delay."""
        config = BackoffConfig(max_delay=50, jitter_percent=0)
        calc = BackoffCalculator(config)

        delays = calc.get_delays_sequence(4, with_jitter=False)

        # 4, 16, 50 (capped), 50 (capped)
        assert delays == [4, 16, 50, 50]
