"""
Backoff calculation strategies for retry mechanisms.

This module provides various backoff strategies for calculating
delay between retry attempts.
"""

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass


class BackoffCalculator(ABC):
    """Abstract base class for backoff calculation strategies."""

    @abstractmethod
    def calculate(self, attempt: int) -> float:
        """
        Calculate the delay for the given attempt number.

        Args:
            attempt: The current attempt number (1-indexed)

        Returns:
            The delay in seconds before the next retry
        """
        pass

    @abstractmethod
    def reset(self) -> None:
        """Reset the backoff calculator to its initial state."""
        pass


@dataclass
class ExponentialBackoff(BackoffCalculator):
    """
    Exponential backoff strategy.

    Delay grows exponentially with each attempt: base_delay * (multiplier ^ attempt)
    Optional jitter adds randomness to prevent thundering herd.
    """

    base_delay: float = 1.0
    max_delay: float = 300.0
    multiplier: float = 2.0
    jitter: bool = True
    jitter_factor: float = 0.2

    @classmethod
    def from_settings(cls, settings=None, **overrides) -> "ExponentialBackoff":
        """
        Settings 기반 인스턴스 생성.

        Args:
            settings: BackoffSettings 인스턴스 (None이면 자동 로드)
            **overrides: 개별 필드 오버라이드

        Returns:
            ExponentialBackoff: Settings 기반 인스턴스
        """
        from selfhealing.settings.backoff import get_backoff_settings

        s = settings or get_backoff_settings()
        return cls(
            base_delay=overrides.get("base_delay", s.exponential_base_delay),
            max_delay=overrides.get("max_delay", s.exponential_max_delay),
            multiplier=overrides.get("multiplier", s.exponential_multiplier),
            jitter=overrides.get("jitter", True),
            jitter_factor=overrides.get("jitter_factor", s.exponential_jitter_factor),
        )

    def calculate(self, attempt: int) -> float:
        """Calculate exponential delay with optional jitter."""
        delay = self.base_delay * (self.multiplier ** (attempt - 1))
        delay = min(delay, self.max_delay)

        if self.jitter:
            jitter_range = delay * self.jitter_factor
            delay = delay + random.uniform(-jitter_range, jitter_range)
            delay = max(0.0, delay)

        return delay

    def reset(self) -> None:
        """Reset is a no-op for stateless exponential backoff."""
        pass


@dataclass
class LinearBackoff(BackoffCalculator):
    """
    Linear backoff strategy.

    Delay grows linearly with each attempt: base_delay + (increment * attempt)
    """

    base_delay: float = 1.0
    increment: float = 1.0
    max_delay: float = 60.0
    jitter: bool = False
    jitter_factor: float = 0.1

    @classmethod
    def from_settings(cls, settings=None, **overrides) -> "LinearBackoff":
        """
        Settings 기반 인스턴스 생성.

        Args:
            settings: BackoffSettings 인스턴스 (None이면 자동 로드)
            **overrides: 개별 필드 오버라이드

        Returns:
            LinearBackoff: Settings 기반 인스턴스
        """
        from selfhealing.settings.backoff import get_backoff_settings

        s = settings or get_backoff_settings()
        return cls(
            base_delay=overrides.get("base_delay", s.linear_base_delay),
            increment=overrides.get("increment", s.linear_increment),
            max_delay=overrides.get("max_delay", s.linear_max_delay),
            jitter=overrides.get("jitter", False),
            jitter_factor=overrides.get("jitter_factor", s.linear_jitter_factor),
        )

    def calculate(self, attempt: int) -> float:
        """Calculate linear delay."""
        delay = self.base_delay + (self.increment * (attempt - 1))
        delay = min(delay, self.max_delay)

        if self.jitter:
            jitter_range = delay * self.jitter_factor
            delay = delay + random.uniform(-jitter_range, jitter_range)
            delay = max(0.0, delay)

        return delay

    def reset(self) -> None:
        """Reset is a no-op for stateless linear backoff."""
        pass


@dataclass
class ConstantBackoff(BackoffCalculator):
    """
    Constant backoff strategy.

    Delay is constant regardless of attempt number.
    """

    delay: float = 5.0
    jitter: bool = False
    jitter_factor: float = 0.1

    @classmethod
    def from_settings(cls, settings=None, **overrides) -> "ConstantBackoff":
        """
        Settings 기반 인스턴스 생성.

        Args:
            settings: BackoffSettings 인스턴스 (None이면 자동 로드)
            **overrides: 개별 필드 오버라이드

        Returns:
            ConstantBackoff: Settings 기반 인스턴스
        """
        from selfhealing.settings.backoff import get_backoff_settings

        s = settings or get_backoff_settings()
        return cls(
            delay=overrides.get("delay", s.constant_delay),
            jitter=overrides.get("jitter", False),
            jitter_factor=overrides.get("jitter_factor", s.constant_jitter_factor),
        )

    def calculate(self, attempt: int) -> float:
        """Return constant delay."""
        result = self.delay

        if self.jitter:
            jitter_range = result * self.jitter_factor
            result = result + random.uniform(-jitter_range, jitter_range)
            result = max(0.0, result)

        return result

    def reset(self) -> None:
        """Reset is a no-op for constant backoff."""
        pass


@dataclass
class DecorrelatedJitterBackoff(BackoffCalculator):
    """
    Decorrelated jitter backoff strategy (AWS-style).

    Each delay is randomly chosen between base_delay and 3 * previous_delay.
    This provides better distribution than simple exponential with jitter.
    """

    base_delay: float = 1.0
    max_delay: float = 300.0
    _previous_delay: float | None = None

    @classmethod
    def from_settings(cls, settings=None, **overrides) -> "DecorrelatedJitterBackoff":
        """
        Settings 기반 인스턴스 생성.

        Args:
            settings: BackoffSettings 인스턴스 (None이면 자동 로드)
            **overrides: 개별 필드 오버라이드

        Returns:
            DecorrelatedJitterBackoff: Settings 기반 인스턴스
        """
        from selfhealing.settings.backoff import get_backoff_settings

        s = settings or get_backoff_settings()
        return cls(
            base_delay=overrides.get("base_delay", s.decorrelated_base_delay),
            max_delay=overrides.get("max_delay", s.decorrelated_max_delay),
        )

    def calculate(self, attempt: int) -> float:
        """Calculate decorrelated jitter delay."""
        if self._previous_delay is None or attempt == 1:
            delay = self.base_delay
        else:
            delay = random.uniform(self.base_delay, self._previous_delay * 3)

        delay = min(delay, self.max_delay)
        self._previous_delay = delay
        return delay

    def reset(self) -> None:
        """Reset the previous delay tracking."""
        self._previous_delay = None


def get_backoff_calculator(
    strategy: str = "exponential", **kwargs
) -> BackoffCalculator:
    """
    Factory function to create a backoff calculator.

    Args:
        strategy: One of 'exponential', 'linear', 'constant', 'decorrelated'
        **kwargs: Strategy-specific parameters

    Returns:
        A BackoffCalculator instance

    Raises:
        ValueError: If an unknown strategy is specified
    """
    strategies = {
        "exponential": ExponentialBackoff,
        "linear": LinearBackoff,
        "constant": ConstantBackoff,
        "decorrelated": DecorrelatedJitterBackoff,
    }

    if strategy not in strategies:
        raise ValueError(
            f"Unknown backoff strategy: {strategy}. "
            f"Available: {list(strategies.keys())}"
        )

    return strategies[strategy](**kwargs)


# =============================================================================
# Legacy compatible classes (for migration from older projects)
# =============================================================================


@dataclass
class BackoffConfig:
    """
    Configuration for exponential backoff calculation.

    Compatible with older project BackoffConfig for migration.
    """

    base: int = 4  # Base for exponential (4^n seconds)
    max_delay: int = 180  # Maximum wait time (3 minutes)
    jitter_percent: int = 25  # ±25% random jitter
    min_delay: int = 1  # Minimum delay in seconds

    @classmethod
    def from_settings(cls, settings=None, **overrides) -> "BackoffConfig":
        """
        Settings 기반 인스턴스 생성.

        Args:
            settings: BackoffSettings 인스턴스 (None이면 자동 로드)
            **overrides: 개별 필드 오버라이드

        Returns:
            BackoffConfig: Settings 기반 인스턴스
        """
        from selfhealing.settings.backoff import get_backoff_settings

        s = settings or get_backoff_settings()
        return cls(
            base=overrides.get("base", s.legacy_base),
            max_delay=overrides.get("max_delay", s.legacy_max_delay),
            jitter_percent=overrides.get("jitter_percent", s.legacy_jitter_percent),
            min_delay=overrides.get("min_delay", s.legacy_min_delay),
        )


class LegacyBackoffCalculator:
    """
    Legacy backoff calculator for migration compatibility.

    Uses BackoffConfig for configuration.
    """

    def __init__(self, config: BackoffConfig | None = None):
        """
        Initialize the calculator.

        Args:
            config: BackoffConfig instance
        """
        self.config = config or BackoffConfig()

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

    def get_delays_sequence(self, max_attempts: int, with_jitter: bool = False) -> list:
        """
        Get the sequence of delays for multiple attempts.

        Args:
            max_attempts: Number of attempts to calculate
            with_jitter: Whether to apply jitter

        Returns:
            List of delay values in seconds
        """
        return [
            self.calculate(attempt, with_jitter)
            for attempt in range(1, max_attempts + 1)
        ]


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
    """
    config = BackoffConfig(
        base=base,
        max_delay=max_delay,
        jitter_percent=jitter_percent,
    )
    calculator = LegacyBackoffCalculator(config)
    return calculator.calculate(attempt)
