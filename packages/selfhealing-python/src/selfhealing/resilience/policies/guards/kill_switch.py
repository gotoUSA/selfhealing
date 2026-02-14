"""
Kill Switch Guard — 시스템 전역 활성/비활성 상태 검증.

SystemControlManager의 is_enabled()를 래핑하여
PolicyComposer 파이프라인 실행 전 전역 차단 여부를 확인한다.

Fail-Open 원칙: SystemControlManager import/호출 실패 시 통과 허용.
"""

from __future__ import annotations

import logging

from selfhealing.interfaces.resilience_policy import (
    GuardResult,
    PolicyContext,
)

logger = logging.getLogger(__name__)


class KillSwitchGuard:
    """Kill Switch 가드 — SystemControlManager 래핑.

    전역 상태만 체크하며 context는 무시한다.
    SystemControlManager.is_enabled()가 False이면 실행을 거부한다.
    """

    @property
    def name(self) -> str:
        """Guard 식별자."""
        return "kill_switch"

    def check(self, context: PolicyContext | None = None) -> GuardResult:
        """
        Kill Switch 전역 상태 체크.

        context는 무시한다 (전역 on/off만 판정).
        SystemControlManager import/호출 실패 시 Fail-Open (통과 허용).
        """
        try:
            from selfhealing.services.system_control import get_system_control

            mgr = get_system_control()
            if not mgr.is_enabled():
                return GuardResult(
                    allowed=False,
                    reason="System kill switch is disabled",
                )
        except ImportError:
            logger.debug("SystemControlManager not available (fail-open)")
        except Exception as e:
            logger.warning("KillSwitchGuard check failed (fail-open): %s", e)

        return GuardResult(allowed=True)
