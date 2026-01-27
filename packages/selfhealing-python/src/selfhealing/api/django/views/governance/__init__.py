"""
Governance API Views Package - 통합 거버넌스 허브.

API 구조:
- GET /api/self-healing/metrics/status/ - 통합 상태 조회 (Observability)
- POST /api/self-healing/governance/reconcile/ - 수동 정합성 조정 (Control)
- POST /api/self-healing/governance/mode/ - 운영 모드 강제 전환 (Control)
- GET /api/self-healing/governance/status/ - RBAC 상태 조회
- GET/PUT /api/self-healing/config/governance/ - 거버넌스 설정

Design Philosophy:
- 관찰(Observability)과 제어(Control) 분리
- 엔드포인트 파편화 방지
"""

# Approval Views (4-Eyes)
from selfhealing.api.django.views.governance.approval_views import (
    ApprovalRequestApproveView,
    ApprovalRequestListView,
    ApprovalRequestRejectView,
)

# Config Views
from selfhealing.api.django.views.governance.config_views import (
    GovernanceConfigView,
    L2StorageConfigManagedView,
)

# Control Views
from selfhealing.api.django.views.governance.control_views import (
    GovernanceModeView,
    GovernanceReconcileView,
)

# Status Views (Observability)
from selfhealing.api.django.views.governance.status_views import (
    GovernanceRBACStatusView,
    MetricStatusView,
)

# Service Layer
from selfhealing.services.governance_api_service import (
    GovernanceApiService,
    get_governance_api_service,
    reset_governance_api_service,
)

__all__ = [
    # Service
    "GovernanceApiService",
    "get_governance_api_service",
    "reset_governance_api_service",
    # API Views
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
]
