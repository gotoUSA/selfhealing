"""
Error Budget Gate - 에러 예산 기반 자동화 제어 게이트

"위기 상황일수록 인간의 개입을 강제하는 설계"

에러 예산이 임계값 미만일 때 모든 자동화 기능을 중단하고
수동 모드로 강제 전환합니다.

핵심 원칙:
- 시스템이 "대신 하는 것"이 아니라 "멈추는 것"
- 판단을 대체하지 않고, 위험할 때 보호
- Fail-Open: 게이트 자체 장애 시 자동화 허용 (단, 경고 발생)

Usage:
    from selfhealing.services.error_budget_gate import (
        check_automation_allowed,
        require_automation_allowed,
        AutomationBlockedError,
    )

    # 방법 1: 조건 체크
    result = check_automation_allowed()
    if not result.allowed:
        logger.warning(f"Automation blocked: {result.reason}")
        return  # 자동화 중단

    # 방법 2: 예외 발생 (권장)
    try:
        require_automation_allowed(action="dlq_auto_replay")
    except AutomationBlockedError as e:
        # 수동 처리로 전환
        notify_operator(e.message)
        return

Reference:
- docs/self_healing/12_ERROR_BUDGET.md
- 설계 철학: "보고는 자동, 결정은 수동"
"""

from selfhealing.services.error_budget_gate.alert_manager import (
    GateAlertManager,
)

# Configuration
from selfhealing.services.error_budget_gate.config import (
    ErrorBudgetGateConfig,
    GateCheckResult,
    GateStatus,
)

# Exceptions
from selfhealing.services.error_budget_gate.exceptions import (
    AutomationBlockedError,
)
from selfhealing.services.error_budget_gate.fault_detector import (  # Deprecated aliases는 __getattr__을 통해 lazy load됨
    GateFaultDetector,
    GateFaultState,
)

# Gate & Convenience Functions
from selfhealing.services.error_budget_gate.gate import (
    ErrorBudgetGate,
    automation_gate,
    check_automation_allowed,
    get_error_budget_gate,
    is_automation_allowed,
    require_automation_allowed,
)

# Components
from selfhealing.services.error_budget_gate.rate_limiter import (
    InMemoryRateLimiter,
)

# =============================================================================
# Deprecated 별칭 - __getattr__ 패턴으로 DeprecationWarning 발생
# =============================================================================

_DEPRECATED_ALIASES = {
    "CircuitState": "GateFaultState",
    "InMemoryCircuitBreaker": "GateFaultDetector",
}

_deprecated_warned_init: set = set()


def __getattr__(name: str):
    """
    Deprecated 별칭 접근 시 DeprecationWarning 발생.

    .. deprecated:: 2.0.0
        CircuitState -> GateFaultState
        InMemoryCircuitBreaker -> GateFaultDetector
        Will be removed in version 3.0.0.
    """
    import warnings

    from selfhealing.services.error_budget_gate import fault_detector

    if name in _DEPRECATED_ALIASES:
        new_name = _DEPRECATED_ALIASES[name]
        if name not in _deprecated_warned_init:
            warnings.warn(
                f"'{name}' is deprecated. Use '{new_name}' instead. "
                f"This alias will be removed in v3.0.0.",
                DeprecationWarning,
                stacklevel=2,
            )
            _deprecated_warned_init.add(name)
        return getattr(fault_detector, new_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Configuration
    "ErrorBudgetGateConfig",
    "GateStatus",
    "GateCheckResult",
    # Components
    "InMemoryRateLimiter",
    "GateFaultState",
    "GateFaultDetector",
    "GateAlertManager",
    # Deprecated aliases (backward compatibility)
    "CircuitState",
    "InMemoryCircuitBreaker",
    # Exceptions
    "AutomationBlockedError",
    # Main Gate
    "ErrorBudgetGate",
    "get_error_budget_gate",
    # Convenience Functions
    "check_automation_allowed",
    "require_automation_allowed",
    "is_automation_allowed",
    # Decorator
    "automation_gate",
]
