"""
Retry Policy Guards — Kill Switch, Error Budget 사전 검증.

PolicyComposer가 Policy 실행 전에 호출하는 사전 검증 구현.
Guard.check()가 allowed=False를 반환하면 Policy 실행을 차단한다.
"""

from __future__ import annotations

import logging
from typing import Any

from selfhealing.interfaces.resilience_policy import GuardResult, PolicyContext

logger = logging.getLogger(__name__)


class KillSwitchGuard:
    """
    글로벌 Kill Switch 사전 검증.

    SystemControlManager.is_enabled()를 호출하여 셀프힐링 시스템이
    활성화 상태인지 확인한다. 비활성화 시 모든 Policy 실행을 차단한다.
    """

    @property
    def name(self) -> str:
        return "kill_switch"

    def check(self, context: PolicyContext | None = None) -> GuardResult:
        """Kill Switch 활성화 여부 확인. context는 사용하지 않는다."""
        try:
            from selfhealing.services.system_control import SystemControlManager

            manager = SystemControlManager()
            if manager.is_enabled():
                return GuardResult(allowed=True)
            else:
                return GuardResult(
                    allowed=False,
                    reason="Kill Switch is active: self-healing system is disabled",
                )
        except Exception as e:
            # Fail-Open: SystemControlManager 로드 실패 시 통과
            logger.debug("[KillSwitchGuard] SystemControlManager not available: %s", e)
            return GuardResult(allowed=True)


class ErrorBudgetGuard:
    """
    에러 예산 게이트 사전 검증.

    check_automation_allowed()를 호출하여 에러 예산이 임계치 이상인지 확인한다.
    에러 예산이 부족하면 Policy 실행을 차단한다.
    """

    @property
    def name(self) -> str:
        return "error_budget"

    def check(self, context: PolicyContext | None = None) -> GuardResult:
        """에러 예산 잔여율 확인. context에서 tier_id, region을 추출한다."""
        try:
            from selfhealing.services.error_budget_gate import check_automation_allowed

            tier_id = context.tier_id if context else None
            region = context.region if context else None

            gate_result = check_automation_allowed(
                tier_id=tier_id,
                region=region,
            )

            if gate_result.allowed:
                return GuardResult(
                    allowed=True,
                    metadata={
                        "error_budget_percent": gate_result.error_budget_percent,
                    },
                )
            else:
                return GuardResult(
                    allowed=False,
                    reason=(
                        f"Error budget critically low "
                        f"({gate_result.error_budget_percent:.1f}%): "
                        f"retry blocked to prevent further errors"
                    ),
                    metadata={
                        "error_budget_percent": gate_result.error_budget_percent,
                        "threshold_percent": gate_result.threshold_percent,
                    },
                )
        except ImportError:
            # ErrorBudgetGate 모듈이 없으면 Fail-Open
            return GuardResult(allowed=True)
        except Exception as e:
            # Fail-Open: 게이트 체크 실패 시 통과
            logger.warning("[ErrorBudgetGuard] Gate check failed: %s", e)
            return GuardResult(allowed=True)
