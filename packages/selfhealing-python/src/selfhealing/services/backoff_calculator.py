"""
Exponential Backoff Calculator

Provides configurable exponential backoff with jitter for retry logic.

Features:
- Exponential backoff: base^attempt (4, 16, 64, ...)
- Maximum delay cap to prevent excessive wait times
- Jitter (±25%) to prevent thundering herd problem
- Per-domain configuration support
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from selfhealing.settings import get_config

if TYPE_CHECKING:
    pass


@dataclass
class BackoffConfig:
    """Configuration for exponential backoff calculation."""

    base: int = 4  # Base for exponential (4^n seconds)
    max_delay: int = 180  # Maximum wait time (3 minutes)
    jitter_percent: int = 25  # ±25% random jitter
    min_delay: int = 1  # Minimum delay in seconds

    @classmethod
    def from_settings(cls, domain: str | None = None) -> "BackoffConfig":
        """
        Load configuration from core config.

        Args:
            domain: Optional domain for per-domain overrides

        Returns:
            BackoffConfig with merged settings
        """
        retry_settings = get_config().retry

        # Default values from centralized config
        config = cls(
            base=retry_settings.backoff_base,
            max_delay=int(retry_settings.max_delay),
            jitter_percent=retry_settings.jitter_percent,
            min_delay=retry_settings.min_delay,
        )

        # Apply per-domain overrides if available
        if domain:
            # Get domain config from centralized config
            full_config = get_config()
            domain_configs = getattr(full_config, "domain_configs", {})
            domain_config = domain_configs.get(domain, {})
            if "backoff_base" in domain_config:
                config.base = domain_config["backoff_base"]

        return config


class BackoffCalculator:
    """
    Calculates exponential backoff delays with jitter.

    The formula is:
        delay = min(base^attempt, max_delay) * (1 ± jitter_percent/100)

    Example with default settings (base=4, max=180, jitter=25%):
        - Attempt 1: 4s (±1s jitter) → 3-5s
        - Attempt 2: 16s (±4s jitter) → 12-20s
        - Attempt 3: 64s (±16s jitter) → 48-80s
        - Attempt 4+: 180s (capped at max)
    """

    def __init__(self, config: BackoffConfig | None = None):
        """
        Initialize the calculator.

        Args:
            config: BackoffConfig instance, or None to load from settings
        """
        self.config = config or BackoffConfig.from_settings()

    def calculate(self, attempt: int, with_jitter: bool = True) -> int:
        """
        Calculate backoff delay for a given attempt.

        Args:
            attempt: The attempt number (1-based)
            with_jitter: Whether to apply jitter

        Returns:
            Delay in seconds (integer)
        """
        if attempt < 1:
            return self.config.min_delay

        # Exponential backoff: base^attempt
        delay = self.config.base**attempt

        # Cap at maximum delay
        delay = min(delay, self.config.max_delay)

        # Apply jitter if enabled
        if with_jitter and self.config.jitter_percent > 0:
            jitter_factor = self.config.jitter_percent / 100.0
            # Random value between -jitter_factor and +jitter_factor
            jitter = delay * jitter_factor * (random.random() * 2 - 1)
            delay = int(delay + jitter)

        # Ensure minimum delay
        return max(self.config.min_delay, delay)

    def get_delays_sequence(self, max_attempts: int, with_jitter: bool = False) -> list[int]:
        """
        Get the sequence of delays for multiple attempts.

        Args:
            max_attempts: Number of attempts to calculate
            with_jitter: Whether to apply jitter

        Returns:
            List of delay values in seconds
        """
        return [self.calculate(attempt, with_jitter) for attempt in range(1, max_attempts + 1)]


def calculate_backoff(
    attempt: int,
    base: int = 4,
    max_delay: int = 180,
    jitter_percent: int = 25,
) -> int:
    """
    Convenience function to calculate backoff delay.

    Args:
        attempt: The attempt number (1-based)
        base: Base for exponential calculation
        max_delay: Maximum delay in seconds
        jitter_percent: Jitter percentage (0-100)

    Returns:
        Delay in seconds

    Example:
        >>> calculate_backoff(1)  # First retry
        4  # (approximately, with jitter)
        >>> calculate_backoff(2)  # Second retry
        16  # (approximately, with jitter)
    """
    config = BackoffConfig(
        base=base,
        max_delay=max_delay,
        jitter_percent=jitter_percent,
    )
    calculator = BackoffCalculator(config)
    return calculator.calculate(attempt)


# Domain-specific calculator instances
_calculators: dict[str, BackoffCalculator] = {}


def get_calculator_for_domain(domain: str) -> BackoffCalculator:
    """
    Get a BackoffCalculator configured for a specific domain.

    Caches calculator instances per domain for efficiency.

    Args:
        domain: Domain name (payment, webhook, notification, etc.)

    Returns:
        BackoffCalculator configured for the domain
    """
    if domain not in _calculators:
        config = BackoffConfig.from_settings(domain)
        _calculators[domain] = BackoffCalculator(config)
    return _calculators[domain]
