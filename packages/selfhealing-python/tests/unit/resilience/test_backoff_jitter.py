"""
Backoff Jitter Distribution Tests

Tests for jitter distribution is statistically random.
Validates that jitter produces statistically varied delays to prevent thundering herd.

Migrated from: shopping/tests/unit/self_healing/test_backoff_jitter_distribution.py
"""

import statistics
from collections import Counter

import pytest

from selfhealing.core.backoff import (
    LegacyBackoffConfig,
    LegacyBackoffCalculator as BackoffCalculator,
)


@pytest.mark.tier1
class TestJitterDistribution:
    """
    Statistical tests for jitter randomness.

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
        """
        config = LegacyBackoffConfig(base=4, jitter_percent=25, max_delay=180)
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
        config = LegacyBackoffConfig(base=4, jitter_percent=25, max_delay=180)
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

    def test_no_jitter_produces_identical_values(self):
        """
        Purpose:
            Verify disabling jitter produces identical delays.

        Expected:
            - All 100 calculations return the same value
        """
        config = LegacyBackoffConfig(base=4, jitter_percent=0, max_delay=180)
        calc = BackoffCalculator(config)

        delays = [calc.calculate(2, with_jitter=False) for _ in range(100)]

        unique_values = set(delays)
        assert len(unique_values) == 1, f"Without jitter, all delays should be identical. Got: {unique_values}"
        assert delays[0] == 16, "Base delay for attempt 2 should be 16"

    def test_jitter_is_symmetric(self):
        """
        Purpose:
            Verify jitter distributes evenly above and below base.

        Expected:
            - Approximately 50% of values above base
            - Approximately 50% of values below base
        """
        config = LegacyBackoffConfig(base=4, jitter_percent=25, max_delay=180)
        calc = BackoffCalculator(config)

        delays = [calc.calculate(2, with_jitter=True) for _ in range(1000)]
        base = 16

        above_count = sum(1 for d in delays if d > base)
        below_count = sum(1 for d in delays if d < base)
        equal_count = sum(1 for d in delays if d == base)

        total = above_count + below_count + equal_count

        # Allow for ±10% variance from 50/50
        above_percent = above_count / total
        below_percent = below_count / total

        assert 0.3 < above_percent < 0.7, (
            f"Above/below distribution should be roughly even. " f"Above: {above_percent:.1%}, Below: {below_percent:.1%}"
        )
