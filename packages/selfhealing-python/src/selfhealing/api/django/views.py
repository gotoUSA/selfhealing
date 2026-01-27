"""
Self-Healing Control API Views.

REST API endpoints for the Self-Healing Control API.

This module re-exports all views from the views/ package for backward compatibility.
The actual implementation has been split into:
- views/circuit_breaker.py: Circuit Breaker control views (~563 lines)
- views/dlq.py: DLQ management views (~82 lines)
- views/health.py: Health check and metrics views (~195 lines)

Endpoints:
- POST /api/self-healing/control/ - Execute control action
- GET  /api/self-healing/status/ - Get all service states
- GET  /api/self-healing/status/{service_name}/ - Get specific service state
- GET  /api/self-healing/audit/ - Get audit logs
- POST /api/self-healing/allow/{service_name}/ - Quick allow
- POST /api/self-healing/block/{service_name}/ - Quick block
- POST /api/self-healing/reset/{service_name}/ - Quick reset
- GET  /api/self-healing/health/ - Health check
- GET  /api/self-healing/metrics/ - Get metrics
- POST /api/self-healing/dlq/replay/ - Trigger DLQ replay
"""

# Re-export all views from the views/ package for backward compatibility
# Import from circuit_breaker module
from selfhealing.api.django.views.circuit_breaker import (  # Data classes; Service; Control Views; Quick Action Views
    ControlActionView,
    ControlAPIService,
    ControlAuditView,
    ControlRequest,
    ControlResponse,
    ControlStatusView,
    QuickAllowView,
    QuickBlockView,
    QuickResetView,
    ServiceStatusView,
    get_control_api_service,
)

# Import from dashboard module
from selfhealing.api.django.views.dashboard import (
    DashboardSummaryView,
)

# Import from dlq module
from selfhealing.api.django.views.dlq import (
    DLQArchiveView,
    DLQCleanupStatsView,
    DLQDetailView,
    DLQListView,
    DLQPurgeView,
    DLQReplayView,
    DLQResolveView,
    DLQRetryView,
)

# Import from health module
from selfhealing.api.django.views.health import (
    ConnectionPoolHealthView,
    LivenessView,
    ReadinessView,
    SelfHealingHealthView,
    SelfHealingMetricsView,
    simple_health_ping,
)

# Import from metric_sync module
from selfhealing.api.django.views.metric_sync import (
    DriftReportView,
    MetricSyncView,
)

__all__ = [
    # Data classes
    "ControlRequest",
    "ControlResponse",
    # Service
    "ControlAPIService",
    "get_control_api_service",
    # Control Views
    "ControlActionView",
    "ControlStatusView",
    "ServiceStatusView",
    "ControlAuditView",
    # Quick Action Views
    "QuickAllowView",
    "QuickBlockView",
    "QuickResetView",
    # DLQ Views
    "DLQReplayView",
    "DLQCleanupStatsView",
    "DLQArchiveView",
    "DLQPurgeView",
    "DLQListView",
    "DLQDetailView",
    "DLQRetryView",
    "DLQResolveView",
    # Dashboard Views
    "DashboardSummaryView",
    # Health Views
    "SelfHealingHealthView",
    "LivenessView",
    "ReadinessView",
    "ConnectionPoolHealthView",
    "simple_health_ping",
    "SelfHealingMetricsView",
    # Metric Sync Views
    "MetricSyncView",
    "DriftReportView",
]
