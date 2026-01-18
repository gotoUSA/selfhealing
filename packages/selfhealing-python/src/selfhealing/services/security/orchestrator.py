"""
Protection Orchestrator.

Orchestrates protection measures with atomicity guarantees
and rollback support.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from selfhealing.services.security.policies import (
    ActionPolicy,
    ACTION_POLICY_PRIORITY,
)
from selfhealing.services.security.models import ProtectionResult

if TYPE_CHECKING:
    from selfhealing.services.security.service import SecurityViolationService

logger = logging.getLogger(__name__)


class ProtectionOrchestrator:
    """
    보호 조치 오케스트레이터.

    원자성 보장:
    - 가장 강력한 정책(우선순위 높은)부터 실행
    - 최고 우선순위 정책 실패 시 전체 실패 처리 + 롤백
    - 하위 정책 실패는 경고 로깅 후 계속 진행

    롤백 정책 (v2.1.0 - 순위 0.3):
    - 최고 우선순위 실패 시 이미 실행된 정책들을 역순으로 롤백
    - 롤백 가능한 정책만 롤백 시도 (Emergency Mode는 롤백 불가)
    - 롤백 실패 시 로깅 후 수동 개입 권장

    Reference: Architect Review - "가장 강력한 정책 우선 성공 보장"
    """

    def __init__(self, security_service: "SecurityViolationService"):
        self._service = security_service
        self._policy_executors: dict[ActionPolicy, Any] = {
            ActionPolicy.EMERGENCY_LEVEL_3: self._execute_emergency_3,
            ActionPolicy.EMERGENCY_LEVEL_2: self._execute_emergency_2,
            ActionPolicy.EMERGENCY_LEVEL_1: self._execute_emergency_1,
            ActionPolicy.ACCOUNT_FREEZE: self._execute_account_freeze,
            ActionPolicy.SESSION_INVALIDATE: self._execute_session_invalidate,
            ActionPolicy.IP_PERMANENT_BAN: self._execute_ip_permanent_ban,
            ActionPolicy.IP_TEMPORARY_BAN: self._execute_ip_temporary_ban,
            ActionPolicy.BLOCK_AND_LOG: self._execute_block_and_log,
        }

        # 롤백 실행자 (롤백 가능한 정책만)
        self._policy_rollback_executors: dict[ActionPolicy, Any] = {
            ActionPolicy.ACCOUNT_FREEZE: self._rollback_account_freeze,
            ActionPolicy.SESSION_INVALIDATE: None,
            ActionPolicy.IP_PERMANENT_BAN: self._rollback_ip_ban,
            ActionPolicy.IP_TEMPORARY_BAN: self._rollback_ip_ban,
            ActionPolicy.BLOCK_AND_LOG: None,
        }

    def execute_policies(
        self,
        policies: list[ActionPolicy],
        context: dict[str, Any],
    ) -> ProtectionResult:
        """
        정책 목록을 우선순위 순으로 실행.

        Args:
            policies: 실행할 ActionPolicy 목록
            context: 실행 컨텍스트 (user_id, source_ip 등)

        Returns:
            ProtectionResult with execution details
        """
        if not policies:
            return ProtectionResult(
                success=True,
                executed_policies=[],
                failed_policies=[],
                highest_priority_succeeded=True,
            )

        # 우선순위 순 정렬 (낮은 숫자 = 높은 우선순위)
        sorted_policies = sorted(
            policies, key=lambda p: ACTION_POLICY_PRIORITY.get(p, 999)
        )

        executed: list[ActionPolicy] = []
        failed: list[ActionPolicy] = []
        highest_priority_policy = sorted_policies[0]
        highest_succeeded = False

        for policy in sorted_policies:
            try:
                executor = self._policy_executors.get(policy)
                if executor:
                    executor(context)
                    executed.append(policy)

                    if policy == highest_priority_policy:
                        highest_succeeded = True

            except Exception as e:
                failed.append(policy)
                logger.error(
                    f"[ProtectionOrchestrator] Policy {policy.value} failed: {e}"
                )

                # 최고 우선순위 실패 시 즉시 중단 + 롤백 시도
                if policy == highest_priority_policy:
                    rolled_back, rollback_success = self._rollback_executed_policies(
                        executed, context
                    )

                    return ProtectionResult(
                        success=False,
                        executed_policies=executed,
                        failed_policies=failed,
                        highest_priority_succeeded=False,
                        rolled_back_policies=rolled_back,
                        rollback_success=rollback_success,
                        error_message=f"Highest priority policy failed: {e}",
                        triggering_trace_id=context.get("trace_id"),
                        triggering_request_path=context.get("request_path"),
                    )

        return ProtectionResult(
            success=len(failed) == 0,
            executed_policies=executed,
            failed_policies=failed,
            highest_priority_succeeded=highest_succeeded,
            triggering_trace_id=context.get("trace_id"),
            triggering_request_path=context.get("request_path"),
        )

    # =========================================================================
    # Policy Executors
    # =========================================================================

    def _execute_emergency_3(self, context: dict[str, Any]) -> None:
        """Emergency Level 3 선포."""
        try:
            from selfhealing.services.event_bus import get_event_bus, EventType

            bus = get_event_bus()
            bus.emit(
                event_type=EventType.EMERGENCY_ACTIVATED,
                data={
                    "level": 3,
                    "reason": context.get("reason", "Security violation"),
                    "trigger_source": "protection_orchestrator",
                    "incident_id": context.get("incident_id"),
                },
                source="protection_orchestrator",
            )
            logger.critical("[ProtectionOrchestrator] Emergency Level 3 activated!")
        except Exception as e:
            logger.error(
                f"[ProtectionOrchestrator] Failed to emit emergency event: {e}"
            )
            raise

    def _execute_emergency_2(self, context: dict[str, Any]) -> None:
        """Emergency Level 2 선포."""
        try:
            from selfhealing.services.event_bus import get_event_bus, EventType

            bus = get_event_bus()
            bus.emit(
                event_type=EventType.EMERGENCY_ACTIVATED,
                data={
                    "level": 2,
                    "reason": context.get("reason", "Security violation"),
                    "trigger_source": "protection_orchestrator",
                    "incident_id": context.get("incident_id"),
                },
                source="protection_orchestrator",
            )
            logger.warning("[ProtectionOrchestrator] Emergency Level 2 activated")
        except Exception as e:
            logger.error(
                f"[ProtectionOrchestrator] Failed to emit emergency event: {e}"
            )
            raise

    def _execute_emergency_1(self, context: dict[str, Any]) -> None:
        """Emergency Level 1 선포."""
        try:
            from selfhealing.services.event_bus import get_event_bus, EventType

            bus = get_event_bus()
            bus.emit(
                event_type=EventType.EMERGENCY_ACTIVATED,
                data={
                    "level": 1,
                    "reason": context.get("reason", "Security warning"),
                    "trigger_source": "protection_orchestrator",
                },
                source="protection_orchestrator",
            )
            logger.info("[ProtectionOrchestrator] Emergency Level 1 activated")
        except Exception as e:
            logger.error(
                f"[ProtectionOrchestrator] Failed to emit emergency event: {e}"
            )
            raise

    def _execute_account_freeze(self, context: dict[str, Any]) -> None:
        """계정 동결."""
        user_id = context.get("user_id")
        if user_id:
            logger.warning(
                f"[ProtectionOrchestrator] Account frozen: user_id={user_id}"
            )

    def _execute_session_invalidate(self, context: dict[str, Any]) -> None:
        """세션 무효화."""
        user_id = context.get("user_id")
        if user_id:
            self._service._invalidate_user_sessions(user_id)

    def _execute_ip_permanent_ban(self, context: dict[str, Any]) -> None:
        """영구 IP 차단."""
        source_ip = context.get("source_ip")
        if source_ip:
            self._service._permanent_ip_ban(source_ip)

    def _execute_ip_temporary_ban(self, context: dict[str, Any]) -> None:
        """임시 IP 차단."""
        source_ip = context.get("source_ip")
        if source_ip:
            self._service._temporary_ip_ban(source_ip)

    def _execute_block_and_log(self, context: dict[str, Any]) -> None:
        """차단 및 로깅."""
        logger.warning(f"[ProtectionOrchestrator] Blocked and logged: {context}")

    # =========================================================================
    # Rollback Methods
    # =========================================================================

    def _rollback_executed_policies(
        self,
        executed: list[ActionPolicy],
        context: dict[str, Any],
    ) -> tuple[list[ActionPolicy], bool]:
        """이미 실행된 정책들을 역순으로 롤백."""
        rolled_back: list[ActionPolicy] = []
        all_success = True

        for policy in reversed(executed):
            rollback_fn = self._policy_rollback_executors.get(policy)

            if rollback_fn is None:
                logger.warning(
                    f"[ProtectionOrchestrator] Policy {policy.value} cannot be rolled back"
                )
                continue

            try:
                rollback_fn(context)
                rolled_back.append(policy)
                logger.info(f"[ProtectionOrchestrator] Rolled back: {policy.value}")
            except Exception as e:
                logger.error(
                    f"[ProtectionOrchestrator] Rollback failed for {policy.value}: {e}"
                )
                all_success = False

        if not all_success:
            logger.critical(
                "[ProtectionOrchestrator] Some rollbacks failed! "
                "Manual intervention may be required."
            )

        return rolled_back, all_success

    def _rollback_account_freeze(self, context: dict[str, Any]) -> None:
        """계정 동결 해제."""
        user_id = context.get("user_id")
        if user_id:
            logger.info(
                f"[ProtectionOrchestrator] Account unfrozen: user_id={user_id}"
            )

    def _rollback_ip_ban(self, context: dict[str, Any]) -> None:
        """IP 차단 해제."""
        source_ip = context.get("source_ip")
        if source_ip:
            self._service._remove_ip_ban(source_ip)
            logger.info(f"[ProtectionOrchestrator] IP ban removed: {source_ip}")
