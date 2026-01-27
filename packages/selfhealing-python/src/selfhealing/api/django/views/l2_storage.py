"""
L2 Storage Resilience API Views (Re-export Module).

REST API endpoints for L2 storage configuration and monitoring.

⚠️ BACKWARD COMPATIBILITY:
이 파일은 기존 import 경로와의 호환성을 위해 유지됩니다.
새로운 코드에서는 개별 모듈에서 직접 import하세요:

    from selfhealing.api.django.views.l2_storage_config import L2StorageConfigView
    from selfhealing.api.django.views.l2_storage_status import L2StorageStatusView
    from selfhealing.api.django.views.l2_storage_shadow_log import ShadowLogListView
    from selfhealing.api.django.views.l2_storage_drift import DriftReconciliationTriggerView

Endpoints:
- GET  /api/self-healing/l2-storage/config/      - Get L2 storage config
- PUT  /api/self-healing/l2-storage/config/      - Update L2 storage config
- POST /api/self-healing/l2-storage/config/reset - Reset config to defaults
- GET  /api/self-healing/l2-storage/status/      - Get L2 storage status
- GET  /api/self-healing/l2-storage/health/      - Get L2 health status
- POST /api/self-healing/l2-storage/health/reset - Reset L2 health status
- GET  /api/self-healing/l2-storage/shadow-log/  - Get shadow log entries
- GET  /api/self-healing/l2-storage/shadow-log/stats/ - Get shadow log stats
- POST /api/self-healing/l2-storage/shadow-log/clear/ - Clear shadow log
- POST /api/self-healing/l2-storage/sync/from-l2 - Force sync from L2
- POST /api/self-healing/l2-storage/sync/to-l2   - Force sync to L2
- GET  /api/self-healing/l2-storage/drift/stats/ - Get drift reconciliation stats
- GET  /api/self-healing/l2-storage/drift/history/ - Get drift reconciliation history
- POST /api/self-healing/l2-storage/drift/reconcile/ - Force drift reconciliation
- POST /api/self-healing/l2-storage/drift/reconcile/<service_name>/ - Reconcile single service
"""

# Re-export from config module
from selfhealing.api.django.views.l2_storage_config import (
    L2StorageConfigResetView,
    L2StorageConfigView,
)

# Re-export from drift module
from selfhealing.api.django.views.l2_storage_drift import (
    DriftReconciliationHistoryView,
    DriftReconciliationServiceView,
    DriftReconciliationStatsView,
    DriftReconciliationTriggerView,
)

# Re-export from shadow log module
from selfhealing.api.django.views.l2_storage_shadow_log import (
    ShadowLogAnalyzeView,
    ShadowLogByServiceView,
    ShadowLogClearView,
    ShadowLogListView,
    ShadowLogReplayView,
    ShadowLogStatsView,
)

# Re-export from status module
from selfhealing.api.django.views.l2_storage_status import (
    L2StorageHealthResetView,
    L2StorageHealthView,
    L2StorageMetricsView,
    L2StorageStatusView,
    L2StorageSyncFromL2View,
    L2StorageSyncToL2View,
)

__all__ = [
    # Config
    "L2StorageConfigView",
    "L2StorageConfigResetView",
    # Status
    "L2StorageStatusView",
    "L2StorageHealthView",
    "L2StorageHealthResetView",
    "L2StorageSyncFromL2View",
    "L2StorageSyncToL2View",
    "L2StorageMetricsView",
    # Shadow Log
    "ShadowLogListView",
    "ShadowLogStatsView",
    "ShadowLogClearView",
    "ShadowLogAnalyzeView",
    "ShadowLogReplayView",
    "ShadowLogByServiceView",
    # Drift Reconciliation
    "DriftReconciliationStatsView",
    "DriftReconciliationHistoryView",
    "DriftReconciliationTriggerView",
    "DriftReconciliationServiceView",
]
