"""
Throttle Configuration.

Provides dataclass-based configuration for throttle services.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ThrottleConfig:
    """Configuration for throttle services."""
    
    # Basic rate limiting
    initial_limit: int = 100  # requests per window
    window_seconds: int = 60  # window size
    
    # Adaptive throttling (Netflix Gradient)
    min_limit: int = 10  # minimum limit (never go below)
    max_limit: int = 500  # maximum limit (never exceed)
    
    # Gradient calculation
    sample_interval_ms: int = 500  # RTT sampling interval
    smoothing_factor: float = 0.5  # exponential smoothing (0-1)
    
    # Adjustment rates
    decrease_ratio: float = 0.9  # multiply by this when RTT increasing
    increase_step: int = 1  # add this when RTT decreasing
    
    # SLA thresholds (ms) - trigger aggressive throttling
    sla_warning_ms: int = 200  # start throttling at this RTT
    sla_critical_ms: int = 500  # aggressive throttling at this RTT
    
    # Emergency mode
    emergency_limit: int = 10  # requests per window in emergency
    
    # Redis key prefix
    key_prefix: str = "selfhealing:throttle"
    
    @classmethod
    def from_dict(cls, data: dict) -> "ThrottleConfig":
        """Create config from dictionary."""
        return cls(
            initial_limit=data.get("initial_limit", 100),
            window_seconds=data.get("window_seconds", 60),
            min_limit=data.get("min_limit", 10),
            max_limit=data.get("max_limit", 500),
            sample_interval_ms=data.get("sample_interval_ms", 500),
            smoothing_factor=data.get("smoothing_factor", 0.5),
            decrease_ratio=data.get("decrease_ratio", 0.9),
            increase_step=data.get("increase_step", 1),
            sla_warning_ms=data.get("sla_warning_ms", 200),
            sla_critical_ms=data.get("sla_critical_ms", 500),
            emergency_limit=data.get("emergency_limit", 10),
            key_prefix=data.get("key_prefix", "selfhealing:throttle"),
        )


@dataclass
class ThrottleResult:
    """Result of throttle check."""
    
    allowed: bool
    current_count: int
    limit: int
    remaining: int
    reset_at: float  # Unix timestamp
    reason: Optional[str] = None
    
    # Adaptive info
    current_rtt_ms: Optional[float] = None
    rtt_gradient: Optional[float] = None
    
    def to_headers(self) -> dict:
        """Convert to rate limit response headers."""
        return {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(max(0, self.remaining)),
            "X-RateLimit-Reset": str(int(self.reset_at)),
        }
