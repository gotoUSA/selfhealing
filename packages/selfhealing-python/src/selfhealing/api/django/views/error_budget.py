"""
Error Budget API Views.

REFACTORED: 이 파일은 하위 호환성을 위해 유지됩니다.
실제 구현은 error_budget/ 패키지로 이동되었습니다.

REST API endpoints for Error Budget management and Deployment Policy.

Endpoints:
- GET  /api/self-healing/error-budget/status/           - Get Error Budget status (V3: cached)
- GET  /api/self-healing/error-budget/history/          - Get budget consumption history
- POST /api/self-healing/error-budget/record/           - Record errors (Chaos Engineering)
- POST /api/self-healing/error-budget/exhaust/          - Simulate budget exhaustion (Test)
- POST /api/self-healing/error-budget/reset-simulation/ - Reset simulation stats
- GET  /api/self-healing/deployment-policy/verdict/     - Get deployment verdict
- POST /api/self-healing/deployment-policy/acknowledge/ - Acknowledge freeze
- POST /api/self-healing/deployment-policy/override/    - Approve override
- POST /api/self-healing/deployment-policy/lift/        - Lift freeze

Core Principle: "시스템은 조언하고, 결정은 사람이 한다."
실제 CI/CD 차단 기능은 없으며, 상태 조회 및 결정 기록만 제공합니다.

FAIL-SAFE DESIGN:
- Error Budget 시스템 장애 시 → 기본값 PROCEED (fail-open)
- 배포를 막는 것보다 시스템 가용성이 더 중요
- 장애 시에도 CI/CD 파이프라인이 중단되지 않도록 보장

V3 Optimization:
- L1 In-process cache (2s TTL) + L2 Redis cache (15s TTL)
- Target: P95 < 20ms for /error-budget/status/
"""

# Re-export all from error_budget package for backward compatibility
from .error_budget import (  # Status views; Deployment policy views; Reconciliation views
    ActiveOverrideView,
    DeploymentFreezeAcknowledgeView,
    DeploymentFreezeLiftView,
    DeploymentOverrideView,
    DeploymentVerdictView,
    ErrorBudgetExhaustView,
    ErrorBudgetHistoryView,
    ErrorBudgetRecordView,
    ErrorBudgetResetSimulationView,
    ErrorBudgetStatusView,
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
