"""
Policy Guards — 사전 검증 모듈.

PolicyComposer 파이프라인 실행 전 전역/티어별 조건을 검증하는
Guard 구현체를 제공한다.

- KillSwitchGuard: 시스템 전역 활성/비활성 체크
- ErrorBudgetGuard: 에러 버짓 잔여량 체크
"""

from selfhealing.resilience.policies.guards.error_budget import ErrorBudgetGuard
from selfhealing.resilience.policies.guards.kill_switch import KillSwitchGuard

__all__ = [
    "ErrorBudgetGuard",
    "KillSwitchGuard",
]
