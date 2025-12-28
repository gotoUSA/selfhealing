"""
Base Throttle Implementation.

Framework-agnostic throttle logic with sliding window algorithm.
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Optional, Tuple

from selfhealing.services.throttle.config import ThrottleConfig, ThrottleResult

logger = logging.getLogger(__name__)


class BaseThrottle(ABC):
    """Abstract base class for throttle implementations."""
    
    def __init__(self, config: Optional[ThrottleConfig] = None):
        self.config = config or ThrottleConfig()
        self._current_limit = self.config.initial_limit
    
    @property
    def current_limit(self) -> int:
        """Get current rate limit."""
        return self._current_limit
    
    @current_limit.setter
    def current_limit(self, value: int):
        """Set current rate limit with bounds checking."""
        self._current_limit = max(
            self.config.min_limit,
            min(self.config.max_limit, value)
        )
    
    @abstractmethod
    def check(self, key: str) -> ThrottleResult:
        """
        Check if request is allowed.
        
        Args:
            key: Unique identifier (user_id, ip, etc.)
            
        Returns:
            ThrottleResult with allowed status and metadata
        """
        pass
    
    @abstractmethod
    def reset(self, key: str) -> None:
        """Reset throttle state for a key."""
        pass
    
    def get_stats(self) -> dict:
        """Get throttle statistics."""
        return {
            "current_limit": self._current_limit,
            "min_limit": self.config.min_limit,
            "max_limit": self.config.max_limit,
        }


class SlidingWindowThrottle(BaseThrottle):
    """
    In-memory sliding window throttle.
    
    Thread-safe implementation for single-process use.
    For distributed systems, use Redis-based implementation.
    """
    
    def __init__(self, config: Optional[ThrottleConfig] = None):
        super().__init__(config)
        self._windows: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()
        
        # Statistics
        self._stats = {
            "total_requests": 0,
            "allowed_requests": 0,
            "rejected_requests": 0,
        }
    
    def check(self, key: str) -> ThrottleResult:
        """Check if request is allowed using sliding window."""
        now = time.time()
        window_start = now - self.config.window_seconds
        
        with self._lock:
            # Clean old entries
            self._windows[key] = [
                ts for ts in self._windows[key] if ts > window_start
            ]
            
            current_count = len(self._windows[key])
            self._stats["total_requests"] += 1
            
            if current_count >= self._current_limit:
                self._stats["rejected_requests"] += 1
                return ThrottleResult(
                    allowed=False,
                    current_count=current_count,
                    limit=self._current_limit,
                    remaining=0,
                    reset_at=window_start + self.config.window_seconds,
                    reason="rate_limit_exceeded",
                )
            
            # Add current request
            self._windows[key].append(now)
            self._stats["allowed_requests"] += 1
            
            return ThrottleResult(
                allowed=True,
                current_count=current_count + 1,
                limit=self._current_limit,
                remaining=self._current_limit - current_count - 1,
                reset_at=window_start + self.config.window_seconds,
            )
    
    def reset(self, key: str) -> None:
        """Reset throttle state for a key."""
        with self._lock:
            self._windows.pop(key, None)
    
    def reset_all(self) -> None:
        """Reset all throttle state (for testing)."""
        with self._lock:
            self._windows.clear()
            self._stats = {
                "total_requests": 0,
                "allowed_requests": 0,
                "rejected_requests": 0,
            }
    
    def get_stats(self) -> dict:
        """Get throttle statistics."""
        base_stats = super().get_stats()
        with self._lock:
            return {
                **base_stats,
                **self._stats,
                "active_keys": len(self._windows),
            }
