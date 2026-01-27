"""
Error Budget API Views Package.

REST API endpoints for Error Budget management and Deployment Policy.

REFACTORED: 이 패키지는 기존 error_budget.py (1179줄)를 3개 모듈로 분리했습니다.

Modules:
- status.py: Error Budget 상태 조회/기록 (5개 View)
- deployment.py: 배포 정책 결정 기록 (5개 View)
- reconciliation.py: Shadow Budget 관리 (9개 View)

Endpoints:
- GET  /api/self-healing/error-budget/status/
- GET  /api/self-healing/error-budget/history/
- POST /api/self-healing/error-budget/record/
- POST /api/self-healing/error-budget/exhaust/
- POST /api/self-healing/error-budget/reset-simulation/
- GET  /api/self-healing/deployment-policy/verdict/
- POST /api/self-healing/deployment-policy/acknowledge/
- POST /api/self-healing/deployment-policy/override/
- POST /api/self-healing/deployment-policy/lift/
- GET  /api/self-healing/deployment-policy/active-override/
- GET  /api/self-healing/reconciliation/status/
- GET  /api/self-healing/reconciliation/failsafe-periods/
- GET/POST /api/self-healing/reconciliation/shadow-budgets/
- POST /api/self-healing/reconciliation/shadow-budgets/{id}/approve/
- POST /api/self-healing/reconciliation/shadow-budgets/{id}/reject/
- GET/POST /api/self-healing/reconciliation/excluded-periods/
- DELETE /api/self-healing/reconciliation/excluded-periods/{id}/
- GET/PUT /api/self-healing/reconciliation/config/

Core Principle: "시스템은 조언하고, 결정은 사람이 한다."
FAIL-SAFE DESIGN: 시스템 장애 시 → 기본값 PROCEED (fail-open)
"""

# Deployment policy views
from .deployment import (
    ActiveOverrideView,
    DeploymentFreezeAcknowledgeView,
    DeploymentFreezeLiftView,
    DeploymentOverrideView,
    DeploymentVerdictView,
)

# Reconciliation views
from .reconciliation import (
    ExcludedPeriodDetailView,
    ExcludedPeriodsView,
    FailSafePeriodsView,
    ReconciliationConfigView,
    ReconciliationStatusView,
    ShadowBudgetApproveView,
    ShadowBudgetDetailView,
    ShadowBudgetRejectView,
    ShadowBudgetsView,
)

# Status views
from .status import (
    ErrorBudgetExhaustView,
    ErrorBudgetHistoryView,
    ErrorBudgetRecordView,
    ErrorBudgetResetSimulationView,
    ErrorBudgetStatusView,
)

__all__ = [
    # Status views
    "ErrorBudgetStatusView",
    "ErrorBudgetHistoryView",
    "ErrorBudgetRecordView",
    "ErrorBudgetExhaustView",
    "ErrorBudgetResetSimulationView",
    # Deployment policy views
    "DeploymentVerdictView",
    "DeploymentFreezeAcknowledgeView",
    "DeploymentOverrideView",
    "DeploymentFreezeLiftView",
    "ActiveOverrideView",
    # Reconciliation views
    "ReconciliationStatusView",
    "FailSafePeriodsView",
    "ShadowBudgetsView",
    "ShadowBudgetDetailView",
    "ShadowBudgetApproveView",
    "ShadowBudgetRejectView",
    "ExcludedPeriodsView",
    "ExcludedPeriodDetailView",
    "ReconciliationConfigView",
]
