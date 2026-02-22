"""
Retry Policy — 순수 재시도 Policy 구현.

RetryHandler에서 하드코딩된 외부 의존성(Kill Switch, ErrorBudgetGate,
Audit, DLQ)을 제거하고 순수한 재시도 로직만 담당한다.

외부 관심사는 PolicyComposer의 Guard/Hook/Sink로 주입받는다.
내부 협력 객체(Collaborator)는 생성자로 주입받는다:
- backoff: 백오프 계산 전략 (core/backoff.py BackoffStrategy ABC)
- rate_limit_coordinator: 429 대기/성공 알림/쿨다운 (생성자 주입)
- retry_budget: 적응형 재시도 예산 (루프 내 상태 변경, Guard 부적합)
- sleeper: 대기 함수 (None이면 sleep하지 않음, Celery 위임)
"""

from __future__ import annotations

import structlog
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

from selfhealing.core.backoff import BackoffStrategy, ExponentialBackoff
from selfhealing.interfaces.resilience_policy import (
    PolicyContext,
    PolicyOutcome,
    PolicyResult,
    ResiliencePolicy,
)

from .models import RetryPolicyConfig

if TYPE_CHECKING:
    from selfhealing.services.backoff_calculator import AdaptiveRetryBudget
    from selfhealing.services.rate_limit_coordinator import RateLimitCoordinator

logger = structlog.get_logger()

T = TypeVar("T")

# Rate limit 감지 키워드 (handler.py is_rate_limit_error와 동일)
_RATE_LIMIT_INDICATORS = [
    "429",
    "rate limit",
    "ratelimit",
    "too many requests",
    "throttle",
    "quota exceeded",
]


class RetryPolicy(ResiliencePolicy[T]):
    """
    순수 재시도 Policy.

    Kill Switch, ErrorBudgetGate, Audit, DLQ 등 외부 관심사는
    PolicyComposer의 Guard/Hook/Sink가 처리한다.

    Collaborator:
    - retry_budget: 루프 내 매 시도마다 상태 변경 (Guard 부적합)
    - rate_limit_coordinator: wait/signal/cooldown 복합 책임
    - backoff: core/backoff.py BackoffStrategy(ABC) 재활용
    - sleeper: 대기 함수 (None이면 sleep 미수행, Celery 위임)
    """

    def __init__(
        self,
        config: RetryPolicyConfig,
        backoff: BackoffStrategy | None = None,
        rate_limit_coordinator: RateLimitCoordinator | None = None,
        retry_budget: AdaptiveRetryBudget | None = None,
        sleeper: Callable[[float], None] | None = None,
    ):
        self._config = config
        self._backoff = backoff or ExponentialBackoff(
            base_delay=config.backoff_base,
            max_delay=config.backoff_max,
            jitter_factor=config.jitter_percent / 100.0,
        )
        self._rate_limit_coordinator = rate_limit_coordinator
        self._retry_budget = retry_budget
        self._sleeper = sleeper

    @property
    def name(self) -> str:
        return "retry"

    def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        context: PolicyContext | None = None,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        순수 재시도 실행.

        Kill Switch, ErrorBudgetGate, Audit, DLQ는
        PolicyComposer가 Guard/Hook/Sink로 처리한다.
        """
        attempt = 0
        last_error: Exception | None = None
        retry_history: list[dict[str, Any]] = []

        while attempt < self._config.max_attempts:
            attempt += 1

            # Adaptive Retry Budget: 요청 기록 + 예산 확인
            if self._retry_budget:
                self._retry_budget.record_request(is_retry=(attempt > 1))
                if attempt > 1 and not self._retry_budget.should_allow_retry():
                    logger.warning(
                        "[RetryPolicy] Retry budget exhausted: %s",
                        self._retry_budget.get_stats(),
                    )
                    break

            # Rate limit 대기 (선택적)
            if self._rate_limit_coordinator:
                result = self._rate_limit_coordinator.wait_if_needed(self._config.domain)
                if result.waited:
                    logger.info(
                        "[RetryPolicy] Waited %.2fs for rate limit cooldown",
                        result.wait_time,
                    )

            try:
                result = func(*args, **kwargs)

                # RateLimitCoordinator 성공 알림
                if self._rate_limit_coordinator:
                    self._rate_limit_coordinator.on_success(self._config.domain)

                return PolicyResult(
                    value=result,
                    outcome=PolicyOutcome.SUCCESS,
                    total_attempts=attempt,
                    executed_policies=["retry"],
                )
            except Exception as e:
                last_error = e
                retry_history.append(
                    {
                        "attempt": attempt,
                        "error_type": type(e).__name__,
                        "error_message": str(e)[:500],
                    }
                )

                # 429 감지 → RateLimitCoordinator에 쿨다운 요청
                if self._rate_limit_coordinator:
                    self._notify_rate_limit_cooldown(e)

                if not self._should_retry(e, attempt):
                    break

                # Backoff 계산
                delay = self._backoff.calculate(attempt, context=context)

                # Sleeper: None이면 delay 값만 기록 (Celery 위임)
                if self._sleeper and delay > 0:
                    self._sleeper(delay)

        return PolicyResult(
            outcome=PolicyOutcome.FAILURE,
            error=last_error,
            total_attempts=attempt,
            executed_policies=["retry"],
            metadata={
                "max_attempts": self._config.max_attempts,
                "domain": self._config.domain,
                "should_dlq": self._config.enable_dlq,
                "retry_history": retry_history,
            },
        )

    def _should_retry(self, exception: Exception, attempt: int) -> bool:
        """재시도 가능 여부 판단."""
        if attempt >= self._config.max_attempts:
            return False

        if isinstance(exception, self._config.non_retryable_exceptions):
            return False

        if isinstance(exception, self._config.retryable_exceptions):
            return True

        return False

    def _notify_rate_limit_cooldown(self, exception: Exception) -> None:
        """429 응답 감지 시 RateLimitCoordinator에 쿨다운 설정."""
        is_rate_limited, retry_after = self._detect_rate_limit(exception)

        if is_rate_limited and self._rate_limit_coordinator:
            cooldown = self._rate_limit_coordinator.on_rate_limited(
                key=self._config.domain,
                retry_after=retry_after,
            )
            logger.warning(
                "[RetryPolicy] Rate limit detected, set global cooldown: %.2fs",
                cooldown,
            )

    @staticmethod
    def _detect_rate_limit(exception: Exception) -> tuple[bool, float | None]:
        """429 rate limit 에러 여부와 Retry-After 값을 감지."""
        error_str = str(exception).lower()
        error_type = type(exception).__name__.lower()

        is_rate_limited = any(indicator in error_str or indicator in error_type for indicator in _RATE_LIMIT_INDICATORS)

        retry_after: float | None = None
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
