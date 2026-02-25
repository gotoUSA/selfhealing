"""
Circuit Breaker Policy — 함수 래핑 기반 Circuit Breaker.

기존 CircuitBreakerService.should_allow() 기반의 조건 검사 방식을
ResiliencePolicy.execute() 기반의 함수 래핑 방식으로 전환한다.

내부적으로 기존 CircuitBreakerService를 재사용하며,
자동 카운팅(record_failure/record_success)을 통해 상태를 관리한다.

3가지 서킷 제어 경로가 독립적으로 공존한다:
- CircuitBreakerPolicy (record_failure): 일반 Exception 기반 자동 카운팅
- ProtectionMixin (record_rate_limit_response): 429 트래픽 기반 force_open
- ManualControlMixin (force_open/force_close): 운영자 수동 제어
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

import structlog

from selfhealing.interfaces.resilience_policy import (
    PolicyContext,
    PolicyOutcome,
    PolicyResult,
    ResiliencePolicy,
)

from .config import CircuitBreakerConfig
from .exceptions import CircuitBreakerOpenError
from .hooks import build_default_hooks
from .service import CircuitBreakerService

logger = structlog.get_logger()

T = TypeVar("T")


class CircuitBreakerPolicy(ResiliencePolicy[T]):
    """
    Circuit Breaker Policy — 함수 래핑 기반.

    should_allow()로 요청 허용 여부를 판단하고,
    실행 결과에 따라 record_success() / record_failure()를 자동 호출한다.

    - CB 비활성화 시: 함수를 바로 실행하고 SUCCESS 반환
    - CB OPEN 상태: 함수를 실행하지 않고 REJECTED 반환 (CircuitBreakerOpenError)
    - 함수 성공: record_success() 호출 후 SUCCESS 반환
    - 함수 실패: _is_failure() 판단 후 record_failure() 호출, 예외는 상위로 전파

    Args:
        service_name: Circuit Breaker가 보호하는 외부 서비스 식별자
        cb_service: 기존 CircuitBreakerService 인스턴스 (None이면 자동 생성)
        config: CircuitBreakerConfig (cb_service가 None일 때 사용)
        failure_exceptions: 실패로 카운팅할 예외 타입 튜플
        ignore_exceptions: 실패로 카운팅하지 않을 예외 타입 튜플
        hooks: PolicyHook 목록 (None이면 기본 Audit+EventBus 훅 사용)
    """

    def __init__(
        self,
        service_name: str,
        cb_service: CircuitBreakerService | None = None,
        config: CircuitBreakerConfig | None = None,
        failure_exceptions: tuple[type[Exception], ...] = (Exception,),
        ignore_exceptions: tuple[type[Exception], ...] = (),
        hooks: list | None = None,
    ):
        self._service_name = service_name
        self._cb_service = cb_service or self._create_default_service(config)
        self._failure_exceptions = failure_exceptions
        self._ignore_exceptions = ignore_exceptions
        self._hooks = hooks if hooks is not None else build_default_hooks()

    @staticmethod
    def _create_default_service(
        config: CircuitBreakerConfig | None = None,
    ) -> CircuitBreakerService:
        """
        기본 CircuitBreakerService 생성 — LayeredRepository 사용.

        ProviderRegistry에 "layered" 키가 등록되어 있으면 LayeredRepository를 사용한다.
        등록되지 않은 경우 ProviderRegistry 기본값(redis)으로 fallback한다.
        이를 통해 hot path에서 Redis I/O를 제거하고 L1 Memory 판정을 보장한다 (#227 §7.4).
        """
        repository = None
        try:
            from selfhealing.factory import ProviderRegistry

            repository = ProviderRegistry.get_circuit_breaker_repo(name="layered")
        except (ValueError, ImportError):
            # "layered" 미등록 시 ProviderRegistry 기본값 사용
            logger.debug("circuit_breaker_policy.layered_repo_available_falling")
        return CircuitBreakerService(config=config, repository=repository)

    @property
    def name(self) -> str:
        """Policy 식별자."""
        return "circuit_breaker"

    @property
    def service_name(self) -> str:
        """보호 대상 서비스 이름."""
        return self._service_name

    @property
    def cb_service(self) -> CircuitBreakerService:
        """내부 CircuitBreakerService 인스턴스."""
        return self._cb_service

    def _is_failure(self, error: Exception) -> bool:
        """
        예외가 실패로 카운팅되어야 하는지 판단.

        ignore_exceptions에 해당하면 실패로 카운팅하지 않는다.
        failure_exceptions에 해당하면 실패로 카운팅한다.
        """
        if isinstance(error, self._ignore_exceptions):
            return False
        return isinstance(error, self._failure_exceptions)

    def _invoke_hooks(self, method: str, *args: Any) -> None:
        """Fail-Open으로 모든 훅을 호출한다."""
        for hook in self._hooks:
            try:
                getattr(hook, method)(*args)
            except Exception as e:
                logger.debug(
                    "circuit_breaker_policy.hook_failed",
                    adapter_type=type(hook).__name__,
                    method=method,
                    error=e,
                )

    def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        context: PolicyContext | None = None,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        Circuit Breaker 상태 기반으로 함수를 실행한다.

        1. CB 비활성화 → 바로 실행
        2. should_allow() == False → REJECTED 반환 (함수 미실행) + hook.on_reject
        3. should_allow() == True → 함수 실행
           - 성공 → record_success() 후 SUCCESS 반환 + hook.on_success
           - 실패 → _is_failure() 판단 후 record_failure() + hook.on_failure, 예외 재전파

        CB OPEN으로 인한 거부 시에는 예외를 던지지 않고 PolicyResult로 반환한다.
        함수 실행 중 발생한 예외는 상위 Policy(Retry 등)에서 처리하도록 재전파한다.
        """
        # CB 비활성화 시 바로 실행
        if not self._cb_service.is_enabled:
            result = func(*args, **kwargs)
            return PolicyResult(
                value=result,
                outcome=PolicyOutcome.SUCCESS,
                executed_policies=["circuit_breaker"],
            )

        # Hook: 실행 시작
        self._invoke_hooks("on_execute", self._service_name, 1)

        # 요청 허용 여부 확인
        if not self._cb_service.should_allow(self._service_name):
            reject_result = PolicyResult(
                outcome=PolicyOutcome.REJECTED,
                error=CircuitBreakerOpenError(self._service_name),
                executed_policies=["circuit_breaker"],
                metadata={
                    "service_name": self._service_name,
                    "state": self._cb_service.get_state(self._service_name),
                },
            )
            # Hook: CB OPEN 거부 (Audit + EventBus)
            self._invoke_hooks("on_reject", self._service_name, "circuit_open")
            return reject_result

        # 함수 실행
        try:
            result = func(*args, **kwargs)
            self._cb_service.record_success(self._service_name)
            success_result = PolicyResult(
                value=result,
                outcome=PolicyOutcome.SUCCESS,
                executed_policies=["circuit_breaker"],
            )
            # Hook: 실행 성공 (Audit + EventBus)
            self._invoke_hooks("on_success", self._service_name, success_result)
            return success_result
        except Exception as e:
            if self._is_failure(e):
                self._cb_service.record_failure(
                    self._service_name,
                    error_context={"error": str(e), "type": type(e).__name__},
                )
            # Hook: 실행 실패 (Audit + EventBus)
            self._invoke_hooks("on_failure", self._service_name, e, 1)
            raise  # 상위 Policy(Retry 등)에서 처리하도록 전파


def circuit_breaker(
    service_name: str | None = None,
    cb_service: CircuitBreakerService | None = None,
    config: CircuitBreakerConfig | None = None,
    failure_exceptions: tuple[type[Exception], ...] = (Exception,),
    ignore_exceptions: tuple[type[Exception], ...] = (),
) -> Callable:
    """
    Circuit Breaker 데코레이터.

    함수에 적용하면 CircuitBreakerPolicy로 자동 래핑된다.
    service_name이 None이면 함수의 __qualname__을 기본값으로 사용한다.

    Usage::

        @circuit_breaker("payment_api")
        def call_payment_api():
            ...

        @circuit_breaker()  # service_name = 함수의 __qualname__
        def call_external():
            ...
    """

    def decorator(func: Callable[..., T]) -> Callable[..., PolicyResult[T]]:
        name = service_name or func.__qualname__
        policy = CircuitBreakerPolicy(
            service_name=name,
            cb_service=cb_service,
            config=config,
            failure_exceptions=failure_exceptions,
            ignore_exceptions=ignore_exceptions,
        )

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> PolicyResult[T]:
            return policy.execute(func, *args, **kwargs)

        # Policy 인스턴스에 접근할 수 있도록 속성 부착
        wrapper.policy = policy  # type: ignore[attr-defined]
        return wrapper

    return decorator
