"""
Backoff calculation strategies for retry mechanisms.

This module provides various backoff strategies for calculating
delay between retry attempts.
"""

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


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
    _previous_delay: Optional[float] = None

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


def get_backoff_calculator(strategy: str = "exponential", **kwargs) -> BackoffCalculator:
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
        raise ValueError(f"Unknown backoff strategy: {strategy}. " f"Available: {list(strategies.keys())}")

    return strategies[strategy](**kwargs)
