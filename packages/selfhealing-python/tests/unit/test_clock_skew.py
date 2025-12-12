"""
Stage 23: Clock Skew / TimeProvider Tests

Tests for the TimeProvider abstraction and clock skew tolerance
in distributed systems.

Reference: docs/STAGE_23_CLOCK_SKEW.md
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone as tz
from unittest.mock import MagicMock, patch


class TestTimeProvider:
    """Tests for TimeProvider interface and implementations."""

    def test_system_time_provider_now_returns_datetime(self):
        """SystemTimeProvider.now() returns timezone-aware datetime."""
        from selfhealing.core.time_provider import SystemTimeProvider

        provider = SystemTimeProvider()
        result = provider.now()

        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_system_time_provider_utcnow_returns_utc(self):
        """SystemTimeProvider.utcnow() returns UTC datetime."""
        from selfhealing.core.time_provider import SystemTimeProvider

        provider = SystemTimeProvider()
        result = provider.utcnow()

        assert isinstance(result, datetime)
        assert result.tzinfo == tz.utc

    def test_system_time_provider_custom_timezone(self):
        """SystemTimeProvider can use custom timezone."""
        from selfhealing.core.time_provider import SystemTimeProvider

        provider = SystemTimeProvider(default_timezone="Asia/Seoul")
        result = provider.now()

        # Seoul is UTC+9
        assert result.tzinfo is not None
        # The offset should be +9 hours
        offset = result.utcoffset()
        assert offset is not None
        assert offset == timedelta(hours=9)


class TestMockTimeProvider:
    """Tests for MockTimeProvider."""

    def test_mock_time_provider_fixed_time(self):
        """MockTimeProvider returns fixed time."""
        from selfhealing.core.time_provider import MockTimeProvider

        fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)
        provider = MockTimeProvider(fixed_time=fixed)

        assert provider.now() == fixed

    def test_mock_time_provider_advance(self):
        """MockTimeProvider can advance time."""
        from selfhealing.core.time_provider import MockTimeProvider

        fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)
        provider = MockTimeProvider(fixed_time=fixed)

        new_time = provider.advance(timedelta(hours=1))

        assert new_time == datetime(2024, 1, 15, 13, 0, 0, tzinfo=tz.utc)
        assert provider.now() == new_time

    def test_mock_time_provider_rewind(self):
        """MockTimeProvider can rewind time."""
        from selfhealing.core.time_provider import MockTimeProvider

        fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)
        provider = MockTimeProvider(fixed_time=fixed)

        new_time = provider.rewind(timedelta(hours=2))

        assert new_time == datetime(2024, 1, 15, 10, 0, 0, tzinfo=tz.utc)

    def test_mock_time_provider_set_time(self):
        """MockTimeProvider can set arbitrary time."""
        from selfhealing.core.time_provider import MockTimeProvider

        provider = MockTimeProvider()
        new_time = datetime(2030, 6, 15, 18, 30, 0, tzinfo=tz.utc)

        provider.set_time(new_time)

        assert provider.now() == new_time

    def test_mock_time_provider_time_log(self):
        """MockTimeProvider tracks time changes."""
        from selfhealing.core.time_provider import MockTimeProvider

        fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)
        provider = MockTimeProvider(fixed_time=fixed)

        provider.advance(timedelta(minutes=30))
        provider.set_time(datetime(2024, 1, 16, 12, 0, 0, tzinfo=tz.utc))

        assert len(provider.time_log) == 3
        assert provider.time_log[0] == fixed

    def test_mock_time_provider_freeze_context_manager(self):
        """MockTimeProvider.freeze() creates a context manager."""
        from selfhealing.core.time_provider import MockTimeProvider

        fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)
        provider = MockTimeProvider(fixed_time=fixed)

        with provider.freeze() as frozen:
            frozen.advance(timedelta(hours=5))
            assert frozen.now() == datetime(2024, 1, 15, 17, 0, 0, tzinfo=tz.utc)

        # After context, time should be restored
        assert provider.now() == fixed

    def test_mock_time_provider_reset(self):
        """MockTimeProvider.reset() clears log and updates to current time."""
        from selfhealing.core.time_provider import MockTimeProvider

        fixed = datetime(2024, 1, 1, 0, 0, 0, tzinfo=tz.utc)
        provider = MockTimeProvider(fixed_time=fixed)
        provider.advance(timedelta(days=100))

        provider.reset()

        # Should be close to current time
        now = datetime.now(tz.utc)
        diff = abs((provider.now() - now).total_seconds())
        assert diff < 2  # Within 2 seconds
        assert len(provider.time_log) == 1


class TestClockSkewTolerance:
    """Tests for clock skew tolerance functionality."""

    def test_is_within_tolerance_exact_time(self):
        """Exact same time is within tolerance."""
        from selfhealing.core.time_provider import MockTimeProvider

        fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)
        provider = MockTimeProvider(fixed_time=fixed)

        result = provider.is_within_tolerance(fixed, timedelta(seconds=5))

        assert result is True

    def test_is_within_tolerance_past_within(self):
        """Timestamp in recent past is within tolerance."""
        from selfhealing.core.time_provider import MockTimeProvider

        fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)
        provider = MockTimeProvider(fixed_time=fixed)

        past_time = fixed - timedelta(seconds=3)
        result = provider.is_within_tolerance(past_time, timedelta(seconds=5))

        assert result is True

    def test_is_within_tolerance_future_within(self):
        """Timestamp in near future is within tolerance."""
        from selfhealing.core.time_provider import MockTimeProvider

        fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)
        provider = MockTimeProvider(fixed_time=fixed)

        future_time = fixed + timedelta(seconds=3)
        result = provider.is_within_tolerance(future_time, timedelta(seconds=5))

        assert result is True

    def test_is_within_tolerance_past_outside(self):
        """Timestamp too far in past is outside tolerance."""
        from selfhealing.core.time_provider import MockTimeProvider

        fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)
        provider = MockTimeProvider(fixed_time=fixed)

        past_time = fixed - timedelta(seconds=10)
        result = provider.is_within_tolerance(past_time, timedelta(seconds=5))

        assert result is False

    def test_is_within_tolerance_future_outside(self):
        """Timestamp too far in future is outside tolerance."""
        from selfhealing.core.time_provider import MockTimeProvider

        fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)
        provider = MockTimeProvider(fixed_time=fixed)

        future_time = fixed + timedelta(seconds=10)
        result = provider.is_within_tolerance(future_time, timedelta(seconds=5))

        assert result is False

    def test_is_within_tolerance_naive_datetime(self):
        """Naive datetimes are handled correctly."""
        from selfhealing.core.time_provider import MockTimeProvider

        fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)
        provider = MockTimeProvider(fixed_time=fixed)

        # Naive datetime (assumed UTC)
        naive_time = datetime(2024, 1, 15, 12, 0, 2)
        result = provider.is_within_tolerance(naive_time, timedelta(seconds=5))

        assert result is True

    def test_get_skew_adjusted_window(self):
        """get_skew_adjusted_window expands time window correctly."""
        from selfhealing.core.time_provider import SystemTimeProvider

        provider = SystemTimeProvider()
        start = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)
        end = datetime(2024, 1, 15, 13, 0, 0, tzinfo=tz.utc)

        adj_start, adj_end = provider.get_skew_adjusted_window(start, end, timedelta(seconds=30))

        assert adj_start == start - timedelta(seconds=30)
        assert adj_end == end + timedelta(seconds=30)

    def test_now_with_skew_tolerance_default(self):
        """now_with_skew_tolerance returns 60 second window by default."""
        from selfhealing.core.time_provider import MockTimeProvider

        fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)
        provider = MockTimeProvider(fixed_time=fixed)

        lower, upper = provider.now_with_skew_tolerance()

        assert lower == fixed - timedelta(seconds=30)
        assert upper == fixed + timedelta(seconds=30)
        assert (upper - lower).total_seconds() == 60.0

    def test_now_with_skew_tolerance_custom(self):
        """now_with_skew_tolerance accepts custom tolerance."""
        from selfhealing.core.time_provider import MockTimeProvider

        fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)
        provider = MockTimeProvider(fixed_time=fixed)

        lower, upper = provider.now_with_skew_tolerance(tolerance_seconds=10.0)

        assert lower == fixed - timedelta(seconds=10)
        assert upper == fixed + timedelta(seconds=10)
        assert (upper - lower).total_seconds() == 20.0


class TestSimulateClockSkew:
    """Tests for simulate_clock_skew method."""

    def test_simulate_clock_skew_positive(self):
        """simulate_clock_skew with positive seconds advances time."""
        from selfhealing.core.time_provider import MockTimeProvider

        fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)
        provider = MockTimeProvider(fixed_time=fixed)

        provider.simulate_clock_skew(30)

        assert provider.now() == fixed + timedelta(seconds=30)

    def test_simulate_clock_skew_negative(self):
        """simulate_clock_skew with negative seconds rewinds time."""
        from selfhealing.core.time_provider import MockTimeProvider

        fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)
        provider = MockTimeProvider(fixed_time=fixed)

        provider.simulate_clock_skew(-25)

        assert provider.now() == fixed - timedelta(seconds=25)

    def test_simulate_clock_skew_distributed_scenario(self):
        """Simulate distributed servers with clock skew."""
        from selfhealing.core.time_provider import MockTimeProvider

        base_time = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)

        # Server A: Reference
        server_a = MockTimeProvider(fixed_time=base_time)

        # Server B: 25 seconds behind (within 30s tolerance)
        server_b = MockTimeProvider(fixed_time=base_time)
        server_b.simulate_clock_skew(-25)

        # Check if Server B's time is within Server A's tolerance
        a_lower, a_upper = server_a.now_with_skew_tolerance(30)
        b_now = server_b.now()

        assert a_lower <= b_now <= a_upper

    def test_simulate_clock_skew_exceeds_tolerance(self):
        """Detect when clock skew exceeds tolerance."""
        from selfhealing.core.time_provider import MockTimeProvider

        base_time = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)

        server_a = MockTimeProvider(fixed_time=base_time)
        server_b = MockTimeProvider(fixed_time=base_time)
        server_b.simulate_clock_skew(-60)  # 60 seconds behind

        a_lower, a_upper = server_a.now_with_skew_tolerance(30)
        b_now = server_b.now()

        # Server B should be outside tolerance
        assert not (a_lower <= b_now <= a_upper)


class TestGlobalTimeProvider:
    """Tests for global time provider management."""

    def test_get_time_provider_default(self):
        """Default time provider is SystemTimeProvider."""
        from selfhealing.core.time_provider import (
            get_time_provider,
            reset_time_provider,
            SystemTimeProvider,
        )

        reset_time_provider()
        provider = get_time_provider()

        assert isinstance(provider, SystemTimeProvider)

    def test_set_time_provider(self):
        """Can set custom time provider globally."""
        from selfhealing.core.time_provider import (
            get_time_provider,
            set_time_provider,
            reset_time_provider,
            MockTimeProvider,
        )

        try:
            mock = MockTimeProvider(datetime(2024, 1, 1, 0, 0, 0, tzinfo=tz.utc))
            set_time_provider(mock)

            result = get_time_provider()
            assert result is mock
        finally:
            reset_time_provider()

    def test_convenience_now_uses_global(self):
        """now() function uses global time provider."""
        from selfhealing.core.time_provider import (
            set_time_provider,
            reset_time_provider,
            MockTimeProvider,
            now,
        )

        try:
            fixed = datetime(2024, 6, 15, 18, 30, 0, tzinfo=tz.utc)
            set_time_provider(MockTimeProvider(fixed_time=fixed))

            result = now()
            assert result == fixed
        finally:
            reset_time_provider()

    def test_is_within_clock_skew_convenience(self):
        """is_within_clock_skew() uses global provider."""
        from selfhealing.core.time_provider import (
            set_time_provider,
            reset_time_provider,
            MockTimeProvider,
            is_within_clock_skew,
        )

        try:
            fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)
            set_time_provider(MockTimeProvider(fixed_time=fixed))

            # Within 5 seconds
            assert is_within_clock_skew(fixed - timedelta(seconds=3)) is True
            # Outside 5 seconds
            assert is_within_clock_skew(fixed - timedelta(seconds=10)) is False
        finally:
            reset_time_provider()


class TestTimezoneIntegration:
    """Tests for timezone.py integration with TimeProvider."""

    def test_timezone_now_uses_time_provider(self):
        """timezone.now() uses global TimeProvider."""
        from selfhealing.core.time_provider import (
            set_time_provider,
            reset_time_provider,
            MockTimeProvider,
        )
        from selfhealing.core.timezone import now

        try:
            fixed = datetime(2024, 12, 25, 0, 0, 0, tzinfo=tz.utc)
            set_time_provider(MockTimeProvider(fixed_time=fixed))

            result = now()
            assert result == fixed
        finally:
            reset_time_provider()

    def test_timezone_utcnow_uses_time_provider(self):
        """timezone.utcnow() uses global TimeProvider."""
        from selfhealing.core.time_provider import (
            set_time_provider,
            reset_time_provider,
            MockTimeProvider,
        )
        from selfhealing.core.timezone import utcnow

        try:
            fixed = datetime(2024, 12, 25, 12, 0, 0, tzinfo=tz.utc)
            set_time_provider(MockTimeProvider(fixed_time=fixed))

            result = utcnow()
            assert result == fixed
        finally:
            reset_time_provider()


class TestIdempotencyServiceTimeProvider:
    """Tests for IdempotencyService with TimeProvider injection."""

    def test_idempotency_service_accepts_time_provider(self):
        """IdempotencyService can be initialized with TimeProvider."""
        from selfhealing.core.time_provider import MockTimeProvider
        from selfhealing.services.idempotency_service import IdempotencyService

        fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)
        time_provider = MockTimeProvider(fixed_time=fixed)

        service = IdempotencyService(time_provider=time_provider)

        assert service.time_provider is time_provider

    def test_idempotency_service_now_method(self):
        """IdempotencyService.now() uses injected TimeProvider."""
        from selfhealing.core.time_provider import MockTimeProvider
        from selfhealing.services.idempotency_service import IdempotencyService

        fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)
        time_provider = MockTimeProvider(fixed_time=fixed)

        service = IdempotencyService(time_provider=time_provider)

        assert service.now() == fixed

    def test_idempotency_service_clock_skew_from_config(self):
        """IdempotencyService uses config clock_skew_tolerance by default."""
        from selfhealing.services.idempotency_service import IdempotencyService
        from selfhealing.core.config import get_config

        service = IdempotencyService()
        config = get_config()

        assert service.clock_skew_tolerance == config.idempotency.clock_skew_tolerance_seconds

    def test_idempotency_service_custom_clock_skew(self):
        """IdempotencyService can use custom clock_skew_tolerance."""
        from selfhealing.services.idempotency_service import IdempotencyService

        service = IdempotencyService(clock_skew_tolerance_seconds=10.0)

        assert service.clock_skew_tolerance == 10.0

    def test_is_timestamp_valid_within_tolerance(self):
        """is_timestamp_valid returns True for recent timestamps."""
        from selfhealing.core.time_provider import MockTimeProvider
        from selfhealing.services.idempotency_service import IdempotencyService

        fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)
        time_provider = MockTimeProvider(fixed_time=fixed)

        service = IdempotencyService(
            time_provider=time_provider,
            clock_skew_tolerance_seconds=5.0,
        )

        # Within 5 seconds
        valid_time = fixed - timedelta(seconds=3)
        assert service.is_timestamp_valid(valid_time) is True

    def test_is_timestamp_valid_outside_tolerance(self):
        """is_timestamp_valid returns False for old timestamps."""
        from selfhealing.core.time_provider import MockTimeProvider
        from selfhealing.services.idempotency_service import IdempotencyService

        fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)
        time_provider = MockTimeProvider(fixed_time=fixed)

        service = IdempotencyService(
            time_provider=time_provider,
            clock_skew_tolerance_seconds=5.0,
        )

        # Outside 5 seconds
        invalid_time = fixed - timedelta(seconds=10)
        assert service.is_timestamp_valid(invalid_time) is False

    def test_is_timestamp_valid_custom_tolerance_override(self):
        """is_timestamp_valid can override tolerance."""
        from selfhealing.core.time_provider import MockTimeProvider
        from selfhealing.services.idempotency_service import IdempotencyService

        fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)
        time_provider = MockTimeProvider(fixed_time=fixed)

        service = IdempotencyService(
            time_provider=time_provider,
            clock_skew_tolerance_seconds=5.0,
        )

        # 8 seconds diff - outside default 5s but within override 10s
        test_time = fixed - timedelta(seconds=8)
        assert service.is_timestamp_valid(test_time) is False
        assert service.is_timestamp_valid(test_time, tolerance_seconds=10.0) is True
