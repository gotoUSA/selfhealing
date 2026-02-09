"""
Retry Handler Models

Data classes, enums, and exceptions for retry handling.

RetryAction(Enum), MaxRetriesExceededError(Exception),
RetryConfig(dataclass), RetryResult(dataclass), T TypeVar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

from selfhealing.settings import get_config

T = TypeVar("T")


class RetryAction(str, Enum):
    """Actions that can be taken after a failure."""

    RETRY = "retry"
    DLQ = "dlq"
    ABORT = "abort"
    SUCCESS = "success"


class MaxRetriesExceededError(Exception):
    """Raised when maximum retry attempts have been exhausted."""

    def __init__(
        self,
        message: str,
        retry_count: int,
        max_retries: int,
        last_error: Exception | None = None,
    ):
        super().__init__(message)
        self.retry_count = retry_count
        self.max_retries = max_retries
        self.last_error = last_error


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""

    max_attempts: int = 3
    backoff_base: int = 4
    backoff_max: int = 180
    jitter_percent: int = 25
    retryable_exceptions: tuple[type[Exception], ...] = field(default_factory=lambda: (Exception,))
    non_retryable_exceptions: tuple[type[Exception], ...] = field(default_factory=tuple)
    enable_dlq: bool = True
    domain: str = "default"

    # Rate limit awareness settings
    rate_limit_aware: bool = True  # Enable Self-DDoS prevention
    rate_limit_key: str | None = None  # Custom key, defaults to domain

    # Throttle awareness settings (v2.0)
    throttle_aware: bool = True  # Enable Throttle-aware backoff
    throttle_backoff_multiplier_cap: float = 4.0  # Maximum multiplier cap

    # Critical tier settings (v2.0)
    critical_tier_full_stop_grace_retries: int = 1
    """CRITICAL 티어 요청은 FULL_STOP에서도 추가 재시도 허용 횟수"""

    critical_tier_full_stop_max_delay: int = 720
    """CRITICAL 티어 FULL_STOP 시 최대 대기 시간 (12분)"""

    @classmethod
    def from_settings(cls, domain: str = "default") -> RetryConfig:
        """
        Load configuration from RuntimeConfigManager (preferred) or core config.

        Args:
            domain: Domain name for per-domain overrides

        Returns:
            RetryConfig instance
        """
        # Try RuntimeConfigManager first (runtime-configurable)
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()
            retry_config = manager.get_retry_config()
            dlq_config = manager.get_dlq_config()

            return cls(
                max_attempts=retry_config.get("max_attempts", 3),
                backoff_base=retry_config.get("backoff_base", 4),
                backoff_max=int(retry_config.get("max_delay", 180)),
                jitter_percent=retry_config.get("jitter_percent", 25),
                enable_dlq=dlq_config.get("enabled", True),
                domain=domain,
            )
        except Exception:
            pass  # Fall through to static config

        # Fallback to static core config
        config = get_config()
        retry_settings = config.retry
        dlq_settings = config.dlq

        # Per-domain overrides from domain_configs
        domain_config = config.domain_configs.get(domain, {}).get("retry", {})

        return cls(
            max_attempts=domain_config.get("max_attempts", retry_settings.max_attempts),
            backoff_base=domain_config.get("backoff_base", retry_settings.backoff_base),
            backoff_max=domain_config.get("max_delay", retry_settings.max_delay),
            jitter_percent=retry_settings.jitter_percent,
            enable_dlq=dlq_settings.enabled,
            domain=domain,
        )


@dataclass
class RetryResult:
    """Result of a retry operation."""

    success: bool
    action: RetryAction
    attempt: int
    value: Any = None
    error: Exception | None = None
    dlq_id: int | None = None
    next_delay: int | None = None

    @property
    def should_retry(self) -> bool:
        """Whether another retry should be attempted."""
        return self.action == RetryAction.RETRY

    @property
    def was_retried(self) -> bool:
        """Whether this result came from a retry (not first attempt)."""
        return self.attempt > 1
