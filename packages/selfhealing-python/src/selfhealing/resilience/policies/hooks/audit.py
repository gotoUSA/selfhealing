"""
Audit Hook — 파이프라인 실행 결과를 감사 로그에 기록.

PolicyComposer의 Hook으로 등록하여 파이프라인 전체 결과를
감사 로깅한다. 개별 Policy 내부 이벤트는 관찰하지 않는다
(2계층 Hook 구조).

Fail-Open 원칙: audit 서비스 import/호출 실패 시 로깅만 하고
비즈니스 로직에 영향을 주지 않는다.
"""

from __future__ import annotations

import structlog

from selfhealing.interfaces.resilience_policy import PolicyResult

logger = structlog.get_logger()


class AuditHook:
    """감사 로깅 훅.

    파이프라인 전체(End-to-End) 결과만 관찰한다.
    개별 Policy 내부 이벤트(Retry 각 시도 등)는 Policy가 처리하며,
    이 Hook에는 전파하지 않는다.
    """

    def on_execute(self, policy_name: str, attempt: int) -> None:
        """실행 시작 시 호출."""
        logger.debug(
            "[AuditHook] Pipeline execution started: policy=%s, attempt=%d",
            policy_name,
            attempt,
        )

    def on_success(self, policy_name: str, result: PolicyResult) -> None:
        """파이프라인 성공 시 호출."""
        logger.info(
            "[AuditHook] Pipeline succeeded: policies=%s, attempts=%d, duration_ms=%.2f",
            result.executed_policies,
            result.total_attempts,
            result.total_duration_ms,
        )

    def on_failure(self, policy_name: str, error: Exception, attempt: int) -> None:
        """파이프라인 실패 시 호출."""
        logger.warning(
            "[AuditHook] Pipeline failed: error=%s, attempts=%d",
            error,
            attempt,
        )

    def on_retry(self, policy_name: str, attempt: int, delay: float) -> None:
        """재시도 예정 시 호출."""
        logger.info(
            "[AuditHook] Retry scheduled: policy=%s, attempt=%d, delay=%.2fs",
            policy_name,
            attempt,
            delay,
        )

    def on_reject(self, policy_name: str, reason: str) -> None:
        """파이프라인 거부 시 호출."""
        logger.warning(
            "[AuditHook] Pipeline rejected: guard=%s, reason=%s",
            policy_name,
            reason,
        )
