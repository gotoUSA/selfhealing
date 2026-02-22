"""
PolicyHook implementations for CircuitBreakerPolicy.

Audit 및 EventBus 연동을 PolicyHook 인터페이스로 분리한다.
Fail-Open: 훅 실패가 비즈니스 로직을 중단시키지 않는다.

서비스 레벨 상태 전이 이벤트(OPEN→HALF_OPEN 등)는 service.py에서 처리하며,
이 모듈은 **정책 실행 수준**(request-level) 이벤트만 다룬다:
  - on_execute: CB 판정 시작
  - on_success: 함수 실행 성공
  - on_failure: 함수 실행 실패
  - on_reject: CB OPEN 거부
  - on_retry: CB는 자체 재시도 없음 (no-op)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from selfhealing.interfaces.resilience_policy import PolicyResult

logger = structlog.get_logger()


# =============================================================================
# Audit PolicyHook
# =============================================================================


class AuditPolicyHook:
    """
    CB Policy 실행 이벤트를 Audit 로그에 기록하는 PolicyHook.

    상태 전이 audit(OPEN→HALF_OPEN 등)는 service level에서 처리.
    이 훅은 요청 수준(request-level) 거부 이벤트를 audit에 기록한다.

    - on_reject: CB OPEN 거부 시 WAL 기반 audit 기록
    - on_success/on_failure: debug 레벨 기록 (상태 전이 audit는 service에서 처리)
    """

    def on_execute(self, policy_name: str, attempt: int) -> None:
        """실행 시작 — audit 대상 아님."""

    def on_success(self, policy_name: str, result: PolicyResult) -> None:
        """실행 성공 — debug 레벨 기록."""
        try:
            logger.debug(
                "audit_policy_hook.execution_succeeded",
                policy_name=policy_name,
            )
        except Exception:
            pass

    def on_failure(self, policy_name: str, error: Exception, attempt: int) -> None:
        """실행 실패 — debug 레벨 기록."""
        try:
            logger.debug(
                "audit_policy_hook.execution_failed",
                policy_name=policy_name,
                value=type(error).__name__,
                error=error,
            )
        except Exception:
            pass

    def on_retry(self, policy_name: str, attempt: int, delay: float) -> None:
        """CB는 자체 재시도하지 않음 — no-op."""

    def on_reject(self, policy_name: str, reason: str) -> None:
        """CB OPEN으로 거부 시 audit 기록."""
        try:
            from selfhealing.services.audit.cb_audit import (
                log_cb_state_change_audit,
            )

            log_cb_state_change_audit(
                cb_name=policy_name,
                old_state="open",
                new_state="open",  # 상태 변경 없음 — 거부 기록
                reason=f"request_rejected|{reason}",
            )
        except Exception as e:
            logger.debug(
                "audit_policy_hook.audit_failed",
                error=e,
            )


# =============================================================================
# EventBus PolicyHook
# =============================================================================


class EventBusPolicyHook:
    """
    CB Policy 실행 이벤트를 EventBus로 발행하는 PolicyHook.

    상태 전이 이벤트(CIRCUIT_BREAKER_OPENED 등)는 service level에서 처리.
    이 훅은 요청 수준(request-level) 이벤트만 발행한다.

    - on_reject: CIRCUIT_BREAKER_OPENED 이벤트에 request_rejected 데이터 포함
    """

    def on_execute(self, policy_name: str, attempt: int) -> None:
        """실행 시작 — EventBus 대상 아님."""

    def on_success(self, policy_name: str, result: PolicyResult) -> None:
        """실행 성공 — 상태 전이 이벤트는 service에서 처리. no-op."""

    def on_failure(self, policy_name: str, error: Exception, attempt: int) -> None:
        """실행 실패 — 상태 전이 이벤트는 service에서 처리. no-op."""

    def on_retry(self, policy_name: str, attempt: int, delay: float) -> None:
        """CB는 자체 재시도하지 않음 — no-op."""

    def on_reject(self, policy_name: str, reason: str) -> None:
        """CB OPEN 거부 이벤트 발행."""
        try:
            from selfhealing.services.event_bus import (
                EventType,
                get_event_bus,
            )

            bus = get_event_bus()
            bus.emit(
                EventType.CIRCUIT_BREAKER_OPENED,
                {
                    "service_name": policy_name,
                    "event": "request_rejected",
                    "reason": reason,
                },
                source="circuit_breaker_policy",
            )
        except Exception as e:
            logger.debug(
                "event_bus_policy_hook.event_failed",
                error=e,
            )


# =============================================================================
# Default Hook Builder
# =============================================================================


def build_default_hooks() -> list:
    """
    기본 PolicyHook 목록을 생성한다.

    AuditPolicyHook과 EventBusPolicyHook을 포함한다.
    생성 실패 시 빈 리스트를 반환한다 (Fail-Open).
    """
    hooks: list = []
    try:
        hooks.append(AuditPolicyHook())
    except Exception as e:
        logger.debug(
            "hooks.auditpolicyhook_creation_failed",
            error=e,
        )
    try:
        hooks.append(EventBusPolicyHook())
    except Exception as e:
        logger.debug(
            "hooks.eventbuspolicyhook_creation_failed",
            error=e,
        )
    return hooks
