"""
Resilience Policy Interfaces for Self-Healing System

Unified interfaces for all resilience patterns (Retry, Circuit Breaker,
Bulkhead, Fallback, Hedging, Throttle). Each pattern implements the
same Protocol, enabling declarative composition via PolicyComposer.

Provides:
- PolicyOutcome / PolicyResult: Unified result type replacing
  RetryResult, FallbackResult, CircuitBreakerResult, BulkheadFullError
- PolicyContext: Immutable execution context (frozen dataclass)
- ResiliencePolicy / AsyncResiliencePolicy: Core Protocols (sync/async)
- PolicyGuard / GuardResult: Pre-execution validation hooks
- PolicyHook: Execution event observer (Fail-Open)
- FailureSink: Terminal failure handler (DLQ, logging, etc.)

Design Principles:
1. Protocol-based for structural subtyping (duck typing)
2. Existing implementations are wrapped, not replaced
3. All business exceptions are swallowed into PolicyResult
4. KeyboardInterrupt/SystemExit pass through (except Exception)

Usage:
    from selfhealing.interfaces import (
        PolicyOutcome,
        PolicyResult,
        PolicyContext,
        ResiliencePolicy,
        AsyncResiliencePolicy,
        PolicyGuard,
        GuardResult,
        PolicyHook,
        FailureSink,
    )
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")


# =============================================================================
# Exceptions
# =============================================================================


class PolicyRejectedException(Exception):
    """Policy에 의해 거부된 경우 발생하는 예외.

    PolicyComposer가 Policy 체인 실행 중 REJECTED/TIMEOUT 결과를
    예외로 변환할 때 사용한다. Composer 내부에서만 사용되며
    최종 PolicyResult(outcome=REJECTED)로 변환되어 소비자에게 전달된다.
    """


# =============================================================================
# Enums
# =============================================================================


class PolicyOutcome(str, Enum):
    """Policy 실행 결과 종류."""

    SUCCESS = "success"  # 정상 성공
    SUCCESS_WITH_FALLBACK = "fallback"  # Fallback으로 성공
    REJECTED = "rejected"  # Policy에 의해 거부 (CB open, Bulkhead full 등)
    FAILURE = "failure"  # 모든 시도 실패
    TIMEOUT = "timeout"  # 시간 초과


# =============================================================================
# Data Transfer Objects (DTOs)
# =============================================================================


@dataclass
class PolicyResult(Generic[T]):
    """
    모든 resilience Policy의 통합 결과 타입.

    각 패턴의 기존 결과 타입을 단일 구조로 통합한다:
    - RetryResult(success, action, attempt, value, error, dlq_id)
    - FallbackResult(value, used_fallback, fallback_mode, original_error)
    - CircuitBreakerResult는 상태 관리 전용이므로 변환 대상 아님
    - BulkheadFullError 예외는 PolicyResult(outcome=REJECTED)로 변환

    Attributes:
        value: 실행 결과 값 (성공 시)
        outcome: 실행 결과 종류
        error: 실패 시 발생한 예외
        executed_policies: 실행된 Policy 이름 목록
        total_attempts: 총 시도 횟수
        total_duration_ms: 총 실행 시간 (밀리초)
        metadata: 패턴별 상세 정보 (선택적)
    """

    value: T | None = None
    outcome: PolicyOutcome = PolicyOutcome.SUCCESS
    error: Exception | None = None

    # 실행 메타데이터
    executed_policies: list[str] = field(default_factory=list)
    total_attempts: int = 1
    total_duration_ms: float = 0.0

    # 패턴별 상세 정보 (선택적)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        """성공 여부 (Fallback 포함)."""
        return self.outcome in (
            PolicyOutcome.SUCCESS,
            PolicyOutcome.SUCCESS_WITH_FALLBACK,
        )

    @property
    def rejected(self) -> bool:
        """Policy에 의해 거부되었는지 여부."""
        return self.outcome == PolicyOutcome.REJECTED


@dataclass(frozen=True)
class PolicyContext:
    """
    Policy 파이프라인 실행 컨텍스트 (Immutable).

    frozen=True로 설정하여 파이프라인 내 사이드 이펙트를 방지한다.
    수정 필요 시 dataclasses.replace()로 복사본을 생성한다 (Copy-on-Write).

    Attributes:
        order_id: 주문 식별자 (DLQ 저장 시 사용)
        payment_id: 결제 식별자 (DLQ 저장 시 사용)
        user_id: 사용자 식별자 (DLQ 저장 시 사용)
        tier_id: 서비스 티어 ("critical" | "standard" | "non_essential")
        region: 리전 식별자 (ErrorBudgetGate 판정 기준)
        domain: 도메인 식별자 (RetryConfig.domain 대응)
        trace_id: 분산 추적 ID (OTel trace_id)
        extra: 확장 필드 (snapshot_data, request_data 등)
    """

    # 비즈니스 식별자
    order_id: str | None = None
    payment_id: str | None = None
    user_id: str | None = None

    # Policy 판정 기준
    tier_id: str | None = None
    region: str | None = None

    # 도메인/추적
    domain: str = ""
    trace_id: str | None = None

    # 확장 필드
    extra: dict[str, Any] = field(default_factory=dict)

    def with_updates(self, **kwargs: Any) -> PolicyContext:
        """Copy-on-Write: 변경된 필드만 교체한 새 인스턴스 반환."""
        return replace(self, **kwargs)


@dataclass
class GuardResult:
    """
    Guard 검증 결과.

    Attributes:
        allowed: 실행 허용 여부 (True면 통과, False면 거부)
        reason: 거부 사유 (allowed=False일 때)
        metadata: 추가 정보 (Guard별 상세)
    """

    allowed: bool
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Core Protocols — Sync / Async
# =============================================================================


@runtime_checkable
class ResiliencePolicy(Protocol[T]):
    """
    동기 resilience 패턴이 구현하는 핵심 Protocol.

    각 Policy는 함수를 래핑하여 resilience 로직을 적용한다.
    Policy 간 조합은 PolicyComposer가 담당한다.

    예외 처리 컨트랙트:
    - 비즈니스 예외는 PolicyResult(outcome=FAILURE, error=e)에 포장하여 반환
    - except Exception 패턴으로 KeyboardInterrupt/SystemExit 자동 통과
    - BulkheadFullError는 Policy 래퍼에서 PolicyResult(outcome=REJECTED)로 변환
    """

    @property
    def name(self) -> str:
        """Policy 식별자 (예: 'retry', 'circuit_breaker', 'bulkhead')."""
        ...

    def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        context: PolicyContext | None = None,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        함수를 Policy로 감싸서 실행.

        Args:
            func: 실행할 함수
            *args: 함수 위치 인자
            context: 실행 컨텍스트 (Guard/Hook/Sink에 전파)
            **kwargs: 함수 키워드 인자

        Returns:
            PolicyResult[T]: 통합 결과. 예외를 던지지 않는다.
        """
        ...


@runtime_checkable
class AsyncResiliencePolicy(Protocol[T]):
    """
    비동기 resilience 패턴이 구현하는 Protocol.

    현재 해당하는 구현: AsyncSemaphoreBulkhead (async_semaphore.py).
    동일한 예외 처리 컨트랙트를 따른다.
    """

    @property
    def name(self) -> str:
        """Policy 식별자."""
        ...

    async def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        context: PolicyContext | None = None,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        비동기 함수를 Policy로 감싸서 실행.

        Args:
            func: 실행할 비동기 함수
            *args: 함수 위치 인자
            context: 실행 컨텍스트 (Guard/Hook/Sink에 전파)
            **kwargs: 함수 키워드 인자

        Returns:
            PolicyResult[T]: 통합 결과. 예외를 던지지 않는다.
        """
        ...


# =============================================================================
# Guard — Pre-execution Validation
# =============================================================================


@runtime_checkable
class PolicyGuard(Protocol):
    """
    Policy 실행 전 사전 검증.

    Guard 구현 시 context=None 기본 동작을 정의해야 한다:
    - KillSwitchGuard: context 무시, 전역 상태만 체크
    - ErrorBudgetGuard: tier_id=None → 글로벌 판정 (티어 무관)
    - RetryBudgetGuard: 기본 예산 기준으로 판정
    """

    @property
    def name(self) -> str:
        """Guard 식별자."""
        ...

    def check(self, context: PolicyContext | None = None) -> GuardResult:
        """
        실행 허용 여부 확인.

        Args:
            context: 실행 컨텍스트. None이면 전역 상태만 검증.

        Returns:
            GuardResult: allowed=True면 통과, False면 거부
        """
        ...


# =============================================================================
# Hook — Execution Event Observer (Fail-Open)
# =============================================================================


@runtime_checkable
class PolicyHook(Protocol):
    """
    Policy 실행 중 이벤트를 관찰하는 훅.

    Fail-Open 원칙: 훅 실패가 비즈니스 로직을 중단시키지 않는다.
    """

    def on_execute(self, policy_name: str, attempt: int) -> None:
        """실행 시작 시 호출."""
        ...

    def on_success(self, policy_name: str, result: PolicyResult) -> None:
        """실행 성공 시 호출."""
        ...

    def on_failure(self, policy_name: str, error: Exception, attempt: int) -> None:
        """실행 실패 시 호출."""
        ...

    def on_retry(self, policy_name: str, attempt: int, delay: float) -> None:
        """재시도 예정 시 호출 (마지막 실패 또는 예산 소진 시에는 미호출)."""
        ...

    def on_reject(self, policy_name: str, reason: str) -> None:
        """Policy에 의해 거부 시 호출 (CB open, Bulkhead full 등)."""
        ...


# =============================================================================
# Sink — Terminal Failure Handler
# =============================================================================


@runtime_checkable
class FailureSink(Protocol):
    """
    모든 Policy가 소진된 후 최종 실패를 처리하는 인터페이스.

    DLQ 저장, 에러 로깅, 알림 발송 등 최종 실패 처리를 수행한다.
    """

    def handle_failure(
        self,
        error: Exception,
        context: PolicyContext | None,
        policy_result: PolicyResult,
    ) -> str | None:
        """
        최종 실패 처리.

        Args:
            error: 최종 실패 예외
            context: PolicyContext (order_id, user_id 등 DLQ 저장에 필요)
            policy_result: 파이프라인 전체 결과

        Returns:
            실패 기록 ID (예: DLQ ID) 또는 None
        """
        ...
