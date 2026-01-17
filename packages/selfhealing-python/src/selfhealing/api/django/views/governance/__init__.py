"""
Governance API Views Package - 통합 거버넌스 허브.

새로운 API 구조:
- GET /api/self-healing/metrics/status/ - 통합 상태 조회 (Observability)
- POST /api/self-healing/governance/reconcile/ - 수동 정합성 조정 (Control)
- POST /api/self-healing/governance/mode/ - 운영 모드 강제 전환 (Control)
- GET /api/self-healing/governance/status/ - RBAC 상태 조회
- GET/PUT /api/self-healing/config/governance/ - 거버넌스 설정

기존 API (Deprecated):
- POST /api/self-healing/metrics/sync/ → /governance/reconcile/ (308 Redirect)
- GET /api/self-healing/metrics/drift-report/ → /metrics/status/ (308 Redirect)

Design Philosophy:
- 관찰(Observability)과 제어(Control) 분리
- 엔드포인트 파편화 방지
- Deprecated API는 Warning 헤더 + 리다이렉트로 하위 호환성 유지
"""

# Service Layer
from selfhealing.api.django.views.governance.service import (
    GovernanceService,
    get_governance_service,
    reset_governance_service,
)

# Status Views (Observability)
from selfhealing.api.django.views.governance.status_views import (
    MetricStatusView,
    GovernanceRBACStatusView,
)

# Control Views
from selfhealing.api.django.views.governance.control_views import (
    GovernanceReconcileView,
    GovernanceModeView,
)

# Config Views
from selfhealing.api.django.views.governance.config_views import (
    GovernanceConfigView,
    L2StorageConfigManagedView,
)

# Approval Views (4-Eyes)
from selfhealing.api.django.views.governance.approval_views import (
    ApprovalRequestListView,
    ApprovalRequestApproveView,
    ApprovalRequestRejectView,
)

# Deprecated Views
from selfhealing.api.django.views.governance.deprecated_views import (
    DeprecatedMetricSyncView,
    DeprecatedDriftReportView,
)


__all__ = [
    # Service
    "GovernanceService",
    "get_governance_service",
    "reset_governance_service",
    # New API Views
    "MetricStatusView",
    "GovernanceReconcileView",
    "GovernanceModeView",
    # RBAC API Views
    "GovernanceRBACStatusView",
    "GovernanceConfigView",
    # 4-Eyes Approval API Views
    "ApprovalRequestListView",
    "ApprovalRequestApproveView",
    "ApprovalRequestRejectView",
    "L2StorageConfigManagedView",
    # Deprecated Views
    "DeprecatedMetricSyncView",
    "DeprecatedDriftReportView",
]
