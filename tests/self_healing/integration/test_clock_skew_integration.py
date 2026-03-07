"""
Clock Skew Integration Tests

Tests for clock skew tolerance at the service level,
simulating distributed systems with clock drift.
뛔에 분산 시스템의 시간 동기화 문제를 검증합니다.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone as tz
from typing import Dict, Optional


class MockIdempotencyRepository:
    """In-memory repository for testing idempotency checks."""

    def __init__(self):
        self._keys: Dict[str, datetime] = {}

    def save(self, key: str, timestamp: datetime) -> None:
        """Save a key with its timestamp."""
        self._keys[key] = timestamp

    def exists_in_window(self, key: str, from_time: datetime, to_time: datetime) -> bool:
        """Check if key exists within the time window."""
        if key not in self._keys:
            return False
        key_time = self._keys[key]
        return from_time <= key_time <= to_time

    def get(self, key: str) -> Optional[datetime]:
        """Get the timestamp for a key."""
        return self._keys.get(key)


class TestClockSkewIntegration:
    """Integration tests for clock skew handling in idempotency."""

    @pytest.fixture
    def mock_repo(self):
        """Create a mock repository."""
        return MockIdempotencyRepository()

    def test_duplicate_request_within_skew_tolerance(self, mock_repo):
        """
        Scenario: Two servers with 25-second clock difference
        Same idempotency key should be recognized as duplicate.

        This tests the 2024 Kakao incident scenario where 30s clock skew
        caused duplicate payments.
        """
        from selfhealing.core.time_provider import MockTimeProvider

        # Server A: Normal clock at 12:00:00
        server_a = MockTimeProvider(datetime(2024, 1, 1, 12, 0, 0, tzinfo=tz.utc))

        # Server B: Clock 25 seconds behind (within 30s tolerance)
        server_b = MockTimeProvider(datetime(2024, 1, 1, 12, 0, 0, tzinfo=tz.utc))
        server_b.simulate_clock_skew(-25)

        # Record payment on Server A
        key = "payment_12345"
        mock_repo.save(key, server_a.now())

        # Server B checks if the payment is duplicate
        a_lower, a_upper = server_a.now_with_skew_tolerance(30)
        b_now = server_b.now()

        # Server B's time should be within Server A's tolerance window
        assert a_lower <= b_now <= a_upper

        # The key should be found in the repository
        assert mock_repo.exists_in_window(key, a_lower, a_upper) is True

    def test_clock_skew_exceeds_tolerance(self, mock_repo):
        """
        Scenario: Two servers with 60-second clock difference
        Exceeds 30s tolerance - should be detected as out of sync.
        """
        from selfhealing.core.time_provider import MockTimeProvider

        server_a = MockTimeProvider(datetime(2024, 1, 1, 12, 0, 0, tzinfo=tz.utc))

        # 60 seconds difference (exceeds 30s tolerance)
        server_b = MockTimeProvider(datetime(2024, 1, 1, 12, 0, 0, tzinfo=tz.utc))
        server_b.simulate_clock_skew(-60)

        a_lower, a_upper = server_a.now_with_skew_tolerance(30)
        b_now = server_b.now()

        # Server B's time should be outside Server A's tolerance window
        assert not (a_lower <= b_now <= a_upper)

    def test_legitimate_retry_after_window_expires(self, mock_repo):
        """
        Scenario: Legitimate retry after the idempotency window expires
        Should be recognized as a new request.
        """
        from selfhealing.core.time_provider import MockTimeProvider

        time_provider = MockTimeProvider(datetime(2024, 1, 1, 12, 0, 0, tzinfo=tz.utc))

        # First request
        key = "payment_67890"
        mock_repo.save(key, time_provider.now())

        # 2 hours later (window is 1 hour + skew tolerance)
        time_provider.advance(timedelta(hours=2))

        # Check with 1 hour window + 30s skew tolerance
        window_seconds = 3600  # 1 hour
        skew_tolerance = 30

        lower = time_provider.now() - timedelta(seconds=window_seconds + skew_tolerance)
        upper = time_provider.now() + timedelta(seconds=skew_tolerance)

        # Key should NOT be found (outside the window)
        assert mock_repo.exists_in_window(key, lower, upper) is False

    def test_idempotency_service_with_clock_skew(self):
        """
        Test IdempotencyService with TimeProvider injection.
        """
        from selfhealing.core.time_provider import MockTimeProvider
        from selfhealing.services.idempotency import IdempotencyService

        fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)
        time_provider = MockTimeProvider(fixed_time=fixed)

        service = IdempotencyService(
            time_provider=time_provider,
            clock_skew_tolerance_seconds=30.0,
        )

        # Verify time provider is used
        assert service.now() == fixed

        # Verify clock skew tolerance
        assert service.clock_skew_tolerance == 30.0

        # Test timestamp validation within tolerance
        valid_time = fixed - timedelta(seconds=25)
        assert service.is_timestamp_valid(valid_time) is True

        # Test timestamp validation outside tolerance
        invalid_time = fixed - timedelta(seconds=60)
        assert service.is_timestamp_valid(invalid_time) is False

    def test_distributed_idempotency_scenario(self, mock_repo):
        """
        Full distributed scenario test:
        - 3 servers with varying clock drifts
        - All should agree on idempotency within tolerance
        """
        from selfhealing.core.time_provider import MockTimeProvider

        base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=tz.utc)
        tolerance = 30  # 30 seconds

        # Server A: Master (reference clock)
        server_a = MockTimeProvider(base_time)

        # Server B: 15 seconds ahead
        server_b = MockTimeProvider(base_time)
        server_b.simulate_clock_skew(15)

        # Server C: 20 seconds behind
        server_c = MockTimeProvider(base_time)
        server_c.simulate_clock_skew(-20)

        # Payment recorded on Server A
        key = "distributed_payment_001"
        mock_repo.save(key, server_a.now())

        # Server B checks (15s ahead)
        b_lower, b_upper = server_b.now_with_skew_tolerance(tolerance)
        assert mock_repo.exists_in_window(key, b_lower, b_upper) is True

        # Server C checks (20s behind)
        c_lower, c_upper = server_c.now_with_skew_tolerance(tolerance)
        assert mock_repo.exists_in_window(key, c_lower, c_upper) is True

    def test_jwt_expiry_with_clock_skew(self):
        """
        Scenario: JWT token validation with clock skew
        Token issued on one server, validated on another with clock drift.
        """
        from selfhealing.core.time_provider import MockTimeProvider

        # Token issuer server
        issuer = MockTimeProvider(datetime(2024, 1, 1, 12, 0, 0, tzinfo=tz.utc))
        token_issued_at = issuer.now()
        token_expires_at = token_issued_at + timedelta(hours=1)

        # Validator server with 20 second clock drift
        validator = MockTimeProvider(datetime(2024, 1, 1, 12, 0, 0, tzinfo=tz.utc))
        validator.simulate_clock_skew(20)

        # With tolerance, token should still be valid
        lower, upper = validator.now_with_skew_tolerance(30)

        # Token issued_at should be within reasonable past
        assert token_issued_at <= upper

        # Token should not be expired yet
        assert validator.now() < token_expires_at + timedelta(seconds=30)

    def test_time_provider_window_expansion(self):
        """
        Test that now_with_skew_tolerance properly expands the window.
        """
        from selfhealing.core.time_provider import MockTimeProvider

        fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=tz.utc)
        provider = MockTimeProvider(fixed_time=fixed)

        # 30 second tolerance
        lower, upper = provider.now_with_skew_tolerance(30)

        assert lower == fixed - timedelta(seconds=30)
        assert upper == fixed + timedelta(seconds=30)
        assert (upper - lower).total_seconds() == 60


class TestTimezoneHandling:
    """Tests for timezone handling with clock skew."""

    def test_different_timezone_servers(self):
        """
        Scenario: Servers in different timezones should still work.
        All times should be normalized to UTC.
        """
        from selfhealing.core.time_provider import MockTimeProvider

        # Server in UTC
        utc_server = MockTimeProvider(datetime(2024, 1, 1, 12, 0, 0, tzinfo=tz.utc))

        # Create Seoul time (UTC+9)
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo  # type: ignore

        seoul_tz = ZoneInfo("Asia/Seoul")
        # Same moment in time: 12:00 UTC = 21:00 KST
        seoul_time = datetime(2024, 1, 1, 21, 0, 0, tzinfo=seoul_tz)
        seoul_server = MockTimeProvider(seoul_time)

        # Both should represent the same UTC time
        assert utc_server.utcnow() == seoul_server.utcnow()

    def test_naive_datetime_handling(self):
        """
        Naive datetimes should be treated as UTC.
        """
        from selfhealing.core.time_provider import MockTimeProvider

        # Naive datetime (no timezone)
        naive_time = datetime(2024, 1, 1, 12, 0, 0)
        provider = MockTimeProvider()

        # Provider should accept naive datetime
        provider.set_time(naive_time)

        # Should now have UTC timezone
        assert provider.now().tzinfo == tz.utc
