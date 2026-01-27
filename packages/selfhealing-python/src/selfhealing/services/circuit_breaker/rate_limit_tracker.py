"""
Rate Limit Tracker

Thread-safe tracker for rate limit events to detect rate limit cascades
and self-DDoS situations.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict


class RateLimitTracker:
    """
    Thread-safe tracker for rate limit events.

    Used to detect rate limit cascades and self-DDoS situations.
    Tracks 429 responses and request rates per service.
    """

    def __init__(self):
        self._lock = threading.Lock()
        # {service_name: [timestamp, ...]} for rate limit hits
        self._rate_limit_events: dict[str, list[float]] = defaultdict(list)
        # {service_name: [timestamp, ...]} for all requests
        self._request_events: dict[str, list[float]] = defaultdict(list)
        # {service_name: backoff_level} for adaptive backoff
        self._backoff_levels: dict[str, int] = defaultdict(int)

    def record_rate_limit(self, service_name: str) -> None:
        """Record a 429 rate limit response."""
        with self._lock:
            self._rate_limit_events[service_name].append(time.time())

    def record_request(self, service_name: str) -> None:
        """Record a request attempt."""
        with self._lock:
            self._request_events[service_name].append(time.time())

    def get_rate_limit_count(self, service_name: str, window_seconds: int) -> int:
        """Get the number of rate limits in the time window."""
        cutoff = time.time() - window_seconds
        with self._lock:
            # Clean old entries
            self._rate_limit_events[service_name] = [
                t for t in self._rate_limit_events[service_name] if t > cutoff
            ]
            return len(self._rate_limit_events[service_name])

    def get_request_count(self, service_name: str, window_seconds: int) -> int:
        """Get the number of requests in the time window."""
        cutoff = time.time() - window_seconds
        with self._lock:
            # Clean old entries
            self._request_events[service_name] = [
                t for t in self._request_events[service_name] if t > cutoff
            ]
            return len(self._request_events[service_name])

    def get_backoff_level(self, service_name: str) -> int:
        """Get current backoff level for a service."""
        with self._lock:
            return self._backoff_levels[service_name]

    def increment_backoff(self, service_name: str) -> int:
        """Increment and return the new backoff level."""
        with self._lock:
            self._backoff_levels[service_name] += 1
            return self._backoff_levels[service_name]

    def reset_backoff(self, service_name: str) -> None:
        """Reset backoff level to zero."""
        with self._lock:
            self._backoff_levels[service_name] = 0

    def clear_service(self, service_name: str) -> None:
        """Clear all tracking data for a service."""
        with self._lock:
            self._rate_limit_events[service_name].clear()
            self._request_events[service_name].clear()
            self._backoff_levels[service_name] = 0


# Global rate limit tracker instance
_rate_limit_tracker: RateLimitTracker | None = None


def get_rate_limit_tracker() -> RateLimitTracker:
    """Get the singleton rate limit tracker instance."""
    global _rate_limit_tracker
    if _rate_limit_tracker is None:
        _rate_limit_tracker = RateLimitTracker()
    return _rate_limit_tracker
