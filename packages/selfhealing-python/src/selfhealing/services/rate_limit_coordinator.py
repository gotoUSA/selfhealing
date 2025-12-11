"""
Rate Limit Coordinator

Central coordinator for distributed rate limit management.
Prevents Self-DDoS by coordinating retry behavior across all workers.

Key Features:
    - Global cooldown on 429 responses
    - Exponential backoff with jitter
    - Distributed state via pluggable storage
    - 100% coverage with database fallback

Design Philosophy:
    "어떤 고객 환경이든 100% Self-DDoS 차단"
    - Redis 있으면 사용 (최고 성능)
    - 없으면 Database 사용 (100% 호환)
    - DB도 없으면 InMemory (단일 프로세스)

Reference: docs/RATE_LIMIT_COORDINATOR.md
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, TypeVar

from selfhealing.adapters.rate_limit import get_rate_limit_storage
from selfhealing.interfaces.rate_limit_storage import (
    RateLimitState,
    RateLimitStorageInterface,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class RateLimitConfig:
    """Configuration for rate limit coordination."""

    # Backoff settings
    base_delay: float = 1.0  # Base delay in seconds
    max_delay: float = 60.0  # Maximum delay cap
    jitter_percent: float = 30.0  # ±30% random jitter

    # 429 response settings
    default_retry_after: float = 5.0  # Default if no Retry-After header

    # Cooldown multiplier for consecutive 429s
    # delay = min(base_delay * (2 ^ consecutive_429s), max_delay)
    backoff_multiplier: float = 2.0

    @classmethod
    def from_settings(cls) -> "RateLimitConfig":
        """Load configuration from settings."""
        try:
            from django.conf import settings

            self_healing = getattr(settings, "SELF_HEALING", {})
            rate_limit = self_healing.get("RATE_LIMIT", {})

            return cls(
                base_delay=rate_limit.get("BASE_DELAY", 1.0),
                max_delay=rate_limit.get("MAX_DELAY", 60.0),
                jitter_percent=rate_limit.get("JITTER_PERCENT", 30.0),
                default_retry_after=rate_limit.get("DEFAULT_RETRY_AFTER", 5.0),
                backoff_multiplier=rate_limit.get("BACKOFF_MULTIPLIER", 2.0),
            )
        except Exception:
            return cls()


@dataclass
class RateLimitResult:
    """Result of a rate limit check or wait operation."""

    waited: bool = False
    wait_time: float = 0.0
    was_rate_limited: bool = False
    consecutive_429s: int = 0


class RateLimitCoordinator:
    """
    Coordinates rate limiting across distributed workers.

    Prevents Self-DDoS by:
    1. Detecting 429 responses
    2. Setting global cooldown (shared across all workers)
    3. Making all workers wait before retrying
    4. Using exponential backoff with jitter

    Usage:
        coordinator = RateLimitCoordinator()

        # Before making request
        coordinator.wait_if_needed("payment_api")

        # After receiving 429
        coordinator.on_rate_limited(
            key="payment_api",
            retry_after=response.headers.get("Retry-After"),
        )

        # After successful request
        coordinator.on_success("payment_api")

    With decorator:
        @coordinator.rate_limit_aware("payment_api")
        def call_external_api():
            return requests.post(...)
    """

    _instance: Optional["RateLimitCoordinator"] = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        storage: Optional[RateLimitStorageInterface] = None,
        config: Optional[RateLimitConfig] = None,
    ) -> None:
        """
        Initialize rate limit coordinator.

        Args:
            storage: Rate limit storage backend (auto-detected if None)
            config: Rate limit configuration
        """
        self._storage = storage or get_rate_limit_storage()
        self._config = config or RateLimitConfig.from_settings()
        self._local_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "RateLimitCoordinator":
        """Get singleton instance."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton instance (for testing)."""
        with cls._instance_lock:
            cls._instance = None

    @property
    def storage_type(self) -> str:
        """Get the type of storage backend being used."""
        return self._storage.storage_type.value

    def get_state(self, key: str) -> RateLimitState:
        """Get current rate limit state for a key."""
        return self._storage.get_state(key)

    def wait_if_needed(self, key: str) -> RateLimitResult:
        """
        Wait if currently in cooldown period.

        Call this BEFORE making an external request.

        Args:
            key: Rate limit key (e.g., "payment_api", "external_service")

        Returns:
            RateLimitResult with wait information
        """
        state = self._storage.get_state(key)

        if not state.is_in_cooldown:
            return RateLimitResult(
                waited=False,
                wait_time=0.0,
                was_rate_limited=False,
                consecutive_429s=state.consecutive_429s,
            )

        wait_time = state.remaining_cooldown

        logger.info(
            f"[RateLimitCoordinator] Waiting {wait_time:.2f}s for '{key}' " f"(consecutive_429s={state.consecutive_429s})"
        )

        time.sleep(wait_time)

        return RateLimitResult(
            waited=True,
            wait_time=wait_time,
            was_rate_limited=True,
            consecutive_429s=state.consecutive_429s,
        )

    def on_rate_limited(
        self,
        key: str,
        retry_after: Optional[float] = None,
        status_code: int = 429,
    ) -> float:
        """
        Handle a rate limit (429) response.

        Call this when you receive a 429 response.
        Sets a global cooldown for all workers.

        Args:
            key: Rate limit key
            retry_after: Retry-After header value (seconds)
            status_code: HTTP status code (for logging)

        Returns:
            Calculated cooldown duration in seconds
        """
        # Increment consecutive 429 counter
        consecutive = self._storage.increment_consecutive_429s(key)

        # Calculate backoff with exponential increase
        if retry_after is not None and retry_after > 0:
            base_delay = retry_after
        else:
            base_delay = self._config.default_retry_after

        # Exponential backoff: base * (multiplier ^ consecutive)
        delay = base_delay * (self._config.backoff_multiplier ** (consecutive - 1))
        delay = min(delay, self._config.max_delay)

        # Add jitter to prevent thundering herd
        jitter_range = delay * (self._config.jitter_percent / 100.0)
        jitter = random.uniform(-jitter_range, jitter_range)
        delay = max(0.1, delay + jitter)

        # Set global cooldown
        cooldown_until = time.time() + delay
        self._storage.set_cooldown(key, cooldown_until)

        logger.warning(
            f"[RateLimitCoordinator] Rate limited on '{key}' "
            f"(status={status_code}, consecutive={consecutive}, "
            f"cooldown={delay:.2f}s)"
        )

        return delay

    def on_success(self, key: str) -> None:
        """
        Handle a successful response.

        Call this after a successful request to gradually
        reduce the consecutive 429 counter.

        Args:
            key: Rate limit key
        """
        state = self._storage.get_state(key)

        if state.consecutive_429s > 0:
            # Gradual reduction instead of immediate reset
            # Prevents immediate flood after recovery
            self._storage.reset_consecutive_429s(key)

            logger.debug(f"[RateLimitCoordinator] Success on '{key}', " f"reset consecutive 429 counter")

    def clear(self, key: str) -> None:
        """Clear all rate limit state for a key."""
        self._storage.clear(key)
        logger.info(f"[RateLimitCoordinator] Cleared state for '{key}'")

    def rate_limit_aware(
        self,
        key: str,
        is_429: Optional[Callable[[Any], bool]] = None,
        get_retry_after: Optional[Callable[[Any], Optional[float]]] = None,
    ) -> Callable[[Callable[..., T]], Callable[..., T]]:
        """
        Decorator to make a function rate-limit aware.

        Args:
            key: Rate limit key
            is_429: Function to detect if response is 429 (default: check status_code)
            get_retry_after: Function to extract Retry-After from response

        Returns:
            Decorated function

        Example:
            @coordinator.rate_limit_aware("payment_api")
            def call_payment_api():
                return requests.post(...)

            @coordinator.rate_limit_aware(
                "external_api",
                is_429=lambda r: r.status_code == 429,
                get_retry_after=lambda r: float(r.headers.get("Retry-After", 5)),
            )
            def call_external_api():
                return requests.get(...)
        """

        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            def wrapper(*args: Any, **kwargs: Any) -> T:
                # Wait if in cooldown
                self.wait_if_needed(key)

                # Call function
                result = func(*args, **kwargs)

                # Check if rate limited
                _is_429 = is_429 or _default_is_429
                _get_retry_after = get_retry_after or _default_get_retry_after

                if _is_429(result):
                    retry_after = _get_retry_after(result)
                    self.on_rate_limited(key, retry_after)
                else:
                    self.on_success(key)

                return result

            return wrapper

        return decorator


def _default_is_429(response: Any) -> bool:
    """Default 429 detection."""
    if hasattr(response, "status_code"):
        return response.status_code == 429
    return False


def _default_get_retry_after(response: Any) -> Optional[float]:
    """Default Retry-After extraction."""
    if hasattr(response, "headers"):
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
    return None


# Convenience function
def get_rate_limit_coordinator() -> RateLimitCoordinator:
    """Get the global rate limit coordinator instance."""
    return RateLimitCoordinator.get_instance()
