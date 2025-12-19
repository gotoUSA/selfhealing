"""
Retry Handler with Exponential Backoff

Provides a reusable retry mechanism with:
- Configurable max attempts
- Exponential backoff with jitter
- Idempotency checking
- DLQ routing on exhaustion
- Forensic context capture

Reference: docs/L3_SELF_HEALING_ARCHITECTURE.md §7, §8
"""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, TypeVar

from django.conf import settings
from django.utils import timezone

from .backoff_calculator import BackoffCalculator, BackoffConfig

if TYPE_CHECKING:
    from shopping.models.failed_operation import FailedOperation

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RetryAction(Enum):
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

    @classmethod
    def from_settings(cls, domain: str = "default") -> "RetryConfig":
        """
        Load configuration from Django settings via centralized config.

        Args:
            domain: Domain name for per-domain overrides

        Returns:
            RetryConfig instance
        """
        from shopping.services.self_healing.config import get_retry_settings, get_dlq_settings

        retry_settings = get_retry_settings()
        dlq_settings = get_dlq_settings()

        # Per-domain overrides
        self_healing = getattr(settings, "SELF_HEALING", {})
        domain_config = self_healing.get("DOMAIN_CONFIG", {}).get(domain, {})

        return cls(
            max_attempts=domain_config.get("max_attempts", retry_settings.max_attempts),
            backoff_base=domain_config.get("backoff_base", retry_settings.backoff_base),
            backoff_max=domain_config.get("backoff_max", retry_settings.backoff_max),
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


class RetryHandler:
    """
    Handles retry logic with exponential backoff.

    Usage:
        handler = RetryHandler(domain="payment")
        result = handler.execute(my_function, arg1, arg2, kwarg=value)

        if result.success:
            print(f"Success after {result.attempt} attempts")
        else:
            print(f"Failed: {result.error}, DLQ ID: {result.dlq_id}")
    """

    def __init__(
        self,
        config: RetryConfig | None = None,
        domain: str = "default",
    ):
        """
        Initialize the retry handler.

        Args:
            config: RetryConfig instance, or None to load from settings
            domain: Domain for per-domain configuration
        """
        self.config = config or RetryConfig.from_settings(domain)
        self.backoff = BackoffCalculator(
            BackoffConfig(
                base=self.config.backoff_base,
                max_delay=self.config.backoff_max,
                jitter_percent=self.config.jitter_percent,
            )
        )

    def should_retry(self, exception: Exception, attempt: int) -> bool:
        """
        Determine if an exception should trigger a retry.

        Args:
            exception: The exception that occurred
            attempt: Current attempt number

        Returns:
            True if should retry, False otherwise
        """
        # Check if max attempts reached
        if attempt >= self.config.max_attempts:
            return False

        # Check non-retryable exceptions first
        if isinstance(exception, self.config.non_retryable_exceptions):
            return False

        # Check if exception is retryable
        if isinstance(exception, self.config.retryable_exceptions):
            return True

        return False

    def get_next_delay(self, attempt: int) -> int:
        """
        Get the delay before the next retry attempt.

        Args:
            attempt: Current attempt number

        Returns:
            Delay in seconds
        """
        return self.backoff.calculate(attempt)

    def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        context: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> RetryResult:
        """
        Execute a function with retry logic.

        Note: This is a synchronous implementation. For async tasks,
        use the Celery-based retry mechanism.

        Args:
            func: Function to execute
            *args: Positional arguments for the function
            context: Optional context for forensic logging
            **kwargs: Keyword arguments for the function

        Returns:
            RetryResult with the outcome
        """
        attempt = 0
        last_error: Exception | None = None
        retry_history: list[dict[str, Any]] = []
        func_name = getattr(func, "__name__", str(func))

        while attempt < self.config.max_attempts:
            attempt += 1

            # 행동 직전 로깅 - 실제 실행 전에 기록
            logger.info(
                f"[RetryHandler] EXECUTING domain={self.config.domain}, "
                f"func={func_name}, attempt={attempt}/{self.config.max_attempts}"
            )

            try:
                result = func(*args, **kwargs)
                # 성공 로깅
                logger.info(
                    f"[RetryHandler] SUCCESS domain={self.config.domain}, "
                    f"func={func_name}, attempt={attempt}/{self.config.max_attempts}"
                )
                return RetryResult(
                    success=True,
                    action=RetryAction.SUCCESS,
                    attempt=attempt,
                    value=result,
                )

            except Exception as e:
                last_error = e
                retry_history.append(
                    {
                        "attempt": attempt,
                        "error_type": type(e).__name__,
                        "error_message": str(e)[:500],
                        "timestamp": timezone.now().isoformat(),
                    }
                )

                logger.warning(f"[RetryHandler] Attempt {attempt}/{self.config.max_attempts} failed: {e}")

                if self.should_retry(e, attempt):
                    delay = self.get_next_delay(attempt)
                    logger.info(
                        f"[RetryHandler] Will retry in {delay}s " f"(attempt {attempt + 1}/{self.config.max_attempts})"
                    )
                    # For synchronous execution, we don't actually sleep
                    # The caller (usually Celery) handles the delay
                    continue
                else:
                    break

        # Max retries exceeded or non-retryable error
        logger.error(
            f"[RetryHandler] Max retries exceeded ({attempt}/{self.config.max_attempts}), " f"last error: {last_error}"
        )

        # Move to DLQ if enabled
        dlq_id = None
        if self.config.enable_dlq:
            dlq_id = self._move_to_dlq(
                last_error=last_error,
                attempt=attempt,
                context=context,
                retry_history=retry_history,
            )

        return RetryResult(
            success=False,
            action=RetryAction.DLQ if dlq_id else RetryAction.ABORT,
            attempt=attempt,
            error=last_error,
            dlq_id=dlq_id,
        )

    def _move_to_dlq(
        self,
        last_error: Exception | None,
        attempt: int,
        context: dict[str, Any] | None,
        retry_history: list[dict[str, Any]],
    ) -> int | None:
        """
        Move the failed operation to the Dead Letter Queue.

        Args:
            last_error: The last exception that occurred
            attempt: Final attempt number
            context: Additional context for forensic logging
            retry_history: History of all retry attempts

        Returns:
            DLQ record ID or None if DLQ is disabled
        """
        from shopping.models.failed_operation import FailedOperation

        try:
            context = context or {}
            error_type = type(last_error).__name__ if last_error else "Unknown"

            failed_op = FailedOperation.create_from_failure(
                domain=self.config.domain,
                failure_type=f"MAX_RETRIES_{error_type.upper()}",
                order=context.get("order"),
                payment=context.get("payment"),
                user=context.get("user"),
                error_code=error_type,
                error_message=str(last_error)[:1000] if last_error else "",
                snapshot_data=context.get("snapshot_data", {}),
                request_data=context.get("request_data", {}),
                response_data=context.get("response_data", {}),
                metadata={
                    "retry_history": retry_history,
                    "max_attempts": self.config.max_attempts,
                    "domain": self.config.domain,
                    "final_attempt": attempt,
                },
                next_action_hint="Review error and retry if transient",
                recommended_action=FailedOperation.RecommendedAction.MANUAL_CHECK,
            )

            logger.info(f"[RetryHandler] Created DLQ entry: id={failed_op.id}")
            return failed_op.id

        except Exception as dlq_error:
            logger.error(f"[RetryHandler] Failed to create DLQ entry: {dlq_error}")
            return None


def with_retry(
    domain: str = "default",
    max_attempts: int | None = None,
    retryable_exceptions: tuple[type[Exception], ...] | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator to add retry logic to a function.

    Args:
        domain: Domain for configuration
        max_attempts: Override max attempts
        retryable_exceptions: Exceptions that should trigger retry

    Returns:
        Decorated function

    Example:
        @with_retry(domain="payment", max_attempts=3)
        def call_external_api():
            return requests.post(...)
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            config = RetryConfig.from_settings(domain)
            if max_attempts is not None:
                config.max_attempts = max_attempts
            if retryable_exceptions is not None:
                config.retryable_exceptions = retryable_exceptions

            handler = RetryHandler(config=config, domain=domain)
            result = handler.execute(func, *args, **kwargs)

            if result.success:
                return result.value
            else:
                raise MaxRetriesExceededError(
                    f"Max retries exceeded for {func.__name__}",
                    retry_count=result.attempt,
                    max_retries=config.max_attempts,
                    last_error=result.error,
                )

        return wrapper

    return decorator
