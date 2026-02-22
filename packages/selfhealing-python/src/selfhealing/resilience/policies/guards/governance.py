"""
ThrottleGovernanceGuard — Kill Switch + Emergency + ErrorBudget + BreakGlass 통합 Guard.

AdaptiveThrottle에서 GovernanceCheckMixin 상속으로 하드코딩되어 있던
4건의 거버넌스 의존성을 단일 Guard로 통합 분리한다.

분리 대상:
    1. GovernanceCheckMixin.is_automation_allowed() — Kill Switch, Emergency,
       ErrorBudget, BreakGlass 통합 체크
    2. EmergencyMode.get_current_level() — Emergency Level 확인
    3. governance.checks.is_system_enabled() — Kill Switch Drift 교정
    4. GovernanceSettings.break_glass_enabled — Break Glass polling

PolicyComposer에서 add_guard()로 등록하여 ThrottlePolicy 실행 전
거버넌스 조건을 사전 검증한다.

Fail-Open 원칙:
    거버넌스 모듈 import/호출 실패 시 통과 허용.
    기존 KillSwitchGuard, ErrorBudgetGuard와 동일한 Fail-Open 패턴.
"""

from __future__ import annotations

import structlog

from selfhealing.interfaces.resilience_policy import (
    GuardResult,
    PolicyContext,
)

logger = structlog.get_logger()


class ThrottleGovernanceGuard:
    """
    Kill Switch + Emergency + ErrorBudget + BreakGlass 통합 가드.

    AdaptiveThrottle._sync_governance_state()와
    GovernanceCheckMixin.is_automation_allowed()를 Guard 프로토콜로 분리.

    체크 순서:
    1. Kill Switch (is_system_enabled) → 비활성이면 거부
    2. Emergency Level → LEVEL_3이면 거부
    3. Error Budget → 소진이면 거부
    4. Break Glass → 활성이면 체크 2,3 무시 (긴급 우회)

    Break Glass가 활성이면 Emergency/ErrorBudget 체크를 우회하여
    관리자가 수동으로 시스템을 강제 허용할 수 있다.
    """

    @property
    def name(self) -> str:
        """Guard 식별자."""
        return "throttle_governance"

    def check(self, context: PolicyContext | None = None) -> GuardResult:
        """
        거버넌스 사전 검증.

        Kill Switch → Emergency → ErrorBudget 순서로 체크.
        Break Glass 활성 시 Emergency/ErrorBudget 체크를 우회한다.

        Returns:
            GuardResult(allowed=True) 또는 거부 사유 포함 GuardResult
        """
        # Break Glass 확인 — 활성이면 나머지 체크 우회
        if self._is_break_glass_active():
            logger.debug(
                "[ThrottleGovernanceGuard] Break Glass active: " "bypassing governance checks",
            )
            return GuardResult(allowed=True)

        # 1. Kill Switch 체크
        kill_switch_result = self._check_kill_switch()
        if not kill_switch_result.allowed:
            return kill_switch_result

        # 2. Emergency Level 체크
        emergency_result = self._check_emergency_level()
        if not emergency_result.allowed:
            return emergency_result

        # 3. Error Budget 체크 (context 기반 tier/region 판정)
        budget_result = self._check_error_budget(context)
        if not budget_result.allowed:
            return budget_result

        return GuardResult(allowed=True)

    def _check_kill_switch(self) -> GuardResult:
        """Kill Switch 전역 상태 체크 (Fail-Open)."""
        try:
            from selfhealing.services.governance.checks import is_system_enabled

            if not is_system_enabled():
                return GuardResult(
                    allowed=False,
                    reason="kill_switch_disabled",
                )
        except ImportError:
            logger.debug(
                "[ThrottleGovernanceGuard] Kill switch module not available " "(fail-open)",
            )
        except Exception as e:
            logger.warning(
                "[ThrottleGovernanceGuard] Kill switch check failed " "(fail-open): %s",
                e,
            )
        return GuardResult(allowed=True)

    def _check_emergency_level(self) -> GuardResult:
        """Emergency Level 체크 — LEVEL_3이면 거부 (Fail-Open)."""
        try:
            from selfhealing.services.emergency_mode import get_emergency_manager

            manager = get_emergency_manager()
            level = manager.get_current_level().value

            if level >= 3:
                return GuardResult(
                    allowed=False,
                    reason=f"emergency_level_{level}",
                    metadata={"emergency_level": level},
                )
        except ImportError:
            logger.debug(
                "[ThrottleGovernanceGuard] Emergency mode module not " "available (fail-open)",
            )
        except Exception as e:
            logger.warning(
                "[ThrottleGovernanceGuard] Emergency check failed " "(fail-open): %s",
                e,
            )
        return GuardResult(allowed=True)

    def _check_error_budget(
        self,
        context: PolicyContext | None,
    ) -> GuardResult:
        """Error Budget 잔여량 체크 (Fail-Open)."""
        try:
            from selfhealing.services.error_budget_gate.gate import (
                check_automation_allowed,
            )

            tier_id = context.tier_id if context else None
            region = context.region if context else None

            gate_result = check_automation_allowed(
                tier_id=tier_id,
                region=region,
            )

            if not gate_result.allowed:
                return GuardResult(
                    allowed=False,
                    reason=gate_result.reason or "error_budget_exhausted",
                    metadata={
                        "error_budget_percent": gate_result.error_budget_percent,
                    },
                )
        except ImportError:
            logger.debug(
                "[ThrottleGovernanceGuard] ErrorBudgetGate not available " "(fail-open)",
            )
        except Exception as e:
            logger.warning(
                "[ThrottleGovernanceGuard] Error budget check failed " "(fail-open): %s",
                e,
            )
        return GuardResult(allowed=True)

    def _is_break_glass_active(self) -> bool:
        """Break Glass 활성 여부 확인 (Fail-Open → False)."""
        try:
            from selfhealing.settings.governance import get_governance_settings

            return get_governance_settings().break_glass_enabled
        except ImportError:
            logger.debug(
                "[ThrottleGovernanceGuard] Governance settings not " "available (fail-open)",
            )
        except Exception as e:
            logger.debug(
                "[ThrottleGovernanceGuard] Break glass check failed " "(fail-open): %s",
                e,
            )
        return False
