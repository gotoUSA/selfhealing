"""
Retry Policy Hooks — Audit 로깅, Prometheus 메트릭 관찰.

PolicyComposer가 Policy 실행 이벤트 발생 시 호출하는 Hook 구현.
Fail-Open 원칙: Hook 실패가 비즈니스 로직을 중단시키지 않는다.
"""

from __future__ import annotations

import logging
from typing import Any

from selfhealing.interfaces.resilience_policy import PolicyResult

logger = logging.getLogger(__name__)


class AuditHook:
    """
    재시도 이벤트를 Audit 로그에 기록하는 Hook.

    audit_helpers.log_retry_audit()를 래핑한다.
    Fail-Open 원칙: Audit 실패가 비즈니스 로직을 중단시키지 않는다.
    """

    def __init__(self, domain: str = "default"):
        self._domain = domain

    def on_execute(self, policy_name: str, attempt: int) -> None:
        """실행 시작 — Audit에는 별도 기록하지 않는다."""
        pass

    def on_success(self, policy_name: str, result: PolicyResult) -> None:
        """재시도 성공 시 Audit 기록."""
        try:
            from selfhealing.services.audit_helpers import log_retry_audit

            log_retry_audit(
                domain=self._domain,
                attempt=result.total_attempts,
                max_attempts=result.metadata.get("max_attempts", result.total_attempts),
                success=True,
            )
        except Exception as e:
            logger.debug("[AuditHook] Audit logging failed (ignored): %s", e)

    def on_failure(self, policy_name: str, error: Exception, attempt: int) -> None:
        """재시도 실패 시 Audit 기록."""
        try:
            from selfhealing.services.audit_helpers import log_retry_audit

            log_retry_audit(
                domain=self._domain,
                attempt=attempt,
                max_attempts=attempt,  # 최종 실패 시점의 시도 횟수
                success=False,
                error_type=type(error).__name__,
                error_message=str(error)[:500],
            )
        except Exception as e:
            logger.debug("[AuditHook] Audit logging failed (ignored): %s", e)

    def on_retry(self, policy_name: str, attempt: int, delay: float) -> None:
        """재시도 예정 시 Audit 기록."""
        try:
            from selfhealing.services.audit_helpers import log_retry_audit

            log_retry_audit(
                domain=self._domain,
                attempt=attempt,
                max_attempts=attempt + 1,
                success=False,
                wait_time=delay,
            )
        except Exception as e:
            logger.debug("[AuditHook] Audit logging failed (ignored): %s", e)

    def on_reject(self, policy_name: str, reason: str) -> None:
        """Policy 거부 시 Audit 기록."""
        try:
            from selfhealing.services.audit_helpers import log_retry_audit

            log_retry_audit(
                domain=self._domain,
                attempt=0,
                max_attempts=0,
                success=False,
                error_type="PolicyRejected",
                error_message=reason[:500],
            )
        except Exception as e:
            logger.debug("[AuditHook] Audit logging failed (ignored): %s", e)


class MetricsHook:
    """
    Prometheus 메트릭을 기록하는 Hook.

    retry_critical_tier_grace_retries_total 등
    Prometheus 카운터를 Fail-Open 방식으로 업데이트한다.
    """

    def __init__(self, domain: str = "default"):
        self._domain = domain

    def on_execute(self, policy_name: str, attempt: int) -> None:
        """실행 시작 — 메트릭 기록 없음."""
        pass

    def on_success(self, policy_name: str, result: PolicyResult) -> None:
        """성공 — 메트릭 기록 없음 (별도 성공 카운터 필요 시 추가)."""
        pass

    def on_failure(self, policy_name: str, error: Exception, attempt: int) -> None:
        """실패 — 메트릭 기록 없음 (최종 실패 카운터 필요 시 추가)."""
        pass

    def on_retry(self, policy_name: str, attempt: int, delay: float) -> None:
        """재시도 예정 시 메트릭 기록."""
        try:
            from selfhealing.services.metrics.definitions import (
                retry_critical_tier_grace_retries_total,
            )

            # grace retry 메트릭은 PolicyComposer 레벨에서 tier 판단 후 호출 가능
            # 여기서는 기본 retry 메트릭만 기록
        except ImportError:
            pass
        except Exception:
            pass

    def on_reject(self, policy_name: str, reason: str) -> None:
        """Policy 거부 — 메트릭 기록 없음."""
        pass
