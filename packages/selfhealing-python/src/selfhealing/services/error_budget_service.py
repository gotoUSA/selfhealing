"""
Error Budget Service (Re-export Module)

SRE Error Budget 계산기 및 배포 정책 어드바이저.

.. deprecated:: 2.0.0
    Import from ``selfhealing.services.error_budget`` instead.
    This shim will be removed in v3.0.0.

⚠️ BACKWARD COMPATIBILITY:
이 파일은 기존 import 경로와의 호환성을 위해 유지됩니다.
새로운 코드에서는 다음과 같이 import하세요:

    from selfhealing.services.error_budget import (
        ErrorBudgetService,
        get_error_budget_service,
        FreezeStatus,
        OverrideType,
    )

Core Principle: "시스템은 조언하고, 결정은 사람이 한다."
이 모듈은 오직 '상태 선언'과 '권고 데이터'만 제공하며,
실제 CI/CD 차단 등의 강제 동작은 수행하지 않습니다.

Features:
- Error Budget 잔여량 계산 (SLO 기반)
- Burn Rate 계산 (Fast/Slow)
- 배포 동결 권고 (Freeze Advisor)
- 결정 기록 (Audit Trail)

Reference:
- docs/self_healing/08_OBSERVABILITY.md
- Google SRE Workbook - Alerting on SLOs
"""

from __future__ import annotations

import warnings

warnings.warn(
    "Importing from 'selfhealing.services.error_budget_service' is deprecated. "
    "Use 'selfhealing.services.error_budget' instead. "
    "This module will be removed in v3.0.0.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export all from error_budget package for backward compatibility
from selfhealing.services.error_budget import (  # Enums; Thresholds; Fail-safe; Models; Classes; Factory
    BURN_RATE_THRESHOLDS,
    ERROR_BUDGET_THRESHOLDS,
    DeploymentPolicyAdvisor,
    DeploymentVerdict,
    ErrorBudgetCalculator,
    ErrorBudgetService,
    ErrorBudgetStatus,
    FreezeDecisionRecord,
    FreezeDecisionRecorder,
    FreezeStatus,
    OverrideType,
    configure_error_budget_service,
    get_burn_rate_thresholds,
    get_error_budget_service,
    get_error_budget_thresholds,
    get_failsafe_status_response,
    get_failsafe_verdict_response,
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
