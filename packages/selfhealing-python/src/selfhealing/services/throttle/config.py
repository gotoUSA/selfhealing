"""
Throttle Configuration.

Provides dataclass-based configuration for throttle services.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from selfhealing.settings import get_throttle_settings


@dataclass
class ThrottleConfig:
    """Configuration for throttle services."""

    # Basic rate limiting
    initial_limit: int = field(default_factory=lambda: get_throttle_settings().initial_limit)
    window_seconds: int = field(default_factory=lambda: get_throttle_settings().window_seconds)

    # Adaptive throttling (Netflix Gradient)
    min_limit: int = field(default_factory=lambda: get_throttle_settings().min_limit)
    max_limit: int = field(default_factory=lambda: get_throttle_settings().max_limit)

    # Gradient calculation
    sample_interval_ms: int = field(default_factory=lambda: get_throttle_settings().sample_interval_ms)
    smoothing_factor: float = field(default_factory=lambda: get_throttle_settings().smoothing_factor)

    # Adjustment rates
    decrease_ratio: float = field(default_factory=lambda: get_throttle_settings().decrease_ratio)
    increase_step: int = field(default_factory=lambda: get_throttle_settings().increase_step)

    # SLA thresholds (ms) - trigger aggressive throttling
    sla_warning_ms: int = field(default_factory=lambda: get_throttle_settings().sla_warning_ms)
    sla_critical_ms: int = field(default_factory=lambda: get_throttle_settings().sla_critical_ms)

    # Emergency mode
    emergency_limit: int = field(default_factory=lambda: get_throttle_settings().emergency_limit)

    # Redis key prefix
    key_prefix: str = field(default_factory=lambda: get_throttle_settings().key_prefix)

    # Prometheus metrics service label
    service_name: str = field(default_factory=lambda: get_throttle_settings().service_name)

    @classmethod
    def from_settings(cls) -> ThrottleConfig:
        """Create config from settings."""
        settings = get_throttle_settings()
        return cls(
            initial_limit=settings.initial_limit,
            window_seconds=settings.window_seconds,
            min_limit=settings.min_limit,
            max_limit=settings.max_limit,
            sample_interval_ms=settings.sample_interval_ms,
            smoothing_factor=settings.smoothing_factor,
            decrease_ratio=settings.decrease_ratio,
            increase_step=settings.increase_step,
            sla_warning_ms=settings.sla_warning_ms,
            sla_critical_ms=settings.sla_critical_ms,
            emergency_limit=settings.emergency_limit,
            key_prefix=settings.key_prefix,
            service_name=settings.service_name,
        )

    @classmethod
    def from_dict(cls, data: dict) -> ThrottleConfig:
        """Create config from dictionary."""
        settings = get_throttle_settings()
        return cls(
            initial_limit=data.get("initial_limit", settings.initial_limit),
            window_seconds=data.get("window_seconds", settings.window_seconds),
            min_limit=data.get("min_limit", settings.min_limit),
            max_limit=data.get("max_limit", settings.max_limit),
            sample_interval_ms=data.get("sample_interval_ms", settings.sample_interval_ms),
            smoothing_factor=data.get("smoothing_factor", settings.smoothing_factor),
            decrease_ratio=data.get("decrease_ratio", settings.decrease_ratio),
            increase_step=data.get("increase_step", settings.increase_step),
            sla_warning_ms=data.get("sla_warning_ms", settings.sla_warning_ms),
            sla_critical_ms=data.get("sla_critical_ms", settings.sla_critical_ms),
            emergency_limit=data.get("emergency_limit", settings.emergency_limit),
            key_prefix=data.get("key_prefix", settings.key_prefix),
            service_name=data.get("service_name", settings.service_name),
        )


@dataclass
class ThrottleResult:
    """Result of throttle check."""

    allowed: bool
    current_count: int
    limit: int
    remaining: int
    reset_at: float  # Unix timestamp
    reason: str | None = None

    # Adaptive info
    current_rtt_ms: float | None = None
    rtt_gradient: float | None = None

    def to_headers(self) -> dict:
        """Convert to rate limit response headers."""
        return {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(max(0, self.remaining)),
            "X-RateLimit-Reset": str(int(self.reset_at)),
        }
