"""
Self-Healing API Views Package.

This package provides REST API endpoints for the Self-Healing system.

Modules:
- circuit_breaker: Control API views for Circuit Breaker management
- dlq: DLQ (Dead Letter Queue) management views
- health: Health check and metrics views
"""

# Circuit Breaker Control Views
from selfhealing.api.django.views.circuit_breaker import (
    ControlRequest,
    ControlResponse,
    ControlAPIService,
    get_control_api_service,
    ControlActionView,
    ControlStatusView,
    ServiceStatusView,
    ControlAuditView,
    QuickAllowView,
    QuickBlockView,
    QuickResetView,
)

# DLQ Views
from selfhealing.api.django.views.dlq import (
    DLQReplayView,
    DLQCleanupStatsView,
    DLQArchiveView,
    DLQPurgeView,
    DLQListView,
    DLQDetailView,
    DLQRetryView,
    DLQResolveView,
)

# Dashboard Views
from selfhealing.api.django.views.dashboard import (
    DashboardSummaryView,
)

# Health & Metrics Views
from selfhealing.api.django.views.health import (
    SelfHealingHealthView,
    LivenessView,
    ReadinessView,
    ConnectionPoolHealthView,
    simple_health_ping,
    SelfHealingMetricsView,
)

# System Control Views (Kill Switch)
from selfhealing.api.django.views.system_control import (
    SystemStatusView,
    SystemEnableView,
    SystemDisableView,
    DryRunEnableView,
    DryRunDisableView,
    is_selfhealing_enabled,
    is_dry_run,
    should_execute_action,
    get_system_control,
)

# Runtime Config Views
from selfhealing.api.django.views.config import (
    AllConfigView,
    ResetConfigView,
    PendingChangesView,
    CancelPendingChangeView,
    CircuitBreakerConfigView,
    DLQConfigView,
    RetryConfigView,
    SLAConfigView,
    RateLimitConfigView,
    SecurityConfigView,
    IdempotencyConfigView,
    NotificationConfigView,
    ForensicConfigView,
    MetricsConfigView,
)

# Drift Threshold Configuration Views
from selfhealing.api.django.views.drift_threshold import (
    DriftThresholdConfigView,
    DriftThresholdResetView,
)

# Error Budget & Deployment Policy Views
from selfhealing.api.django.views.error_budget import (
    ErrorBudgetStatusView,
    ErrorBudgetHistoryView,
    DeploymentVerdictView,
    DeploymentFreezeAcknowledgeView,
    DeploymentOverrideView,
    DeploymentFreezeLiftView,
    ActiveOverrideView,
)

# Config History Views
from selfhealing.api.django.views.config_history import (
    ConfigHistoryView,
    ConfigVersionDetailView,
    ConfigRollbackView,
    ConfigCompareView,
)

# Chaos Engineering Views
from selfhealing.api.django.views.chaos import (
    SafetyGuardConfigView,
    BlastRadiusPolicyView,
    SchedulerConfigView,
    ReportConfigView,
    ScheduleListView,
    ScheduleDetailView,
    ScheduleApprovalView,
    ScheduleExecuteView,
    KillSwitchView,
    SafetyCheckView,
    BlastRadiusCheckView,
    ReportListView,
    ReportDetailView,
    ReportGenerateView,
    GradeHistoryView,
    PendingApprovalsView,
)

# Governance API Views (New Unified Hub)
# Reference: docs/self_healing/18_METRIC_DRIFT_STRATEGY.md
from selfhealing.api.django.views.governance import (
    # Service
    GovernanceService,
    get_governance_service,
    reset_governance_service,
    # New API Views
    MetricStatusView,
    GovernanceReconcileView,
    GovernanceModeView,
    # Deprecated Views (with Warning headers)
    DeprecatedMetricSyncView,
    DeprecatedDriftReportView,
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
    # System Control (Kill Switch)
    "SystemStatusView",
    "SystemEnableView",
    "SystemDisableView",
    "DryRunEnableView",
    "DryRunDisableView",
    "is_selfhealing_enabled",
    "is_dry_run",
    "should_execute_action",
    "get_system_control",
    # Config Views
    "AllConfigView",
    "ResetConfigView",
    "PendingChangesView",
    "CancelPendingChangeView",
    "CircuitBreakerConfigView",
    "DLQConfigView",
    "RetryConfigView",
    "SLAConfigView",
    "RateLimitConfigView",
    "SecurityConfigView",
    "IdempotencyConfigView",
    "NotificationConfigView",
    "ForensicConfigView",
    "MetricsConfigView",
    # Drift Threshold Config Views
    "DriftThresholdConfigView",
    "DriftThresholdResetView",
    # Config History Views
    "ConfigHistoryView",
    "ConfigVersionDetailView",
    "ConfigRollbackView",
    "ConfigCompareView",
    # Error Budget & Deployment Policy Views
    "ErrorBudgetStatusView",
    "ErrorBudgetHistoryView",
    "DeploymentVerdictView",
    "DeploymentFreezeAcknowledgeView",
    "DeploymentOverrideView",
    "DeploymentFreezeLiftView",
    "ActiveOverrideView",
    # Chaos Engineering Views
    "SafetyGuardConfigView",
    "BlastRadiusPolicyView",
    "SchedulerConfigView",
    "ReportConfigView",
    "ScheduleListView",
    "ScheduleDetailView",
    "ScheduleApprovalView",
    "ScheduleExecuteView",
    "KillSwitchView",
    "SafetyCheckView",
    "BlastRadiusCheckView",
    "ReportListView",
    "ReportDetailView",
    "ReportGenerateView",
    "GradeHistoryView",
    "PendingApprovalsView",
    # Governance API (New Unified Hub)
    "GovernanceService",
    "get_governance_service",
    "reset_governance_service",
    "MetricStatusView",
    "GovernanceReconcileView",
    "GovernanceModeView",
    "DeprecatedMetricSyncView",
    "DeprecatedDriftReportView",
]
