"""
Retry Handler (Legacy)

Core retry handler with exponential backoff, rate limit awareness,
and throttle-aware backoff.

.. deprecated::
    RetryPolicy를 사용하세요. RetryHandler는 하위 호환을 위해 유지됩니다.
    새 코드에서는 RetryPolicy + PolicyComposer 조합을 권장합니다.
"""

from __future__ import annotations

import structlog
import warnings
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from selfhealing.core.timezone import now

from ..backoff_calculator import BackoffCalculator, BackoffConfig
from .models import RetryAction, RetryConfig, RetryResult, T

if TYPE_CHECKING:
    from ..rate_limit_coordinator import RateLimitCoordinator

logger = structlog.get_logger()


def _is_system_enabled() -> bool:
    """Check if self-healing system is enabled (Kill Switch not activated)."""
    try:
        from selfhealing.services.system_control import SystemControlManager

        manager = SystemControlManager()
        return manager.is_enabled()
    except Exception:
        # If SystemControlManager not available, assume enabled
        return True


class RetryHandler:
    """
    Handles retry logic with exponential backoff.

    .. deprecated::
        RetryPolicy를 사용하세요. RetryHandler는 하위 호환을 위해 유지됩니다.

    Now includes Rate Limit Awareness to prevent Self-DDoS:
    - Detects 429 responses
    - Coordinates cooldown across all workers
    - Uses distributed storage (Redis/DB)

    Throttle-aware Backoff (v2.0):
    - Dynamically adjusts backoff based on system load
    - Full Stop detection for immediate DLQ routing
    - Adaptive retry budget management

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
        rate_limit_coordinator: RateLimitCoordinator | None = None,
        throttle_aware: bool | None = None,
        service_name: str | None = None,
    ):
        """
        Initialize the retry handler.

        Args:
            config: RetryConfig instance, or None to load from settings
            domain: Domain for per-domain configuration
            rate_limit_coordinator: Optional coordinator for rate limiting
            throttle_aware: Override throttle awareness (defaults to config)
            service_name: Service name for Throttle Registry (defaults to domain)
        """
        warnings.warn(
            "RetryHandler is deprecated. Use RetryPolicy instead. " "RetryHandler will be removed in a future version.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.config = config or RetryConfig.from_settings(domain)

        # Throttle-aware 설정 결정 (파라미터 > config)
        self._throttle_aware = throttle_aware if throttle_aware is not None else self.config.throttle_aware

        # 서비스명 결정 (파라미터 > domain)
        effective_service_name = service_name or domain

        # Backoff 계산기 선택
        if self._throttle_aware:
            from ..backoff_calculator import ThrottleAwareBackoffCalculator

            self.backoff = ThrottleAwareBackoffCalculator(
                BackoffConfig(
                    base=self.config.backoff_base,
                    max_delay=self.config.backoff_max,
                    jitter_percent=self.config.jitter_percent,
                ),
                service_name=effective_service_name,
            )
        else:
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

        # Adaptive Retry Budget (v2.0)
        from ..backoff_calculator import AdaptiveRetryBudget

        self._retry_budget = AdaptiveRetryBudget()

        # 마지막 백오프 정보 (DLQ 메타데이터용)
        self._last_backoff_info: dict[str, Any] | None = None

    @property
    def rate_limit_coordinator(self) -> RateLimitCoordinator | None:
        """Get rate limit coordinator, lazily initialized."""
        if self._rate_limit_coordinator is None and self.config.rate_limit_aware:
            try:
                from ..rate_limit_coordinator import get_rate_limit_coordinator

                self._rate_limit_coordinator = get_rate_limit_coordinator()
            except Exception as e:
                logger.warning(
                    "retry_handler.initialize_rate_limit_coordinator",
                    error=e,
                )
        return self._rate_limit_coordinator

    def _log_retry_audit(
        self,
        attempt: int,
        success: bool,
        error_type: str | None = None,
        error_message: str | None = None,
        wait_time: float | None = None,
        rate_limited: bool = False,
        context: dict | None = None,
    ) -> None:
        """
        재시도 이벤트를 Audit 로그에 기록.

        Fail-Open 원칙: Audit 실패가 비즈니스 로직을 중단시키지 않음.
        """
        try:
            from ..audit_helpers import log_retry_audit

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
            logger.debug(
                "retry_handler.audit_logging_failed_ignored",
                error=e,
            )

    def _check_error_budget_gate(self) -> Any | None:
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
            logger.warning(
                "retry_handler.errorbudgetgate_check_failed",
                error=e,
            )
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
            retry_after = exception.retry_after
        elif hasattr(exception, "response"):
            response = exception.response
            if hasattr(response, "headers"):
                retry_after_header = response.headers.get("Retry-After")
                if retry_after_header:
                    try:
                        retry_after = float(retry_after_header)
                    except ValueError:
                        pass

        return is_rate_limited, retry_after

    def should_retry(
        self,
        exception: Exception,
        attempt: int,
        effective_max_attempts: int | None = None,
    ) -> bool:
        """
        Determine if an exception should trigger a retry.

        Args:
            exception: The exception that occurred
            attempt: Current attempt number
            effective_max_attempts: CRITICAL 티어 grace retry 포함한 최대 시도 횟수

        Returns:
            True if should retry, False otherwise
        """
        max_attempts = effective_max_attempts or self.config.max_attempts

        # Check if max attempts reached
        if attempt >= max_attempts:
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
                logger.info(
                    "retry_handler.waited_rate_limit_cooldown",
                    result=result.wait_time,
                )

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
                logger.warning(
                    "retry_handler.rate_limit_detected_set",
                    cooldown=cooldown,
                )

    def get_next_delay(self, attempt: int, is_critical_tier: bool = False) -> int:
        """
        Get the delay before the next retry attempt.

        Throttle-aware 모드 시 시스템 상태 반영.

        Args:
            attempt: Current attempt number
            is_critical_tier: CRITICAL 티어 요청 여부 (Full Stop 시 grace retry 허용)

        Returns:
            Delay in seconds. -1 indicates Full Stop (immediate DLQ routing).
        """
        if self._throttle_aware and hasattr(self.backoff, "calculate_with_throttle_context"):
            delay, multiplier, reason = self.backoff.calculate_with_throttle_context(attempt)

            # 백오프 정보 저장 (DLQ 메타데이터용)
            self._last_backoff_info = {
                "delay": delay,
                "multiplier": multiplier,
                "reason": reason,
            }

            # Full Stop 시 즉시 DLQ 이동 신호
            if delay < 0:
                # CRITICAL 티어는 grace retry 허용
                if is_critical_tier:
                    grace_attempts = self.config.critical_tier_full_stop_grace_retries
                    grace_attempt_number = attempt - self.config.max_attempts

                    if grace_attempt_number <= grace_attempts:
                        logger.warning(
                            f"[RetryHandler] CRITICAL tier grace retry "
                            f"{grace_attempt_number}/{grace_attempts} during FULL_STOP"
                        )
                        self._record_critical_tier_grace_metric()
                        return self.config.critical_tier_full_stop_max_delay

                logger.warning(
                    "retry_handler.full_stop_active_skipping",
                    attempt=attempt,
                )
                return -1

            if multiplier > 1.0:
                logger.info(
                    f"[RetryHandler] Backoff adjusted: {self.backoff.calculate(attempt)}s → "
                    f"{delay}s (×{multiplier:.1f}, reason={reason})"
                )

            return delay

        return self.backoff.calculate(attempt)

    def _record_critical_tier_grace_metric(self) -> None:
        """CRITICAL 티어 grace retry 메트릭 기록."""
        try:
            from ..metrics.definitions import retry_critical_tier_grace_retries_total

            retry_critical_tier_grace_retries_total.labels(domain=self.config.domain).inc()
        except ImportError:
            pass
        except Exception:
            pass

    def get_combined_delay(self, attempt: int, is_critical_tier: bool = False) -> int:
        """
        429 쿨다운과 Throttle 백오프 중 긴 값 반환.

        합산 대신 max() 선택으로 사용자 체감 대기 시간 개선.

        Args:
            attempt: Current attempt number
            is_critical_tier: CRITICAL 티어 요청 여부

        Returns:
            Combined delay in seconds. -1 indicates Full Stop.
        """
        throttle_delay = self.get_next_delay(attempt, is_critical_tier)

        # Full Stop 신호는 그대로 전달
        if throttle_delay < 0:
            return throttle_delay

        # 429 쿨다운 남은 시간 조회 (대기하지 않고 확인만)
        coordinator = self.rate_limit_coordinator
        if coordinator:
            try:
                state = coordinator._storage.get_state(self._rate_limit_key)
                if state.is_in_cooldown:
                    rate_limit_delay = int(state.remaining_cooldown)

                    # max() 선택
                    combined = max(rate_limit_delay, throttle_delay)

                    if combined != throttle_delay:
                        logger.info(
                            f"[RetryHandler] Using 429 cooldown ({rate_limit_delay}s) "
                            f"over throttle backoff ({throttle_delay}s)"
                        )

                    return combined
            except Exception as e:
                logger.debug(
                    "retry_handler.rate_limit_state_check",
                    error=e,
                )

        return throttle_delay

    def _check_preconditions(self, context: dict[str, Any] | None) -> RetryResult | None:
        """Kill Switch 및 ErrorBudgetGate 사전 조건 확인. 차단 시 RetryResult 반환."""
        # Kill Switch 체크
        if not _is_system_enabled():
            logger.warning(
                "retry_handler.execute_blocked_kill_switch",
                self=self.config.domain,
            )
            return RetryResult(
                success=False,
                action=RetryAction.ABORT,
                attempt=0,
                error=Exception("Kill Switch is active: self-healing system is disabled"),
            )

        # ErrorBudgetGate 체크
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

        return None

    def _handle_attempt_failure(
        self,
        e: Exception,
        attempt: int,
        effective_max_attempts: int,
        context: dict[str, Any] | None,
        retry_history: list[dict[str, Any]],
        is_critical_tier: bool,
    ) -> bool:
        """
        단일 시도 실패 처리. 계속 재시도해야 하면 True, 중단이면 False 반환.
        """
        retry_history.append(
            {
                "attempt": attempt,
                "error_type": type(e).__name__,
                "error_message": str(e)[:500],
                "timestamp": now().isoformat(),
            }
        )

        logger.warning(
            "retry_handler.attempt_failed",
            attempt=attempt,
            effective_max_attempts=effective_max_attempts,
            error=e,
        )

        # Self-DDoS prevention: Handle rate limit errors
        rate_limited, _ = self.is_rate_limit_error(e)
        self._handle_rate_limit_error(e)

        # Throttle-aware delay 계산 (Full Stop 감지 포함)
        next_delay = None
        throttle_reason = None
        if self.should_retry(e, attempt, effective_max_attempts):
            next_delay = self.get_combined_delay(attempt, is_critical_tier)

            # Full Stop 신호 처리 (-1)
            if next_delay < 0:
                logger.warning("retry_handler.full_stop_triggered_moving")
                self._log_retry_audit(
                    attempt=attempt,
                    success=False,
                    error_type=type(e).__name__,
                    error_message=str(e)[:500],
                    wait_time=None,
                    rate_limited=rate_limited,
                    context={
                        **(context or {}),
                        "throttle_aware_backoff": self._throttle_aware,
                        "throttle_reason": "full_stop",
                    },
                )
                return False  # 중단

            # Throttle 상태에 따라 예산 조정
            if self._last_backoff_info:
                throttle_reason = self._last_backoff_info.get("reason")
                self._retry_budget.adjust_budget_for_throttle_state(throttle_reason or "normal")

        self._log_retry_audit(
            attempt=attempt,
            success=False,
            error_type=type(e).__name__,
            error_message=str(e)[:500],
            wait_time=next_delay if next_delay and next_delay > 0 else None,
            rate_limited=rate_limited,
            context={
                **(context or {}),
                "throttle_aware_backoff": self._throttle_aware,
                "throttle_reason": throttle_reason,
            },
        )

        if self.should_retry(e, attempt, effective_max_attempts) and next_delay is not None and next_delay >= 0:
            delay = next_delay
            logger.info(
                "retry_handler.retry_attempt",
                delay=delay,
                value=attempt + 1,
                effective_max_attempts=effective_max_attempts,
            )
            return True  # 계속

        return False  # 중단

    def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        context: dict[str, Any] | None = None,
        is_critical_tier: bool = False,
        **kwargs: Any,
    ) -> RetryResult:
        """
        Execute a function with retry logic.

        Now includes Self-DDoS prevention:
        - Waits for rate limit cooldown before each attempt
        - Sets global cooldown on 429 errors
        - Coordinates across all workers via distributed storage

        Throttle-aware Backoff (v2.0):
        - Full Stop 시 재시도 없이 즉시 DLQ 이동
        - Emergency 시 Backoff 배율 적용
        - Adaptive Retry Budget으로 재시도 비율 제한
        - CRITICAL 티어는 Full Stop 시 grace retry 허용

        Note: This is a synchronous implementation. For async tasks,
        use the Celery-based retry mechanism.

        Args:
            func: Function to execute
            *args: Positional arguments for the function
            context: Optional context for forensic logging
            is_critical_tier: CRITICAL 티어 요청 여부 (Full Stop 시 grace retry 허용)
            **kwargs: Keyword arguments for the function

        Returns:
            RetryResult with the outcome
        """
        # 사전 조건 확인 (Kill Switch, ErrorBudgetGate)
        precondition_result = self._check_preconditions(context)
        if precondition_result is not None:
            return precondition_result

        attempt = 0
        last_error: Exception | None = None
        retry_history: list[dict[str, Any]] = []

        # CRITICAL 티어는 grace retry 포함한 최대 시도 횟수
        effective_max_attempts = self.config.max_attempts
        if is_critical_tier:
            effective_max_attempts += self.config.critical_tier_full_stop_grace_retries

        while attempt < effective_max_attempts:
            attempt += 1

            # Adaptive Retry Budget: 요청 기록
            self._retry_budget.record_request(is_retry=(attempt > 1))

            # Adaptive Retry Budget: 재시도 예산 확인 (CRITICAL 티어는 우회)
            if attempt > 1 and not is_critical_tier and not self._retry_budget.should_allow_retry():
                logger.warning(
                    "retry_handler.retry_budget_exhausted",
                    self=self._retry_budget.get_stats(),
                )
                break

            # Self-DDoS prevention: Wait if rate limited
            self._wait_for_rate_limit()

            try:
                result = func(*args, **kwargs)

                # Notify coordinator of success
                if self.rate_limit_coordinator:
                    self.rate_limit_coordinator.on_success(self._rate_limit_key)

                logger.debug(
                    "retry_handler.success_attempt",
                    attempt=attempt,
                    self=self.config.max_attempts,
                )

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

                should_continue = self._handle_attempt_failure(
                    e,
                    attempt,
                    effective_max_attempts,
                    context,
                    retry_history,
                    is_critical_tier,
                )
                if should_continue:
                    continue
                else:
                    break

        # Max retries exceeded or non-retryable error
        logger.error(
            "retry_handler.max_retries_exceeded_last",
            attempt=attempt,
            effective_max_attempts=effective_max_attempts,
            last_error=last_error,
        )

        # Move to DLQ if enabled
        dlq_id = None
        if self.config.enable_dlq:
            dlq_id = self._move_to_dlq(
                last_error=last_error,
                attempt=attempt,
                context=context,
                retry_history=retry_history,
                backoff_info=self._last_backoff_info,
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
        backoff_info: dict[str, Any] | None = None,
    ) -> int | None:
        """
        Move the failed operation to the Dead Letter Queue.

        Args:
            last_error: The last exception that occurred
            attempt: Final attempt number
            context: Additional context for forensic logging
            retry_history: History of all retry attempts
            backoff_info: Throttle-aware backoff 정보 (v2.0)

        Returns:
            DLQ record ID or None if DLQ is disabled
        """
        from ..dlq_service import store_to_dlq

        try:
            context = context or {}
            error_type = type(last_error).__name__ if last_error else "Unknown"

            # 기본 메타데이터
            metadata = {
                "retry_history": retry_history,
                "max_attempts": self.config.max_attempts,
                "domain": self.config.domain,
                "final_attempt": attempt,
            }

            # Throttle-aware backoff 정보 추가 (v2.0)
            if backoff_info:
                metadata.update(
                    {
                        "final_delay_seconds": backoff_info.get("delay"),
                        "backoff_multiplier": backoff_info.get("multiplier"),
                        "throttle_reason": backoff_info.get("reason"),
                        "throttle_aware_enabled": self._throttle_aware,
                    }
                )

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
                metadata=metadata,
                next_action_hint="Review error and retry if transient",
                recommended_action="manual_check",
            )

            if result.success:
                logger.info(
                    "retry_handler.created_dlq_entry",
                    result=result.dlq_id,
                )
                return result.dlq_id
            else:
                logger.error(
                    "retry_handler.failed_create_dlq_entry",
                    result=result.error,
                )
                return None

        except Exception as dlq_error:
            logger.error(
                "retry_handler.failed_create_dlq_entry",
                dlq_error=dlq_error,
            )
            return None
