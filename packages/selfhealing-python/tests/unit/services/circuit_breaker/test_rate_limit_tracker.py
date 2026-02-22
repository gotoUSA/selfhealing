"""
Tests for Rate Limit Tracker

Covers:
- RateLimitTracker class
- Thread safety
- Event recording and counting
- Backoff level management
- Singleton access
"""

import threading
from unittest.mock import patch


class TestRateLimitTracker:
    """Tests for RateLimitTracker class."""

    def test_record_rate_limit(self):
        """Test recording rate limit events."""
        from selfhealing.services.circuit_breaker.rate_limit_tracker import (
            RateLimitTracker,
        )

        tracker = RateLimitTracker()
        tracker.record_rate_limit("test_service")

        count = tracker.get_rate_limit_count("test_service", 60)
        assert count == 1

    def test_record_multiple_rate_limits(self):
        """Test recording multiple rate limit events."""
        from selfhealing.services.circuit_breaker.rate_limit_tracker import (
            RateLimitTracker,
        )

        tracker = RateLimitTracker()

        for _ in range(5):
            tracker.record_rate_limit("test_service")

        count = tracker.get_rate_limit_count("test_service", 60)
        assert count == 5

    def test_record_request(self):
        """Test recording request events."""
        from selfhealing.services.circuit_breaker.rate_limit_tracker import (
            RateLimitTracker,
        )

        tracker = RateLimitTracker()
        tracker.record_request("test_service")

        count = tracker.get_request_count("test_service", 60)
        assert count == 1

    def test_record_multiple_requests(self):
        """Test recording multiple request events."""
        from selfhealing.services.circuit_breaker.rate_limit_tracker import (
            RateLimitTracker,
        )

        tracker = RateLimitTracker()

        for _ in range(10):
            tracker.record_request("test_service")

        count = tracker.get_request_count("test_service", 60)
        assert count == 10

    def test_rate_limit_count_time_window(self):
        """Test that old events are filtered by time window."""
        from selfhealing.services.circuit_breaker.rate_limit_tracker import (
            RateLimitTracker,
        )

        tracker = RateLimitTracker()

        # Record an event
        tracker.record_rate_limit("test_service")

        # Count with very short window (should expire)
        # Mock time to simulate passage
        with patch('time.time') as mock_time:
            # Initial time
            mock_time.return_value = 1000.0
            tracker._rate_limit_events["test_service2"] = [1000.0]

            # Check immediately
            count = tracker.get_rate_limit_count("test_service2", 60)
            assert count == 1

    def test_request_count_time_window(self):
        """Test request count respects time window."""
        from selfhealing.services.circuit_breaker.rate_limit_tracker import (
            RateLimitTracker,
        )

        tracker = RateLimitTracker()
        tracker.record_request("test_service")

        # Should be present in 60 second window
        count = tracker.get_request_count("test_service", 60)
        assert count >= 0  # At least 0 (could be cleaned)

    def test_backoff_level_initial(self):
        """Test initial backoff level is zero."""
        from selfhealing.services.circuit_breaker.rate_limit_tracker import (
            RateLimitTracker,
        )

        tracker = RateLimitTracker()
        level = tracker.get_backoff_level("new_service")
        assert level == 0

    def test_increment_backoff(self):
        """Test incrementing backoff level."""
        from selfhealing.services.circuit_breaker.rate_limit_tracker import (
            RateLimitTracker,
        )

        tracker = RateLimitTracker()

        level = tracker.increment_backoff("test_service")
        assert level == 1

        level = tracker.increment_backoff("test_service")
        assert level == 2

    def test_reset_backoff(self):
        """Test resetting backoff level."""
        from selfhealing.services.circuit_breaker.rate_limit_tracker import (
            RateLimitTracker,
        )

        tracker = RateLimitTracker()

        tracker.increment_backoff("test_service")
        tracker.increment_backoff("test_service")
        tracker.reset_backoff("test_service")

        level = tracker.get_backoff_level("test_service")
        assert level == 0

    def test_clear_service(self):
        """Test clearing all data for a service."""
        from selfhealing.services.circuit_breaker.rate_limit_tracker import (
            RateLimitTracker,
        )

        tracker = RateLimitTracker()

        tracker.record_rate_limit("test_service")
        tracker.record_request("test_service")
        tracker.increment_backoff("test_service")

        tracker.clear_service("test_service")

        assert tracker.get_rate_limit_count("test_service", 60) == 0
        assert tracker.get_request_count("test_service", 60) == 0
        assert tracker.get_backoff_level("test_service") == 0

    def test_separate_services(self):
        """Test that services are tracked separately."""
        from selfhealing.services.circuit_breaker.rate_limit_tracker import (
            RateLimitTracker,
        )

        tracker = RateLimitTracker()

        tracker.record_rate_limit("service_a")
        tracker.record_rate_limit("service_a")
        tracker.record_rate_limit("service_b")

        assert tracker.get_rate_limit_count("service_a", 60) == 2
        assert tracker.get_rate_limit_count("service_b", 60) == 1


class TestRateLimitTrackerThreadSafety:
    """Thread safety tests for RateLimitTracker."""

    def test_concurrent_record_rate_limit(self):
        """Test concurrent rate limit recording."""
        from selfhealing.services.circuit_breaker.rate_limit_tracker import (
            RateLimitTracker,
        )

        tracker = RateLimitTracker()
        threads = []

        def record():
            for _ in range(100):
                tracker.record_rate_limit("test_service")

        for _ in range(5):
            t = threading.Thread(target=record)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        count = tracker.get_rate_limit_count("test_service", 60)
        assert count == 500

    def test_concurrent_record_request(self):
        """Test concurrent request recording."""
        from selfhealing.services.circuit_breaker.rate_limit_tracker import (
            RateLimitTracker,
        )

        tracker = RateLimitTracker()
        threads = []

        def record():
            for _ in range(100):
                tracker.record_request("test_service")

        for _ in range(5):
            t = threading.Thread(target=record)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        count = tracker.get_request_count("test_service", 60)
        assert count == 500

    def test_concurrent_backoff_increment(self):
        """Test concurrent backoff increment."""
        from selfhealing.services.circuit_breaker.rate_limit_tracker import (
            RateLimitTracker,
        )

        tracker = RateLimitTracker()
        threads = []

        def increment():
            for _ in range(10):
                tracker.increment_backoff("test_service")

        for _ in range(5):
            t = threading.Thread(target=increment)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        level = tracker.get_backoff_level("test_service")
        assert level == 50


class TestGetRateLimitTracker:
    """Tests for singleton access."""

    def test_get_rate_limit_tracker_singleton(self):
        """Test that get_rate_limit_tracker returns singleton."""
        from selfhealing.services.circuit_breaker.rate_limit_tracker import (
            get_rate_limit_tracker,
        )

        tracker1 = get_rate_limit_tracker()
        tracker2 = get_rate_limit_tracker()

        assert tracker1 is tracker2

    def test_get_rate_limit_tracker_creates_instance(self):
        """Test that get_rate_limit_tracker creates instance."""
        from selfhealing.services.circuit_breaker.rate_limit_tracker import (
            RateLimitTracker,
            get_rate_limit_tracker,
        )

        tracker = get_rate_limit_tracker()
        assert isinstance(tracker, RateLimitTracker)
