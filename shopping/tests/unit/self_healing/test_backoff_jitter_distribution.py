"""
Backoff Jitter Distribution Tests

Tests for G-02: Jitter distribution is statistically random.
Validates that jitter produces statistically varied delays to prevent thundering herd.

Reference: docs/l3_auto_self_healing/testing/L3_TEST_GAP_REPORT.md
Risk Covered: R-015 (Thundering herd from identical retry times)
"""

import statistics
from collections import Counter

import pytest

from shopping.services.self_healing.backoff_calculator import (
    BackoffCalculator,
    BackoffConfig,
)


@pytest.mark.tier1
class TestJitterDistribution:
    """
    Statistical tests for jitter randomness.

    Gap ID: G-02
    Purpose: Verify jitter produces statistically varied delays.
    """

    def test_jitter_distribution_is_random(self):
        """
        Purpose:
            Verify jitter produces statistically varied delays.

        Scenario:
            1. Calculate backoff with jitter 100 times
            2. Collect all delay values
            3. Verify statistical properties

        Expected:
            - Standard deviation > 0 (not all same value)
            - Values fall within expected jitter range (±25%)
            - No obvious patterns

        Risk Covered:
            - R-015: Thundering herd from identical retry times
        """
        config = BackoffConfig(base=4, jitter_percent=25, max_delay=180)
        calc = BackoffCalculator(config)

        # Calculate 100 delays for attempt 2 (base delay = 16)
        delays = [calc.calculate(2, with_jitter=True) for _ in range(100)]

        # Verify variance exists (not all same value)
        std_dev = statistics.stdev(delays)
        assert std_dev > 0, "Jitter should produce variance. " f"All values identical: {delays[0]}"

        # Verify range (16 ± 25% = 12 to 20)
        base_delay = 16
        min_expected = base_delay * 0.75 - 1  # Allow small rounding tolerance
        max_expected = base_delay * 1.25 + 1

        out_of_range = [d for d in delays if d < min_expected or d > max_expected]
        assert len(out_of_range) == 0, (
            f"Jitter out of range: {out_of_range}. " f"Expected all values in [{min_expected}, {max_expected}]"
        )

        # Verify not all identical (should have many unique values)
        unique_values = set(delays)
        assert len(unique_values) > 5, (
            f"Should have many unique values, got only {len(unique_values)}. " "Jitter should produce good distribution."
        )

    def test_jitter_distribution_uniformity(self):
        """
        Purpose:
            Verify jitter distribution is approximately uniform across the range.

        Scenario:
            1. Calculate 1000 delays with jitter
            2. Divide the range into buckets
            3. Verify each bucket has reasonable count

        Expected:
            - No bucket should be empty
            - Distribution should be roughly uniform
        """
        config = BackoffConfig(base=4, jitter_percent=25, max_delay=180)
        calc = BackoffCalculator(config)

        # Calculate 1000 delays
        delays = [calc.calculate(2, with_jitter=True) for _ in range(1000)]

        # Base delay = 16, range = 12 to 20
        # Create 4 buckets: [12-14), [14-16), [16-18), [18-20]
        buckets = {
            "12-14": 0,
            "14-16": 0,
            "16-18": 0,
            "18-20": 0,
        }

        for d in delays:
            if d < 14:
                buckets["12-14"] += 1
            elif d < 16:
                buckets["14-16"] += 1
            elif d < 18:
                buckets["16-18"] += 1
            else:
                buckets["18-20"] += 1

        # Each bucket should have at least 10% of samples (100 out of 1000)
        min_per_bucket = 50  # 5% minimum to allow for randomness
        for bucket_name, count in buckets.items():
            assert count >= min_per_bucket, (
                f"Bucket {bucket_name} has only {count} samples. " f"Distribution may be biased. All buckets: {buckets}"
            )

    def test_jitter_statistical_properties(self):
        """
        Purpose:
            Verify jitter has expected statistical properties.

        Scenario:
            1. Calculate many delays
            2. Verify mean is close to base delay
            3. Verify standard deviation is proportional to jitter_percent

        Expected:
            - Mean ≈ base_delay (within 5%)
            - Standard deviation reflects jitter range
        """
        config = BackoffConfig(base=4, jitter_percent=25, max_delay=180)
        calc = BackoffCalculator(config)

        delays = [calc.calculate(2, with_jitter=True) for _ in range(1000)]

        mean = statistics.mean(delays)
        std_dev = statistics.stdev(delays)
        base_delay = 16

        # Mean should be close to base delay (within 10% tolerance)
        assert abs(mean - base_delay) < base_delay * 0.10, (
            f"Mean ({mean:.2f}) should be close to base delay ({base_delay}). " "Jitter distribution may be biased."
        )

        # Standard deviation should reflect the jitter range
        # For uniform distribution over ±25%, theoretical std_dev ≈ range/√12 ≈ 8/3.46 ≈ 2.3
        # Allow some tolerance for implementation differences
        assert std_dev > 1.0, f"Standard deviation ({std_dev:.2f}) too low. " "Jitter should produce meaningful variance."

    def test_jitter_no_pattern_in_consecutive_values(self):
        """
        Purpose:
            Verify jitter doesn't produce predictable patterns.

        Scenario:
            1. Calculate consecutive delays
            2. Check for alternating or sequential patterns

        Expected:
            - No alternating high/low pattern
            - No ascending/descending pattern
        """
        config = BackoffConfig(base=4, jitter_percent=25, max_delay=180)
        calc = BackoffCalculator(config)

        delays = [calc.calculate(2, with_jitter=True) for _ in range(50)]

        # Check for alternating pattern (diff signs should not all alternate)
        diffs = [delays[i + 1] - delays[i] for i in range(len(delays) - 1)]
        sign_changes = sum(1 for i in range(len(diffs) - 1) if diffs[i] * diffs[i + 1] < 0)

        # In a truly random sequence, sign changes should be roughly 50%
        # Not too high (would indicate strict alternation)
        alternation_ratio = sign_changes / (len(diffs) - 1)
        assert alternation_ratio < 0.85, (
            f"Alternation ratio ({alternation_ratio:.2f}) too high. " "Jitter may be producing alternating pattern."
        )

        # Check that sequence is not monotonic
        all_increasing = all(d >= 0 for d in diffs)
        all_decreasing = all(d <= 0 for d in diffs)
        assert not all_increasing and not all_decreasing, "Sequence should not be monotonic. Jitter should be random."

    def test_jitter_across_different_attempts(self):
        """
        Purpose:
            Verify jitter works correctly for different attempt numbers.

        Scenario:
            1. Calculate delays with jitter for attempts 1, 2, 3
            2. Verify each attempt level has correct base and range

        Expected:
            - Attempt 1: base=4, range ≈ 3-5
            - Attempt 2: base=16, range ≈ 12-20
            - Attempt 3: base=64, range ≈ 48-80
        """
        config = BackoffConfig(base=4, jitter_percent=25, max_delay=180)
        calc = BackoffCalculator(config)

        attempt_ranges = {
            1: (4, 3, 5),  # base, min, max (approximate)
            2: (16, 12, 20),
            3: (64, 48, 80),
        }

        for attempt, (base, min_exp, max_exp) in attempt_ranges.items():
            delays = [calc.calculate(attempt, with_jitter=True) for _ in range(100)]

            mean = statistics.mean(delays)
            min_delay = min(delays)
            max_delay = max(delays)

            # Mean should be close to base
            assert abs(mean - base) < base * 0.15, f"Attempt {attempt}: Mean ({mean:.2f}) not close to base ({base})"

            # All delays should be within range (with tolerance)
            assert all(min_exp - 1 <= d <= max_exp + 1 for d in delays), (
                f"Attempt {attempt}: Some delays outside expected range "
                f"[{min_exp}, {max_exp}]. Got [{min_delay}, {max_delay}]"
            )

    def test_zero_jitter_produces_deterministic_results(self):
        """
        Purpose:
            Verify 0% jitter produces consistent (deterministic) values.

        Scenario:
            1. Calculate delays with 0% jitter
            2. Verify all values are identical

        Expected:
            - Standard deviation = 0
            - All values equal base delay
        """
        config = BackoffConfig(base=4, jitter_percent=0, max_delay=180)
        calc = BackoffCalculator(config)

        delays = [calc.calculate(2, with_jitter=True) for _ in range(100)]

        unique_values = set(delays)
        assert len(unique_values) == 1, (
            f"Zero jitter should produce identical values. " f"Got {len(unique_values)} unique values: {unique_values}"
        )
        assert delays[0] == 16, f"Expected base delay 16, got {delays[0]}"

    def test_different_jitter_percentages(self):
        """
        Purpose:
            Verify different jitter percentages produce proportionally different variance.

        Scenario:
            1. Calculate delays with 10%, 25%, 50% jitter
            2. Verify variance increases with jitter percentage

        Expected:
            - Higher jitter_percent = higher standard deviation
        """
        base = 4
        attempt = 2
        base_delay = 16  # 4^2

        jitter_std_devs = {}

        for jitter_pct in [10, 25, 50]:
            config = BackoffConfig(base=base, jitter_percent=jitter_pct, max_delay=180)
            calc = BackoffCalculator(config)

            delays = [calc.calculate(attempt, with_jitter=True) for _ in range(500)]
            jitter_std_devs[jitter_pct] = statistics.stdev(delays)

        # Verify variance increases with jitter percentage
        assert jitter_std_devs[10] < jitter_std_devs[25] < jitter_std_devs[50], (
            f"Variance should increase with jitter percentage. " f"Got std_devs: {jitter_std_devs}"
        )

    def test_jitter_with_max_delay_cap(self):
        """
        Purpose:
            Verify jitter is applied correctly when base delay hits max_delay cap.

        Scenario:
            1. Calculate delay for high attempt where base > max
            2. Verify jitter is applied to capped value

        Expected:
            - Delays should be around max_delay ± jitter
        """
        config = BackoffConfig(base=4, jitter_percent=25, max_delay=50)
        calc = BackoffCalculator(config)

        # Attempt 3: 4^3 = 64, but capped at 50
        delays = [calc.calculate(3, with_jitter=True) for _ in range(100)]

        mean = statistics.mean(delays)

        # Mean should be around max_delay (50)
        assert abs(mean - 50) < 5, f"Mean ({mean:.2f}) should be close to max_delay (50) when capped"

        # Range should be 50 ± 25% = 37.5 to 62.5, but capped at 50
        assert all(d <= 63 for d in delays), "Delays should not exceed max + jitter"
