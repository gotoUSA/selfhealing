"""
Self-Healing API Views Package.

This package provides REST API endpoints for the Self-Healing system.

Performance Optimization:
    All views are lazy-loaded to minimize Django app startup time.
    Views are loaded on-demand when first accessed.

Usage:
    # Recommended: Import from specific submodules for best performance
    from selfhealing.api.django.views.circuit_breaker import ControlActionView
    from selfhealing.api.django.views.health import SelfHealingHealthView

    # Legacy: Still works via lazy import (backward compatible)
    from selfhealing.api.django.views import ControlActionView

Modules:
- circuit_breaker: Control API views for Circuit Breaker management
- dlq: DLQ (Dead Letter Queue) management views
- health: Health check and metrics views
- system_control: Kill switch and system control views
- config: Runtime configuration views
- drift_threshold: Drift threshold configuration views
- error_budget: Error budget and deployment policy views
- config_history: Configuration history views
- chaos: Chaos Engineering views (already lazy)
- governance: Governance API views
- xtest_mode: X-Test-Mode views
- auto_tuning: Auto tuning views
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# =============================================================================
# LAZY IMPORTS - All views loaded on-demand for faster startup
# =============================================================================

# Mapping of symbol names to their module paths
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # -------------------------------------------------------------------------
    # circuit_breaker.py (11 symbols)
    # -------------------------------------------------------------------------
    "ControlRequest": (
        "selfhealing.api.django.views.circuit_breaker",
        "ControlRequest",
    ),
    "ControlResponse": (
        "selfhealing.api.django.views.circuit_breaker",
        "ControlResponse",
    ),
    "ControlAPIService": (
        "selfhealing.api.django.views.circuit_breaker",
        "ControlAPIService",
    ),
    "get_control_api_service": (
        "selfhealing.api.django.views.circuit_breaker",
        "get_control_api_service",
    ),
    "ControlActionView": (
        "selfhealing.api.django.views.circuit_breaker",
        "ControlActionView",
    ),
    "ControlStatusView": (
        "selfhealing.api.django.views.circuit_breaker",
        "ControlStatusView",
    ),
    "ServiceStatusView": (
        "selfhealing.api.django.views.circuit_breaker",
        "ServiceStatusView",
    ),
    "ControlAuditView": (
        "selfhealing.api.django.views.circuit_breaker",
        "ControlAuditView",
    ),
    "QuickAllowView": (
        "selfhealing.api.django.views.circuit_breaker",
        "QuickAllowView",
    ),
    "QuickBlockView": (
        "selfhealing.api.django.views.circuit_breaker",
        "QuickBlockView",
    ),
    "QuickResetView": (
        "selfhealing.api.django.views.circuit_breaker",
        "QuickResetView",
    ),
    # -------------------------------------------------------------------------
    # dlq.py (8 symbols)
    # -------------------------------------------------------------------------
    "DLQReplayView": ("selfhealing.api.django.views.dlq", "DLQReplayView"),
    "DLQCleanupStatsView": ("selfhealing.api.django.views.dlq", "DLQCleanupStatsView"),
    "DLQArchiveView": ("selfhealing.api.django.views.dlq", "DLQArchiveView"),
    "DLQPurgeView": ("selfhealing.api.django.views.dlq", "DLQPurgeView"),
    "DLQListView": ("selfhealing.api.django.views.dlq", "DLQListView"),
    "DLQDetailView": ("selfhealing.api.django.views.dlq", "DLQDetailView"),
    "DLQRetryView": ("selfhealing.api.django.views.dlq", "DLQRetryView"),
    "DLQResolveView": ("selfhealing.api.django.views.dlq", "DLQResolveView"),
    # -------------------------------------------------------------------------
    # dashboard.py (1 symbol)
    # -------------------------------------------------------------------------
    "DashboardSummaryView": (
        "selfhealing.api.django.views.dashboard",
        "DashboardSummaryView",
    ),
    # -------------------------------------------------------------------------
    # health.py (6 symbols)
    # -------------------------------------------------------------------------
    "SelfHealingHealthView": (
        "selfhealing.api.django.views.health",
        "SelfHealingHealthView",
    ),
    "LivenessView": ("selfhealing.api.django.views.health", "LivenessView"),
    "ReadinessView": ("selfhealing.api.django.views.health", "ReadinessView"),
    "ConnectionPoolHealthView": (
        "selfhealing.api.django.views.health",
        "ConnectionPoolHealthView",
    ),
    "simple_health_ping": ("selfhealing.api.django.views.health", "simple_health_ping"),
    "SelfHealingMetricsView": (
        "selfhealing.api.django.views.health",
        "SelfHealingMetricsView",
    ),
    # -------------------------------------------------------------------------
    # system_control.py (9 symbols)
    # -------------------------------------------------------------------------
    "SystemStatusView": (
        "selfhealing.api.django.views.system_control",
        "SystemStatusView",
    ),
    "SystemEnableView": (
        "selfhealing.api.django.views.system_control",
        "SystemEnableView",
    ),
    "SystemDisableView": (
        "selfhealing.api.django.views.system_control",
        "SystemDisableView",
    ),
    "DryRunEnableView": (
        "selfhealing.api.django.views.system_control",
        "DryRunEnableView",
    ),
    "DryRunDisableView": (
        "selfhealing.api.django.views.system_control",
        "DryRunDisableView",
    ),
    "is_selfhealing_enabled": (
        "selfhealing.api.django.views.system_control",
        "is_selfhealing_enabled",
    ),
    "is_dry_run": ("selfhealing.api.django.views.system_control", "is_dry_run"),
    "should_execute_action": (
        "selfhealing.api.django.views.system_control",
        "should_execute_action",
    ),
    "get_system_control": (
        "selfhealing.api.django.views.system_control",
        "get_system_control",
    ),
    # -------------------------------------------------------------------------
    # config.py (14 symbols)
    # -------------------------------------------------------------------------
    "AllConfigView": ("selfhealing.api.django.views.config", "AllConfigView"),
    "ResetConfigView": ("selfhealing.api.django.views.config", "ResetConfigView"),
    "PendingChangesView": ("selfhealing.api.django.views.config", "PendingChangesView"),
    "CancelPendingChangeView": (
        "selfhealing.api.django.views.config",
        "CancelPendingChangeView",
    ),
    "CircuitBreakerConfigView": (
        "selfhealing.api.django.views.config",
        "CircuitBreakerConfigView",
    ),
    "DLQConfigView": ("selfhealing.api.django.views.config", "DLQConfigView"),
    "RetryConfigView": ("selfhealing.api.django.views.config", "RetryConfigView"),
    "SLAConfigView": ("selfhealing.api.django.views.config", "SLAConfigView"),
    "RateLimitConfigView": (
        "selfhealing.api.django.views.config",
        "RateLimitConfigView",
    ),
    "SecurityConfigView": ("selfhealing.api.django.views.config", "SecurityConfigView"),
    "IdempotencyConfigView": (
        "selfhealing.api.django.views.config",
        "IdempotencyConfigView",
    ),
    "NotificationConfigView": (
        "selfhealing.api.django.views.config",
        "NotificationConfigView",
    ),
    "ForensicConfigView": ("selfhealing.api.django.views.config", "ForensicConfigView"),
    "MetricsConfigView": ("selfhealing.api.django.views.config", "MetricsConfigView"),
    # -------------------------------------------------------------------------
    # drift_threshold.py (2 symbols)
    # -------------------------------------------------------------------------
    "DriftThresholdConfigView": (
        "selfhealing.api.django.views.drift_threshold",
        "DriftThresholdConfigView",
    ),
    "DriftThresholdResetView": (
        "selfhealing.api.django.views.drift_threshold",
        "DriftThresholdResetView",
    ),
    # -------------------------------------------------------------------------
    # error_budget.py (7 symbols)
    # -------------------------------------------------------------------------
    "ErrorBudgetStatusView": (
        "selfhealing.api.django.views.error_budget",
        "ErrorBudgetStatusView",
    ),
    "ErrorBudgetHistoryView": (
        "selfhealing.api.django.views.error_budget",
        "ErrorBudgetHistoryView",
    ),
    "DeploymentVerdictView": (
        "selfhealing.api.django.views.error_budget",
        "DeploymentVerdictView",
    ),
    "DeploymentFreezeAcknowledgeView": (
        "selfhealing.api.django.views.error_budget",
        "DeploymentFreezeAcknowledgeView",
    ),
    "DeploymentOverrideView": (
        "selfhealing.api.django.views.error_budget",
        "DeploymentOverrideView",
    ),
    "DeploymentFreezeLiftView": (
        "selfhealing.api.django.views.error_budget",
        "DeploymentFreezeLiftView",
    ),
    "ActiveOverrideView": (
        "selfhealing.api.django.views.error_budget",
        "ActiveOverrideView",
    ),
    # -------------------------------------------------------------------------
    # config_history.py (4 symbols)
    # -------------------------------------------------------------------------
    "ConfigHistoryView": (
        "selfhealing.api.django.views.config_history",
        "ConfigHistoryView",
    ),
    "ConfigVersionDetailView": (
        "selfhealing.api.django.views.config_history",
        "ConfigVersionDetailView",
    ),
    "ConfigRollbackView": (
        "selfhealing.api.django.views.config_history",
        "ConfigRollbackView",
    ),
    "ConfigCompareView": (
        "selfhealing.api.django.views.config_history",
        "ConfigCompareView",
    ),
    # -------------------------------------------------------------------------
    # chaos/ (16 symbols) - already lazy in chaos/__init__.py
    # -------------------------------------------------------------------------
    "SafetyGuardConfigView": (
        "selfhealing.api.django.views.chaos",
        "SafetyGuardConfigView",
    ),
    "BlastRadiusPolicyView": (
        "selfhealing.api.django.views.chaos",
        "BlastRadiusPolicyView",
    ),
    "SchedulerConfigView": (
        "selfhealing.api.django.views.chaos",
        "SchedulerConfigView",
    ),
    "ReportConfigView": ("selfhealing.api.django.views.chaos", "ReportConfigView"),
    "ScheduleListView": ("selfhealing.api.django.views.chaos", "ScheduleListView"),
    "ScheduleDetailView": ("selfhealing.api.django.views.chaos", "ScheduleDetailView"),
    "ScheduleApprovalView": (
        "selfhealing.api.django.views.chaos",
        "ScheduleApprovalView",
    ),
    "ScheduleExecuteView": (
        "selfhealing.api.django.views.chaos",
        "ScheduleExecuteView",
    ),
    "KillSwitchView": ("selfhealing.api.django.views.chaos", "KillSwitchView"),
    "SafetyCheckView": ("selfhealing.api.django.views.chaos", "SafetyCheckView"),
    "BlastRadiusCheckView": (
        "selfhealing.api.django.views.chaos",
        "BlastRadiusCheckView",
    ),
    "ReportListView": ("selfhealing.api.django.views.chaos", "ReportListView"),
    "ReportDetailView": ("selfhealing.api.django.views.chaos", "ReportDetailView"),
    "ReportGenerateView": ("selfhealing.api.django.views.chaos", "ReportGenerateView"),
    "GradeHistoryView": ("selfhealing.api.django.views.chaos", "GradeHistoryView"),
    "PendingApprovalsView": (
        "selfhealing.api.django.views.chaos",
        "PendingApprovalsView",
    ),
    # -------------------------------------------------------------------------
    # governance/ (7 symbols) - Deprecated views removed
    # -------------------------------------------------------------------------
    "GovernanceService": (
        "selfhealing.api.django.views.governance",
        "GovernanceService",
    ),
    "get_governance_service": (
        "selfhealing.api.django.views.governance",
        "get_governance_service",
    ),
    "reset_governance_service": (
        "selfhealing.api.django.views.governance",
        "reset_governance_service",
    ),
    "MetricStatusView": ("selfhealing.api.django.views.governance", "MetricStatusView"),
    "GovernanceReconcileView": (
        "selfhealing.api.django.views.governance",
        "GovernanceReconcileView",
    ),
    "GovernanceModeView": (
        "selfhealing.api.django.views.governance",
        "GovernanceModeView",
    ),
    # -------------------------------------------------------------------------
    # xtest/ package (11 symbols) - 직접 import 패턴 사용
    # -------------------------------------------------------------------------
    "XTestModeMixin": ("selfhealing.api.django.views.xtest", "XTestModeMixin"),
    "InjectCBFailureView": (
        "selfhealing.api.django.views.xtest",
        "InjectCBFailureView",
    ),
    "ResetCBView": ("selfhealing.api.django.views.xtest", "ResetCBView"),
    "CBStatusDetailView": ("selfhealing.api.django.views.xtest", "CBStatusDetailView"),
    "InjectErrorBudgetView": (
        "selfhealing.api.django.views.xtest",
        "InjectErrorBudgetView",
    ),
    "SystemSnapshotView": ("selfhealing.api.django.views.xtest", "SystemSnapshotView"),
    "FastFailTestView": ("selfhealing.api.django.views.xtest", "FastFailTestView"),
    # DLQ X-Test Views
    "InjectDLQEntryView": ("selfhealing.api.django.views.xtest", "InjectDLQEntryView"),
    "DLQXTestStatusView": ("selfhealing.api.django.views.xtest", "DLQXTestStatusView"),
    "ForceStatusView": ("selfhealing.api.django.views.xtest", "ForceStatusView"),
    "ResetDLQXTestView": ("selfhealing.api.django.views.xtest", "ResetDLQXTestView"),
    # -------------------------------------------------------------------------
    # auto_tuning.py (9 symbols)
    # -------------------------------------------------------------------------
    "AutoTuningStatusView": (
        "selfhealing.api.django.views.auto_tuning",
        "AutoTuningStatusView",
    ),
    "AutoTuningEnableView": (
        "selfhealing.api.django.views.auto_tuning",
        "AutoTuningEnableView",
    ),
    "AutoTuningDisableView": (
        "selfhealing.api.django.views.auto_tuning",
        "AutoTuningDisableView",
    ),
    "AutoTuningModuleEnableView": (
        "selfhealing.api.django.views.auto_tuning",
        "AutoTuningModuleEnableView",
    ),
    "AutoTuningModuleDisableView": (
        "selfhealing.api.django.views.auto_tuning",
        "AutoTuningModuleDisableView",
    ),
    "AutoTuningBoundsView": (
        "selfhealing.api.django.views.auto_tuning",
        "AutoTuningBoundsView",
    ),
    "AutoTuningHistoryView": (
        "selfhealing.api.django.views.auto_tuning",
        "AutoTuningHistoryView",
    ),
    "AutoTuningOverrideView": (
        "selfhealing.api.django.views.auto_tuning",
        "AutoTuningOverrideView",
    ),
    "AutoTuningMetricsView": (
        "selfhealing.api.django.views.auto_tuning",
        "AutoTuningMetricsView",
    ),
    # -------------------------------------------------------------------------
    # grafana_webhook.py (2 symbols)
    # -------------------------------------------------------------------------
    "GrafanaAlertWebhookView": (
        "selfhealing.api.django.views.grafana_webhook",
        "GrafanaAlertWebhookView",
    ),
    "GrafanaAlertWebhookTestView": (
        "selfhealing.api.django.views.grafana_webhook",
        "GrafanaAlertWebhookTestView",
    ),
}

# Cache for lazily loaded symbols
_loaded_symbols: dict[str, object] = {}


def __getattr__(name: str) -> object:
    """Lazy import for backward compatibility.

    This allows:
        from selfhealing.api.django.views import ControlActionView

    Without loading all view modules at package import time.
    """
    if name in _loaded_symbols:
        return _loaded_symbols[name]

    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        import importlib

        module = importlib.import_module(module_path)
        symbol = getattr(module, attr_name)
        _loaded_symbols[name] = symbol
        return symbol

    raise AttributeError(f"module 'selfhealing.api.django.views' has no attribute '{name}'")


def __dir__() -> list[str]:
    """List available symbols for IDE autocompletion."""
    return list(__all__)


# TYPE_CHECKING block for IDE support without runtime import
if TYPE_CHECKING:
    # Circuit Breaker Control Views
    # Auto Tuning Views
    from selfhealing.api.django.views.auto_tuning import (
        AutoTuningBoundsView,
        AutoTuningDisableView,
        AutoTuningEnableView,
        AutoTuningHistoryView,
        AutoTuningMetricsView,
        AutoTuningModuleDisableView,
        AutoTuningModuleEnableView,
        AutoTuningOverrideView,
        AutoTuningStatusView,
    )

    # Chaos Engineering Views
    from selfhealing.api.django.views.chaos import (
        BlastRadiusCheckView,
        BlastRadiusPolicyView,
        GradeHistoryView,
        KillSwitchView,
        PendingApprovalsView,
        ReportConfigView,
        ReportDetailView,
        ReportGenerateView,
        ReportListView,
        SafetyCheckView,
        SafetyGuardConfigView,
        ScheduleApprovalView,
        ScheduleDetailView,
        ScheduleExecuteView,
        ScheduleListView,
        SchedulerConfigView,
    )
    from selfhealing.api.django.views.circuit_breaker import (
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

    # Runtime Config Views
    from selfhealing.api.django.views.config import (
        AllConfigView,
        CancelPendingChangeView,
        CircuitBreakerConfigView,
        DLQConfigView,
        ForensicConfigView,
        IdempotencyConfigView,
        MetricsConfigView,
        NotificationConfigView,
        PendingChangesView,
        RateLimitConfigView,
        ResetConfigView,
        RetryConfigView,
        SecurityConfigView,
        SLAConfigView,
    )

    # Config History Views
    from selfhealing.api.django.views.config_history import (
        ConfigCompareView,
        ConfigHistoryView,
        ConfigRollbackView,
        ConfigVersionDetailView,
    )

    # Dashboard Views
    from selfhealing.api.django.views.dashboard import (
        DashboardSummaryView,
    )

    # DLQ Views
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

    # Drift Threshold Configuration Views
    from selfhealing.api.django.views.drift_threshold import (
        DriftThresholdConfigView,
        DriftThresholdResetView,
    )

    # Error Budget & Deployment Policy Views
    from selfhealing.api.django.views.error_budget import (
        ActiveOverrideView,
        DeploymentFreezeAcknowledgeView,
        DeploymentFreezeLiftView,
        DeploymentOverrideView,
        DeploymentVerdictView,
        ErrorBudgetHistoryView,
        ErrorBudgetStatusView,
    )

    # Governance API Views (New Unified Hub)
    from selfhealing.api.django.views.governance import (
        GovernanceModeView,
        GovernanceReconcileView,
        GovernanceService,
        MetricStatusView,
        get_governance_service,
        reset_governance_service,
    )

    # Health & Metrics Views
    from selfhealing.api.django.views.health import (
        ConnectionPoolHealthView,
        LivenessView,
        ReadinessView,
        SelfHealingHealthView,
        SelfHealingMetricsView,
        simple_health_ping,
    )

    # System Control Views (Kill Switch)
    from selfhealing.api.django.views.system_control import (
        DryRunDisableView,
        DryRunEnableView,
        SystemDisableView,
        SystemEnableView,
        SystemStatusView,
        get_system_control,
        is_dry_run,
        is_selfhealing_enabled,
        should_execute_action,
    )

    # X-Test-Mode Views (Stage 48: Chaos Proof)
    # 직접 import 패턴 사용 (xtest_mode.py re-export 대신)
    from selfhealing.api.django.views.xtest import (  # DLQ X-Test Views
        CBStatusDetailView,
        DLQXTestStatusView,
        FastFailTestView,
        ForceStatusView,
        InjectCBFailureView,
        InjectDLQEntryView,
        InjectErrorBudgetView,
        ResetCBView,
        ResetDLQXTestView,
        SystemSnapshotView,
        XTestModeMixin,
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
    # X-Test-Mode Views (Stage 48)
    "XTestModeMixin",
    "InjectCBFailureView",
    "ResetCBView",
    "CBStatusDetailView",
    "InjectErrorBudgetView",
    "SystemSnapshotView",
    "FastFailTestView",
    # DLQ X-Test Views
    "InjectDLQEntryView",
    "DLQXTestStatusView",
    "ForceStatusView",
    "ResetDLQXTestView",
    # Auto Tuning Views (Stage 38)
    "AutoTuningStatusView",
    "AutoTuningEnableView",
    "AutoTuningDisableView",
    "AutoTuningModuleEnableView",
    "AutoTuningModuleDisableView",
    "AutoTuningBoundsView",
    "AutoTuningHistoryView",
    "AutoTuningOverrideView",
    "AutoTuningMetricsView",
    # Grafana Webhook Views
    "GrafanaAlertWebhookView",
    "GrafanaAlertWebhookTestView",
]
