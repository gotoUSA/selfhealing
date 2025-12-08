"""
Unit Tests for Backoff Policy

Tests for exponential backoff calculation, jitter application,
and policy boundary conditions.

Reference: docs/testing/SELF_HEALING_TEST_SPECIFICATIONS.md
Risk Covered: R-015 (Retry storm overwhelming PG)
"""

from decimal import Decimal

import pytest

from shopping.services.self_healing.backoff_calculator import (
    BackoffCalculator,
    BackoffConfig,
    calculate_backoff,
)


@pytest.mark.tier1
class TestBackoffPolicyDefaults:
    """
    Tests for default backoff policy configuration.

    Purpose:
        Verify that default backoff settings match documented policy.

    Compliance:
        Internal governance - retry behavior must be predictable.
    """

    def test_default_base_is_4(self):
        """
        Purpose:
            Verify default backoff base matches architecture spec.

        Expected:
            - base = 4 (4^n progression: 4, 16, 64 seconds)

        Risk Covered:
            R-015: Retry storm prevention
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

        Scenario:
            1. Create calculator with base=4, no jitter
            2. Calculate delays for attempts 1, 2, 3
            3. Verify exponential progression

        Expected:
            - Attempt 1: 4 seconds
            - Attempt 2: 16 seconds
            - Attempt 3: 64 seconds
        """
        config = BackoffConfig(base=4, jitter_percent=0)
        calc = BackoffCalculator(config)

        delays = [calc.calculate(i, with_jitter=False) for i in range(1, 4)]

        assert delays == [4, 16, 64], (
            f"Exponential progression failed: expected [4, 16, 64], got {delays}. " "Formula should be: base^attempt."
        )

    def test_max_delay_caps_large_values(self):
        """
        Purpose:
            Verify delays are capped at max_delay.

        Scenario:
            1. Create calculator with base=4, max=50
            2. Calculate delay for attempt 3 (4^3=64 > 50)
            3. Verify capped at 50

        Expected:
            - Delay is capped at max_delay, not actual 64
        """
        config = BackoffConfig(base=4, max_delay=50, jitter_percent=0)
        calc = BackoffCalculator(config)

        delay = calc.calculate(3, with_jitter=False)

        assert delay == 50, (
            f"Max delay cap failed: expected 50, got {delay}. "
            "Delays must be capped at max_delay to prevent excessive waits."
        )

    def test_min_delay_enforced_for_zero_attempt(self):
        """
        Purpose:
            Verify minimum delay is enforced for edge cases.

        Scenario:
            1. Calculate delay for attempt 0
            2. Verify returns min_delay, not 0

        Expected:
            - Returns min_delay (1 second by default)
        """
        config = BackoffConfig(min_delay=1)
        calc = BackoffCalculator(config)

        delay = calc.calculate(0, with_jitter=False)

        assert delay == 1, (
            f"Min delay not enforced for attempt 0: expected 1, got {delay}. " "Edge case handling must return min_delay."
        )

    def test_min_delay_enforced_for_negative_attempt(self):
        """
        Purpose:
            Verify negative attempts don't cause errors.

        Expected:
            - Returns min_delay for negative attempts
        """
        config = BackoffConfig(min_delay=2)
        calc = BackoffCalculator(config)

        delay = calc.calculate(-1, with_jitter=False)

        assert delay == 2, f"Min delay not enforced for negative attempt: expected 2, got {delay}."


@pytest.mark.tier1
class TestBackoffPolicyJitter:
    """
    Tests for jitter application in backoff calculation.

    Purpose:
        Validate jitter distributes retries to prevent thundering herd.
    """

    def test_jitter_produces_variation(self):
        """
        Purpose:
            Verify jitter produces different values across runs.

        Scenario:
            1. Calculate delay 100 times with jitter enabled
            2. Verify not all values are identical

        Expected:
            - At least 2 distinct values in 100 calculations
        """
        config = BackoffConfig(base=4, jitter_percent=25)
        calc = BackoffCalculator(config)

        delays = {calc.calculate(2, with_jitter=True) for _ in range(100)}

        assert len(delays) > 1, (
            "Jitter failed to produce variation: all 100 calculations identical. "
            "Jitter should introduce randomness to prevent thundering herd."
        )

    def test_jitter_stays_within_bounds(self):
        """
        Purpose:
            Verify jitter stays within ±jitter_percent bounds.

        Scenario:
            1. Base delay for attempt 2: 16 seconds
            2. With 25% jitter: expected range 12-20 seconds
            3. Calculate 1000 times, verify all within bounds

        Expected:
            - All values between 12 and 20 (inclusive, allowing rounding)
        """
        config = BackoffConfig(base=4, jitter_percent=25, max_delay=180)
        calc = BackoffCalculator(config)

        base_delay = 16  # 4^2
        min_expected = int(base_delay * 0.75) - 1  # Account for rounding
        max_expected = int(base_delay * 1.25) + 1

        for _ in range(1000):
            delay = calc.calculate(2, with_jitter=True)
            assert min_expected <= delay <= max_expected, (
                f"Jitter out of bounds: {delay} not in [{min_expected}, {max_expected}]. "
                f"Jitter should be ±25% of base delay ({base_delay}s)."
            )

    def test_zero_jitter_is_deterministic(self):
        """
        Purpose:
            Verify 0% jitter produces consistent values.

        Expected:
            - All 100 calculations produce same value
        """
        config = BackoffConfig(base=4, jitter_percent=0)
        calc = BackoffCalculator(config)

        delays = [calc.calculate(2, with_jitter=True) for _ in range(100)]

        assert all(d == 16 for d in delays), (
            "Zero jitter should produce deterministic results. " f"Got varying values: {set(delays)}"
        )


@pytest.mark.tier1
class TestBackoffPolicySequence:
    """
    Tests for delay sequence generation.

    Purpose:
        Validate sequence generation for retry planning.
    """

    def test_sequence_length_matches_max_attempts(self):
        """
        Purpose:
            Verify sequence returns correct number of delays.
        """
        config = BackoffConfig(base=4, jitter_percent=0)
        calc = BackoffCalculator(config)

        sequence = calc.get_delays_sequence(5, with_jitter=False)

        assert len(sequence) == 5, f"Sequence length mismatch: expected 5, got {len(sequence)}."

    def test_sequence_values_are_correct(self):
        """
        Purpose:
            Verify sequence values match individual calculations.
        """
        config = BackoffConfig(base=2, max_delay=100, jitter_percent=0)
        calc = BackoffCalculator(config)

        sequence = calc.get_delays_sequence(5, with_jitter=False)
        expected = [2, 4, 8, 16, 32]  # 2^1, 2^2, 2^3, 2^4, 2^5

        assert sequence == expected, f"Sequence values incorrect: expected {expected}, got {sequence}."

    def test_sequence_applies_max_cap(self):
        """
        Purpose:
            Verify max_delay cap applies to sequence.
        """
        config = BackoffConfig(base=4, max_delay=50, jitter_percent=0)
        calc = BackoffCalculator(config)

        # 4^1=4, 4^2=16, 4^3=64->50, 4^4=256->50
        sequence = calc.get_delays_sequence(4, with_jitter=False)
        expected = [4, 16, 50, 50]

        assert sequence == expected, f"Max cap not applied in sequence: expected {expected}, got {sequence}."


@pytest.mark.tier1
class TestBackoffPolicyConvenienceFunction:
    """
    Tests for convenience function interface.

    Purpose:
        Validate standalone function works correctly.
    """

    def test_calculate_backoff_basic(self):
        """
        Purpose:
            Verify convenience function produces correct results.
        """
        result = calculate_backoff(
            attempt=2,
            base=4,
            max_delay=180,
            jitter_percent=0,
        )

        assert result == 16, f"Convenience function failed: expected 16, got {result}."

    def test_calculate_backoff_with_different_base(self):
        """
        Purpose:
            Verify function works with custom base.
        """
        result = calculate_backoff(
            attempt=3,
            base=2,
            max_delay=180,
            jitter_percent=0,
        )

        assert result == 8, f"Custom base calculation failed: expected 8, got {result}."  # 2^3 = 8


@pytest.mark.tier1
class TestBackoffPolicyEdgeCases:
    """
    Tests for edge cases and boundary conditions.

    Purpose:
        Ensure robustness against unexpected inputs.
    """

    def test_very_large_attempt_number(self):
        """
        Purpose:
            Verify large attempt numbers are handled safely.
        """
        config = BackoffConfig(base=4, max_delay=180, jitter_percent=0)
        calc = BackoffCalculator(config)

        # Attempt 100 would be 4^100 without capping
        delay = calc.calculate(100, with_jitter=False)

        assert delay == 180, f"Large attempt not capped: expected 180, got {delay}."

    def test_base_of_1_produces_constant_delay(self):
        """
        Purpose:
            Verify base=1 produces constant delays (no exponential growth).
        """
        config = BackoffConfig(base=1, max_delay=180, jitter_percent=0)
        calc = BackoffCalculator(config)

        # 1^n = 1 for all n
        delays = calc.get_delays_sequence(3, with_jitter=False)

        assert delays == [1, 1, 1], f"Base=1 should produce constant delays: expected [1, 1, 1], got {delays}."

    def test_min_delay_override_respects_floor(self):
        """
        Purpose:
            Verify custom min_delay is respected.
        """
        config = BackoffConfig(base=1, min_delay=5, jitter_percent=0)
        calc = BackoffCalculator(config)

        # 1^1 = 1, but min_delay=5 should override
        delay = calc.calculate(1, with_jitter=False)

        assert delay == 5, f"Min delay override failed: expected 5, got {delay}."
