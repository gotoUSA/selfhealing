"""
Retry Handler with Exponential Backoff

Provides a reusable retry mechanism with:
- Configurable max attempts
- Exponential backoff with jitter
- Idempotency checking
- DLQ routing on exhaustion
- Forensic context capture
- Rate limit awareness (Self-DDoS prevention)

Reference: docs/L3_SELF_HEALING_ARCHITECTURE.md §7, §8
"""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Optional, TypeVar

from selfhealing.core.timezone import now
from selfhealing.settings import get_config

from .backoff_calculator import BackoffCalculator, BackoffConfig

if TYPE_CHECKING:
    from .rate_limit_coordinator import RateLimitCoordinator

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _is_system_enabled() -> bool:
    """Check if self-healing system is enabled (Kill Switch not activated)."""
    try:
        from selfhealing.services.system_control import SystemControlManager
        manager = SystemControlManager()
        return manager.is_enabled()
    except Exception:
        # If SystemControlManager not available, assume enabled
        return True


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

    # Rate limit awareness settings
    rate_limit_aware: bool = True  # Enable Self-DDoS prevention
    rate_limit_key: str | None = None  # Custom key, defaults to domain

    @classmethod
    def from_settings(cls, domain: str = "default") -> "RetryConfig":
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


class RetryHandler:
    """
    Handles retry logic with exponential backoff.

    Now includes Rate Limit Awareness to prevent Self-DDoS:
    - Detects 429 responses
    - Coordinates cooldown across all workers
    - Uses distributed storage (Redis/DB)

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
        rate_limit_coordinator: Optional["RateLimitCoordinator"] = None,
    ):
        """
        Initialize the retry handler.

        Args:
            config: RetryConfig instance, or None to load from settings
            domain: Domain for per-domain configuration
            rate_limit_coordinator: Optional coordinator for rate limiting
        """
        self.config = config or RetryConfig.from_settings(domain)
        self.backoff = BackoffCalculator(
            BackoffConfig(
                base=self.config.backoff_base,
                max_delay=self.config.backoff_max,
                jitter_percent=self.config.jitter_percent,
            )
        )

        # Rate limit coordinator for Self-DDoS prevention
        self._rate_limit_coordinator = rate_limit_coordinator
        self._rate_limit_key = self.config.rate_limit_key or self.config.domain

    @property
    def rate_limit_coordinator(self) -> Optional["RateLimitCoordinator"]:
        """Get rate limit coordinator, lazily initialized."""
        if self._rate_limit_coordinator is None and self.config.rate_limit_aware:
            try:
                from .rate_limit_coordinator import get_rate_limit_coordinator

                self._rate_limit_coordinator = get_rate_limit_coordinator()
            except Exception as e:
                logger.warning(f"[RetryHandler] Could not initialize rate limit coordinator: {e}")
        return self._rate_limit_coordinator

    def _log_retry_audit(
        self,
        attempt: int,
        success: bool,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        wait_time: Optional[float] = None,
        rate_limited: bool = False,
        context: Optional[dict] = None,
    ) -> None:
        """
        재시도 이벤트를 Audit 로그에 기록.
        
        Fail-Open 원칙: Audit 실패가 비즈니스 로직을 중단시키지 않음.
        """
        try:
            from .audit_helpers import log_retry_audit
            
            log_retry_audit(
                domain=self.config.domain,
                attempt=attempt,
                max_attempts=self.config.max_attempts,
                success=success,
                error_type=error_type,
                error_message=error_message,
                wait_time=wait_time,
                rate_limited=rate_limited,
                context=context,
            )
        except Exception as e:
            # Fail-Open: Audit 실패가 재시도 로직을 중단시키지 않음
            logger.debug(f"[RetryHandler] Audit logging failed (ignored): {e}")

    def _check_error_budget_gate(self) -> Optional[Any]:
        """
        Check ErrorBudgetGate before retrying.
        
        Returns:
            GateCheckResult if gate is available, None otherwise
        """
        try:
            from selfhealing.services.error_budget_gate import check_automation_allowed
            return check_automation_allowed()
        except ImportError:
            # ErrorBudgetGate not available
            return None
        except Exception as e:
            logger.warning(f"[RetryHandler] ErrorBudgetGate check failed: {e}")
            return None

    def is_rate_limit_error(self, exception: Exception) -> tuple[bool, float | None]:
        """
        Check if an exception indicates a rate limit (429) error.

        Args:
            exception: The exception to check

        Returns:
            Tuple of (is_rate_limited, retry_after_seconds)
        """
        # Check for common rate limit exception patterns
        error_str = str(exception).lower()
        error_type = type(exception).__name__.lower()

        # Common indicators
        rate_limit_indicators = [
            "429",
            "rate limit",
            "ratelimit",
            "too many requests",
            "throttle",
            "quota exceeded",
        ]

        is_rate_limited = any(indicator in error_str or indicator in error_type for indicator in rate_limit_indicators)

        # Try to extract retry-after from exception
        retry_after = None
        if hasattr(exception, "retry_after"):
            retry_after = getattr(exception, "retry_after")
        elif hasattr(exception, "response"):
            response = getattr(exception, "response")
            if hasattr(response, "headers"):
                retry_after_header = response.headers.get("Retry-After")
                if retry_after_header:
                    try:
                        retry_after = float(retry_after_header)
                    except ValueError:
                        pass

        return is_rate_limited, retry_after

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

    def _wait_for_rate_limit(self) -> None:
        """Wait if currently rate limited (Self-DDoS prevention)."""
        coordinator = self.rate_limit_coordinator
        if coordinator:
            result = coordinator.wait_if_needed(self._rate_limit_key)
            if result.waited:
                logger.info(f"[RetryHandler] Waited {result.wait_time:.2f}s for rate limit cooldown")

    def _handle_rate_limit_error(self, exception: Exception) -> None:
        """Handle rate limit error by setting global cooldown."""
        is_rate_limited, retry_after = self.is_rate_limit_error(exception)

        if is_rate_limited:
            coordinator = self.rate_limit_coordinator
            if coordinator:
                cooldown = coordinator.on_rate_limited(
                    key=self._rate_limit_key,
                    retry_after=retry_after,
                )
                logger.warning(f"[RetryHandler] Rate limit detected, set global cooldown: {cooldown:.2f}s")

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

        Now includes Self-DDoS prevention:
        - Waits for rate limit cooldown before each attempt
        - Sets global cooldown on 429 errors
        - Coordinates across all workers via distributed storage

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
        # Kill Switch 체크: 시스템이 비활성화되면 재시도 없이 즉시 실패 반환
        if not _is_system_enabled():
            logger.warning(
                f"[RetryHandler] execute blocked: Kill Switch is active. "
                f"domain={self.config.domain}"
            )
            return RetryResult(
                success=False,
                action=RetryAction.ABORT,
                attempt=0,
                error=Exception("Kill Switch is active: self-healing system is disabled"),
            )

        # ErrorBudgetGate 체크: 에러 예산이 임계치 이하면 재시도 차단
        gate_result = self._check_error_budget_gate()
        if gate_result is not None and not gate_result.allowed:
            logger.warning(
                f"[RetryHandler] execute blocked by ErrorBudgetGate: "
                f"budget={gate_result.error_budget_percent}%, "
                f"threshold={gate_result.threshold_percent}%"
            )
            return RetryResult(
                success=False,
                action=RetryAction.ABORT,
                attempt=0,
                error=Exception(
                    f"Error budget critically low ({gate_result.error_budget_percent:.1f}%): "
                    "retry blocked to prevent further errors"
                ),
            )

        attempt = 0
        last_error: Exception | None = None
        retry_history: list[dict[str, Any]] = []

        while attempt < self.config.max_attempts:
            attempt += 1

            # Self-DDoS prevention: Wait if rate limited
            self._wait_for_rate_limit()

            try:
                result = func(*args, **kwargs)

                # Notify coordinator of success
                if self.rate_limit_coordinator:
                    self.rate_limit_coordinator.on_success(self._rate_limit_key)

                logger.debug(f"[RetryHandler] Success on attempt {attempt}/{self.config.max_attempts}")
                
                # Audit 기록: 재시도 성공
                self._log_retry_audit(
                    attempt=attempt,
                    success=True,
                    context=context,
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
                        "timestamp": now().isoformat(),
                    }
                )

                logger.warning(f"[RetryHandler] Attempt {attempt}/{self.config.max_attempts} failed: {e}")

                # Self-DDoS prevention: Handle rate limit errors
                rate_limited, _ = self.is_rate_limit_error(e)
                self._handle_rate_limit_error(e)
                
                # Audit 기록: 재시도 시도 (실패)
                next_delay = None
                if self.should_retry(e, attempt):
                    next_delay = self.get_next_delay(attempt)
                
                self._log_retry_audit(
                    attempt=attempt,
                    success=False,
                    error_type=type(e).__name__,
                    error_message=str(e)[:500],
                    wait_time=next_delay,
                    rate_limited=rate_limited,
                    context=context,
                )

                if self.should_retry(e, attempt):
                    delay = next_delay
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
        from .dlq_service import store_to_dlq

        try:
            context = context or {}
            error_type = type(last_error).__name__ if last_error else "Unknown"

            result = store_to_dlq(
                domain=self.config.domain,
                failure_type=f"MAX_RETRIES_{error_type.upper()}",
                order_id=context.get("order_id"),
                payment_id=context.get("payment_id"),
                user_id=context.get("user_id"),
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
                recommended_action="manual_check",
            )

            if result.success:
                logger.info(f"[RetryHandler] Created DLQ entry: id={result.dlq_id}")
                return result.dlq_id
            else:
                logger.error(f"[RetryHandler] Failed to create DLQ entry: {result.error}")
                return None

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
