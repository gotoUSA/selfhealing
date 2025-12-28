"""
Throttle Services for Self-Healing System.

Provides framework-agnostic throttling with:
- Base throttle logic (sliding window, token bucket)
- Netflix Gradient Adaptive Throttling
- DRF adapter for Django integration

Usage:
    from selfhealing.services.throttle import AdaptiveThrottle, ThrottleConfig
    
    throttle = AdaptiveThrottle(
        config=ThrottleConfig(
            initial_limit=100,
            min_limit=10,
            max_limit=500,
        )
    )
    
    # Check if request is allowed
    allowed, info = throttle.check("user_123")
    
    # Record response time for gradient calculation
    throttle.record_response(response_time_ms=45.2)
"""

from selfhealing.services.throttle.config import ThrottleConfig
from selfhealing.services.throttle.base import BaseThrottle, SlidingWindowThrottle
from selfhealing.services.throttle.adaptive import (
    AdaptiveThrottle,
    GradientCalculator,
    get_adaptive_throttle,
    reset_adaptive_throttle,
)

__all__ = [
    "ThrottleConfig",
    "BaseThrottle",
    "SlidingWindowThrottle",
    "AdaptiveThrottle",
    "GradientCalculator",
    "get_adaptive_throttle",
    "reset_adaptive_throttle",
]
