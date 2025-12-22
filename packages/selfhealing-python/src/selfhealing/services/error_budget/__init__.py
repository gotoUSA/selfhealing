"""
Error Budget Service Package

SRE Error Budget 계산기 및 배포 정책 어드바이저.

Core Principle: "시스템은 조언하고, 결정은 사람이 한다."
이 모듈은 오직 '상태 선언'과 '권고 데이터'만 제공하며,
실제 CI/CD 차단 등의 강제 동작은 수행하지 않습니다.

Features:
- Error Budget 잔여량 계산 (SLO 기반)
- Burn Rate 계산 (Fast/Slow)
- 배포 동결 권고 (Freeze Advisor)
- 결정 기록 (Audit Trail)

Usage:
    from selfhealing.services.error_budget import (
        ErrorBudgetService,
        get_error_budget_service,
        configure_error_budget_service,
    )

Reference:
- docs/self_healing/08_OBSERVABILITY.md
- Google SRE Workbook - Alerting on SLOs
"""

from selfhealing.services.error_budget.enums import (
    FreezeStatus,
    OverrideType,
    ERROR_BUDGET_THRESHOLDS,
    BURN_RATE_THRESHOLDS,
    get_error_budget_thresholds,
    get_burn_rate_thresholds,
    get_failsafe_verdict_response,
    get_failsafe_status_response,
)

from selfhealing.services.error_budget.models import (
    ErrorBudgetStatus,
    DeploymentVerdict,
    FreezeDecisionRecord,
)

from selfhealing.services.error_budget.calculator import (
    ErrorBudgetCalculator,
)

from selfhealing.services.error_budget.advisor import (
    DeploymentPolicyAdvisor,
)

from selfhealing.services.error_budget.recorder import (
    FreezeDecisionRecorder,
)

from selfhealing.services.error_budget.service import (
    ErrorBudgetService,
    get_error_budget_service,
    configure_error_budget_service,
)


__all__ = [
    # Enums
    "FreezeStatus",
    "OverrideType",
    # Thresholds
    "ERROR_BUDGET_THRESHOLDS",
    "BURN_RATE_THRESHOLDS",
    "get_error_budget_thresholds",
    "get_burn_rate_thresholds",
    # Fail-safe
    "get_failsafe_verdict_response",
    "get_failsafe_status_response",
    # Models
    "ErrorBudgetStatus",
    "DeploymentVerdict",
    "FreezeDecisionRecord",
    # Classes
    "ErrorBudgetCalculator",
    "DeploymentPolicyAdvisor",
    "FreezeDecisionRecorder",
    "ErrorBudgetService",
    # Factory
    "get_error_budget_service",
    "configure_error_budget_service",
]
