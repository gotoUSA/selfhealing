"""
Self-Healing API URL Configuration.

Include these URLs in your Django project's urls.py:

    from selfhealing.api.django import urls as selfhealing_urls

    urlpatterns = [
        # ... your other urls
        path('api/self-healing/', include(selfhealing_urls)),
    ]
"""

from django.conf import settings
from django.urls import path

# Pool Circuit Breaker API
from selfhealing.api.django.pool_circuit_breaker import (
    circuit_breaker_reset,
    circuit_breaker_status,
)
from selfhealing.api.django.views import (
    ConnectionPoolHealthView,
    ControlActionView,
    ControlAuditView,
    ControlStatusView,
    DLQArchiveView,
    DLQCleanupStatsView,
    DLQPurgeView,
    DLQReplayView,
    LivenessView,
    QuickAllowView,
    QuickBlockView,
    QuickResetView,
    ReadinessView,
    SelfHealingHealthView,
    SelfHealingMetricsView,
    ServiceStatusView,
    simple_health_ping,
)

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

# Canary Rollout API Views
from selfhealing.api.django.views.canary import (
    CanaryHistoryView,
    CanaryMetricsView,
    CanaryPanicRollbackView,
    CanaryRolloutActionView,
    CanaryRolloutDetailView,
    CanaryRolloutListView,
)

# Cascade Event Audit API Views (Phase 8)
from selfhealing.api.django.views.cascade import (
    CascadeChainVerifyView,
    CascadeCheckpointView,
    CascadeEventDetailView,
    CascadeEventListView,
    CascadeLoadSheddingStatusView,
    CausationTraceView,
)

# Chaos Engineering API Views
from selfhealing.api.django.views.chaos import (  # Safety Mechanism Views; Impact Prediction Views
    BlastRadiusCheckView,
    BlastRadiusPolicyView,
    DryRunAnalysisView,
    DryRunConfigView,
    GradeHistoryView,
    KillAllView,
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
    StopConditionsConfigView,
    TTLConfigView,
)

# Runtime Config Views
from selfhealing.api.django.views.config import (
    AllConfigView,
    CancelPendingChangeView,
    CircuitBreakerConfigView,
    DLQConfigView,
    ErrorBudgetConfigView,
    ForensicConfigView,
    IdempotencyConfigView,
    LoggingConfigView,
    MetricsConfigView,
    NotificationConfigView,
    PendingChangesView,
    RateLimitConfigView,
    ReplayAutomationConfigView,
    ResetConfigView,
    RetryConfigView,
    SecurityConfigView,
    SLAConfigView,
    SLOConfigView,
)

# Config History & Rollback Views
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

# DLQ Detail Views
from selfhealing.api.django.views.dlq import (
    DLQArchiveView,
    DLQCleanupStatsView,
    DLQDetailView,
    DLQListView,
    DLQPurgeView,
    DLQReplayView,
    DLQResolveView,
    DLQRetryView,
    DLQTestCreateView,
)

# Drift Threshold Configuration Views
from selfhealing.api.django.views.drift_threshold import (
    DriftThresholdConfigView,
    DriftThresholdResetView,
)

# Emergency Mode Views
from selfhealing.api.django.views.emergency import (
    EmergencyConfigView,
    EmergencyHistoryView,
    EmergencyLevelsView,
    EmergencyReleaseView,
    EmergencyStatusView,
    EmergencyTriggerView,
    GradualRecoveryStartView,
    GradualRecoveryStopView,
)
from selfhealing.api.django.views.error_budget.deployment import (
    ActiveOverrideView,
    DeploymentFreezeAcknowledgeView,
    DeploymentFreezeLiftView,
    DeploymentOverrideView,
    DeploymentVerdictView,
)
from selfhealing.api.django.views.error_budget.reconciliation import (
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

# Error Budget & Deployment Policy Views
from selfhealing.api.django.views.error_budget.status import (
    ErrorBudgetExhaustView,
    ErrorBudgetHistoryView,
    ErrorBudgetRecordView,
    ErrorBudgetResetSimulationView,
    ErrorBudgetStatusView,
)

# Governance API Views (New Unified Hub)
from selfhealing.api.django.views.governance import (  # 4-Eyes Approval & L2 Storage
    ApprovalRequestApproveView,
    ApprovalRequestListView,
    ApprovalRequestRejectView,
    GovernanceConfigView,
    GovernanceModeView,
    GovernanceRBACStatusView,
    GovernanceReconcileView,
    L2StorageConfigManagedView,
    MetricStatusView,
)

# Error Budget Gate Health Views
from selfhealing.api.django.views.health import (
    ErrorBudgetGateConfigView,
    ErrorBudgetGateHealthView,
    ErrorBudgetGateResetView,
)

# L2 Storage Resilience API Views
from selfhealing.api.django.views.l2_storage import (  # Drift Reconciliation Views
    DriftReconciliationHistoryView,
    DriftReconciliationServiceView,
    DriftReconciliationStatsView,
    DriftReconciliationTriggerView,
    L2StorageConfigResetView,
    L2StorageConfigView,
    L2StorageHealthResetView,
    L2StorageHealthView,
    L2StorageMetricsView,
    L2StorageStatusView,
    L2StorageSyncFromL2View,
    L2StorageSyncToL2View,
    ShadowLogAnalyzeView,
    ShadowLogByServiceView,
    ShadowLogClearView,
    ShadowLogListView,
    ShadowLogReplayView,
    ShadowLogStatsView,
)

# Metric Sync Views
# Recovery API Views
from selfhealing.api.django.views.recovery import (
    RecoveryAbortView,
    RecoveryApproveView,
    RecoveryDashboardWidgetView,
    RecoveryHistoryView,
    RecoveryPendingApprovalsView,
    RecoveryRejectView,
    RecoveryStartView,
    RecoveryStatusView,
)

# System Control Views (Kill Switch)
from selfhealing.api.django.views.system_control import (
    DryRunDisableView,
    DryRunEnableView,
    SystemDisableView,
    SystemEnableView,
    SystemStatusView,
)

# API Tiering Views
from selfhealing.api.django.views.tiering import (
    TierDefinitionsView,
    TierDryRunView,
    TierExportView,
    TierImportView,
    TierMappingsView,
    TierOverridesView,
    TierResetView,
    TierResolveLookupView,
)

app_name = "selfhealing"

urlpatterns = [
    # Control API
    path("control/", ControlActionView.as_view(), name="control-action"),
    # Status endpoints
    path("status/", ControlStatusView.as_view(), name="status"),
    path("status/<str:service_name>/", ServiceStatusView.as_view(), name="service-status"),
    # Audit logs
    path("audit/", ControlAuditView.as_view(), name="audit"),
    # Quick actions
    path("allow/<str:service_name>/", QuickAllowView.as_view(), name="quick-allow"),
    path("block/<str:service_name>/", QuickBlockView.as_view(), name="quick-block"),
    path("reset/<str:service_name>/", QuickResetView.as_view(), name="quick-reset"),
    # Health & Metrics
    path("health/", SelfHealingHealthView.as_view(), name="health"),
    path("health/live/", LivenessView.as_view(), name="health-liveness"),
    path("health/ready/", ReadinessView.as_view(), name="health-readiness"),
    path("health/pool/", ConnectionPoolHealthView.as_view(), name="health-pool"),
    path("health/ping/", simple_health_ping, name="health-ping"),
    path("health/gate/", ErrorBudgetGateHealthView.as_view(), name="health-gate"),
    path("metrics/", SelfHealingMetricsView.as_view(), name="metrics"),
    # DLQ
    path("dlq/replay/", DLQReplayView.as_view(), name="dlq-replay"),
    path("dlq/cleanup/stats/", DLQCleanupStatsView.as_view(), name="dlq-cleanup-stats"),
    path("dlq/cleanup/archive/", DLQArchiveView.as_view(), name="dlq-cleanup-archive"),
    path("dlq/cleanup/purge/", DLQPurgeView.as_view(), name="dlq-cleanup-purge"),
    path("dlq/list/", DLQListView.as_view(), name="dlq-list"),
    path("dlq/<int:pk>/", DLQDetailView.as_view(), name="dlq-detail"),
    path("dlq/<int:pk>/retry/", DLQRetryView.as_view(), name="dlq-retry"),
    path("dlq/<int:pk>/resolve/", DLQResolveView.as_view(), name="dlq-resolve"),
    path("dlq/test/create/", DLQTestCreateView.as_view(), name="dlq-test-create"),
    # Dashboard
    path("dashboard/summary/", DashboardSummaryView.as_view(), name="dashboard-summary"),
    # Pool Circuit Breaker API
    path("circuit-breaker/pool/status/", circuit_breaker_status, name="pool-cb-status"),
    path("circuit-breaker/pool/reset/", circuit_breaker_reset, name="pool-cb-reset"),
    # System Control (Global Kill Switch)
    path("system/status/", SystemStatusView.as_view(), name="system-status"),
    path("system/enable/", SystemEnableView.as_view(), name="system-enable"),
    path("system/disable/", SystemDisableView.as_view(), name="system-disable"),
    # Dry Run Mode
    path("system/dry-run/enable/", DryRunEnableView.as_view(), name="dry-run-enable"),
    path("system/dry-run/disable/", DryRunDisableView.as_view(), name="dry-run-disable"),
    # Runtime Configuration API
    path("config/", AllConfigView.as_view(), name="config-all"),
    path("config/reset/", ResetConfigView.as_view(), name="config-reset"),
    path("config/pending/", PendingChangesView.as_view(), name="config-pending"),
    path(
        "config/pending/<str:pending_id>/cancel/",
        CancelPendingChangeView.as_view(),
        name="config-pending-cancel",
    ),
    path(
        "config/circuit-breaker/",
        CircuitBreakerConfigView.as_view(),
        name="config-circuit-breaker",
    ),
    path("config/dlq/", DLQConfigView.as_view(), name="config-dlq"),
    path("config/retry/", RetryConfigView.as_view(), name="config-retry"),
    path("config/sla/", SLAConfigView.as_view(), name="config-sla"),
    path("config/slo/", SLOConfigView.as_view(), name="config-slo"),
    path("config/rate-limit/", RateLimitConfigView.as_view(), name="config-rate-limit"),
    path("config/security/", SecurityConfigView.as_view(), name="config-security"),
    path(
        "config/idempotency/",
        IdempotencyConfigView.as_view(),
        name="config-idempotency",
    ),
    path(
        "config/notification/",
        NotificationConfigView.as_view(),
        name="config-notification",
    ),
    path("config/forensic/", ForensicConfigView.as_view(), name="config-forensic"),
    path("config/logging/", LoggingConfigView.as_view(), name="config-logging"),
    path("config/metrics/", MetricsConfigView.as_view(), name="config-metrics"),
    path(
        "config/error-budget/",
        ErrorBudgetConfigView.as_view(),
        name="config-error-budget",
    ),
    path("config/gate/", ErrorBudgetGateConfigView.as_view(), name="config-gate"),
    # =========================================================================
    # Replay Automation Configuration (DLQ Replay Tracks)
    # =========================================================================
    path(
        "config/replay-automation/",
        ReplayAutomationConfigView.as_view(),
        name="config-replay-automation",
    ),
    # =========================================================================
    # Drift Threshold Configuration (Metric Collection Strategy)
    # =========================================================================
    path(
        "config/drift-thresholds/",
        DriftThresholdConfigView.as_view(),
        name="config-drift-thresholds",
    ),
    path(
        "config/drift-thresholds/reset/",
        DriftThresholdResetView.as_view(),
        name="config-drift-thresholds-reset",
    ),
    # =========================================================================
    # Governance API (Unified Hub)
    # 관찰(Observability): GET /metrics/status/
    # 제어(Control): POST /governance/reconcile/, POST /governance/mode/
    # =========================================================================
    # Observability - 통합 상태 조회
    path("metrics/status/", MetricStatusView.as_view(), name="metrics-status"),
    # Control - 정합성 조정 및 모드 전환
    path(
        "governance/reconcile/",
        GovernanceReconcileView.as_view(),
        name="governance-reconcile",
    ),
    path("governance/mode/", GovernanceModeView.as_view(), name="governance-mode"),
    # =========================================================================
    # Governance RBAC Status & Config API
    # =========================================================================
    path(
        "governance/status/",
        GovernanceRBACStatusView.as_view(),
        name="governance-status",
    ),
    path("config/governance/", GovernanceConfigView.as_view(), name="config-governance"),
    # =========================================================================
    # 4-Eyes Approval Workflow API
    # =========================================================================
    path(
        "governance/approval-requests/",
        ApprovalRequestListView.as_view(),
        name="approval-requests-list",
    ),
    path(
        "governance/approval-requests/<str:request_id>/approve/",
        ApprovalRequestApproveView.as_view(),
        name="approval-request-approve",
    ),
    path(
        "governance/approval-requests/<str:request_id>/reject/",
        ApprovalRequestRejectView.as_view(),
        name="approval-request-reject",
    ),
    # =========================================================================
    # L2 Storage Config API (RuntimeConfigManager Integration)
    # =========================================================================
    path(
        "config/l2-storage/",
        L2StorageConfigManagedView.as_view(),
        name="config-l2-storage",
    ),
    # =========================================================================
    # Config Versioning & Rollback
    # =========================================================================
    path(
        "config/<str:config_type>/history/",
        ConfigHistoryView.as_view(),
        name="config-history",
    ),
    path(
        "config/<str:config_type>/history/<int:version>/",
        ConfigVersionDetailView.as_view(),
        name="config-version-detail",
    ),
    path(
        "config/<str:config_type>/rollback/",
        ConfigRollbackView.as_view(),
        name="config-rollback",
    ),
    path(
        "config/<str:config_type>/compare/",
        ConfigCompareView.as_view(),
        name="config-compare",
    ),
    # =========================================================================
    # API Tiering Configuration
    # =========================================================================
    path("config/tiers/", TierDefinitionsView.as_view(), name="config-tiers"),
    path("config/tiers/reset/", TierResetView.as_view(), name="config-tiers-reset"),
    path("config/tiers/dry-run/", TierDryRunView.as_view(), name="config-tiers-dry-run"),
    path("config/tiers/export/", TierExportView.as_view(), name="config-tiers-export"),
    path("config/tiers/import/", TierImportView.as_view(), name="config-tiers-import"),
    path(
        "config/tiers/resolve/",
        TierResolveLookupView.as_view(),
        name="config-tiers-resolve",
    ),
    path("config/tier-mappings/", TierMappingsView.as_view(), name="config-tier-mappings"),
    path(
        "config/tier-overrides/",
        TierOverridesView.as_view(),
        name="config-tier-overrides",
    ),
    # =========================================================================
    # Emergency Mode API
    # =========================================================================
    path("emergency/status/", EmergencyStatusView.as_view(), name="emergency-status"),
    path("emergency/trigger/", EmergencyTriggerView.as_view(), name="emergency-trigger"),
    path("emergency/release/", EmergencyReleaseView.as_view(), name="emergency-release"),
    path(
        "emergency/gradual-recovery/",
        GradualRecoveryStartView.as_view(),
        name="emergency-gradual-recovery",
    ),
    path(
        "emergency/stop-recovery/",
        GradualRecoveryStopView.as_view(),
        name="emergency-stop-recovery",
    ),
    path("emergency/history/", EmergencyHistoryView.as_view(), name="emergency-history"),
    path("emergency/config/", EmergencyConfigView.as_view(), name="emergency-config"),
    path("emergency/levels/", EmergencyLevelsView.as_view(), name="emergency-levels"),
    # =========================================================================
    # Cascade Event Audit API (Phase 8)
    # 연계 이벤트 감사 추적: 인과관계 조회, Hash Chain 무결성 검증
    # Reference: docs/self_healing/middleware_system/76_CASCADE_EVENT_AUDIT.md
    # =========================================================================
    path("cascade/events/", CascadeEventListView.as_view(), name="cascade-event-list"),
    path(
        "cascade/events/<str:cascade_id>/",
        CascadeEventDetailView.as_view(),
        name="cascade-event-detail",
    ),
    path("cascade/verify/", CascadeChainVerifyView.as_view(), name="cascade-chain-verify"),
    path(
        "cascade/trace/<str:event_id>/",
        CausationTraceView.as_view(),
        name="cascade-causation-trace",
    ),
    path(
        "cascade/checkpoint/",
        CascadeCheckpointView.as_view(),
        name="cascade-checkpoint",
    ),
    path(
        "cascade/load-shedding/status/",
        CascadeLoadSheddingStatusView.as_view(),
        name="cascade-load-shedding-status",
    ),
    # =========================================================================
    # Auto Tuning API - 자율 조정 제어
    # =========================================================================
    # Status
    path("auto-tuning/status/", AutoTuningStatusView.as_view(), name="auto-tuning-status"),
    # Enable/Disable
    path("auto-tuning/enable/", AutoTuningEnableView.as_view(), name="auto-tuning-enable"),
    path(
        "auto-tuning/disable/",
        AutoTuningDisableView.as_view(),
        name="auto-tuning-disable",
    ),
    # Module Control
    path(
        "auto-tuning/<str:module>/enable/",
        AutoTuningModuleEnableView.as_view(),
        name="auto-tuning-module-enable",
    ),
    path(
        "auto-tuning/<str:module>/disable/",
        AutoTuningModuleDisableView.as_view(),
        name="auto-tuning-module-disable",
    ),
    # Bounds
    path("auto-tuning/bounds/", AutoTuningBoundsView.as_view(), name="auto-tuning-bounds"),
    # History
    path(
        "auto-tuning/history/",
        AutoTuningHistoryView.as_view(),
        name="auto-tuning-history",
    ),
    path(
        "auto-tuning/history/<str:history_id>/",
        AutoTuningHistoryView.as_view(),
        name="auto-tuning-history-detail",
    ),
    # Override
    path(
        "auto-tuning/override/",
        AutoTuningOverrideView.as_view(),
        name="auto-tuning-override",
    ),
    path(
        "auto-tuning/override/<str:parameter>/",
        AutoTuningOverrideView.as_view(),
        name="auto-tuning-override-clear",
    ),
    # Metrics
    path(
        "auto-tuning/metrics/",
        AutoTuningMetricsView.as_view(),
        name="auto-tuning-metrics",
    ),
    # Error Budget Gate Reset
    path("gate/reset/", ErrorBudgetGateResetView.as_view(), name="gate-reset"),
    # Error Budget API
    path(
        "error-budget/status/",
        ErrorBudgetStatusView.as_view(),
        name="error-budget-status",
    ),
    path(
        "error-budget/history/",
        ErrorBudgetHistoryView.as_view(),
        name="error-budget-history",
    ),
    # Chaos Engineering / Test APIs for Error Budget
    path(
        "error-budget/record/",
        ErrorBudgetRecordView.as_view(),
        name="error-budget-record",
    ),
    path(
        "error-budget/exhaust/",
        ErrorBudgetExhaustView.as_view(),
        name="error-budget-exhaust",
    ),
    path(
        "error-budget/reset-simulation/",
        ErrorBudgetResetSimulationView.as_view(),
        name="error-budget-reset-simulation",
    ),
    # Deployment Policy API
    path(
        "deployment-policy/verdict/",
        DeploymentVerdictView.as_view(),
        name="deployment-verdict",
    ),
    path(
        "deployment-policy/acknowledge/",
        DeploymentFreezeAcknowledgeView.as_view(),
        name="deployment-acknowledge",
    ),
    path(
        "deployment-policy/override/",
        DeploymentOverrideView.as_view(),
        name="deployment-override",
    ),
    path(
        "deployment-policy/lift/",
        DeploymentFreezeLiftView.as_view(),
        name="deployment-lift",
    ),
    path(
        "deployment-policy/active-override/",
        ActiveOverrideView.as_view(),
        name="deployment-active-override",
    ),
    # =========================================================================
    # Reconciliation API (Shadow Budget)
    # "시스템은 계산하고, 반영은 사람이 결정한다."
    # =========================================================================
    path(
        "reconciliation/status/",
        ReconciliationStatusView.as_view(),
        name="reconciliation-status",
    ),
    path(
        "reconciliation/failsafe-periods/",
        FailSafePeriodsView.as_view(),
        name="reconciliation-failsafe-periods",
    ),
    path(
        "reconciliation/shadow-budgets/",
        ShadowBudgetsView.as_view(),
        name="reconciliation-shadow-budgets",
    ),
    path(
        "reconciliation/shadow-budgets/<str:calculation_id>/",
        ShadowBudgetDetailView.as_view(),
        name="reconciliation-shadow-budget-detail",
    ),
    path(
        "reconciliation/shadow-budgets/<str:calculation_id>/approve/",
        ShadowBudgetApproveView.as_view(),
        name="reconciliation-shadow-budget-approve",
    ),
    path(
        "reconciliation/shadow-budgets/<str:calculation_id>/reject/",
        ShadowBudgetRejectView.as_view(),
        name="reconciliation-shadow-budget-reject",
    ),
    path(
        "reconciliation/excluded-periods/",
        ExcludedPeriodsView.as_view(),
        name="reconciliation-excluded-periods",
    ),
    path(
        "reconciliation/excluded-periods/<str:exclusion_id>/",
        ExcludedPeriodDetailView.as_view(),
        name="reconciliation-excluded-period-detail",
    ),
    path(
        "reconciliation/config/",
        ReconciliationConfigView.as_view(),
        name="reconciliation-config",
    ),
    # =========================================================================
    # Chaos Engineering API
    # =========================================================================
    # Configuration
    path(
        "chaos/config/safety-guard/",
        SafetyGuardConfigView.as_view(),
        name="chaos-config-safety-guard",
    ),
    path(
        "chaos/config/blast-radius/",
        BlastRadiusPolicyView.as_view(),
        name="chaos-config-blast-radius",
    ),
    path(
        "chaos/config/scheduler/",
        SchedulerConfigView.as_view(),
        name="chaos-config-scheduler",
    ),
    path("chaos/config/reports/", ReportConfigView.as_view(), name="chaos-config-reports"),
    # Safety Mechanism Configuration
    path(
        "chaos/config/stop-conditions/",
        StopConditionsConfigView.as_view(),
        name="chaos-config-stop-conditions",
    ),
    path("chaos/config/ttl/", TTLConfigView.as_view(), name="chaos-config-ttl"),
    path("chaos/config/dry-run/", DryRunConfigView.as_view(), name="chaos-config-dry-run"),
    # Dry Run Analysis with Impact Prediction
    path(
        "chaos/dry-run/analyze/",
        DryRunAnalysisView.as_view(),
        name="chaos-dry-run-analyze",
    ),
    # Scheduled Experiments CRUD
    path("chaos/schedules/", ScheduleListView.as_view(), name="chaos-schedules-list"),
    path(
        "chaos/schedules/<str:schedule_id>/",
        ScheduleDetailView.as_view(),
        name="chaos-schedule-detail",
    ),
    path(
        "chaos/schedules/<str:schedule_id>/approve/",
        ScheduleApprovalView.as_view(),
        name="chaos-schedule-approve",
    ),
    path(
        "chaos/schedules/<str:schedule_id>/execute/",
        ScheduleExecuteView.as_view(),
        name="chaos-schedule-execute",
    ),
    # Kill Switch
    path("chaos/kill-switch/", KillSwitchView.as_view(), name="chaos-kill-switch"),
    # Kill All Control
    path("chaos/control/kill-all/", KillAllView.as_view(), name="chaos-control-kill-all"),
    # Safety & Blast Radius Checks
    path("chaos/safety-check/", SafetyCheckView.as_view(), name="chaos-safety-check"),
    path(
        "chaos/blast-radius/check/",
        BlastRadiusCheckView.as_view(),
        name="chaos-blast-radius-check",
    ),
    # Reports
    path("chaos/reports/", ReportListView.as_view(), name="chaos-reports"),
    path(
        "chaos/reports/<str:report_id>/",
        ReportDetailView.as_view(),
        name="chaos-report-detail",
    ),
    path(
        "chaos/reports/generate/",
        ReportGenerateView.as_view(),
        name="chaos-reports-generate",
    ),
    path("chaos/reports/grades/", GradeHistoryView.as_view(), name="chaos-grade-history"),
    # Pending Approvals
    path(
        "chaos/pending-approvals/",
        PendingApprovalsView.as_view(),
        name="chaos-pending-approvals",
    ),
    # =========================================================================
    # L2 Storage Resilience API
    # =========================================================================
    # Configuration
    path("l2-storage/config/", L2StorageConfigView.as_view(), name="l2-storage-config"),
    path(
        "l2-storage/config/reset/",
        L2StorageConfigResetView.as_view(),
        name="l2-storage-config-reset",
    ),
    # Status & Health
    path("l2-storage/status/", L2StorageStatusView.as_view(), name="l2-storage-status"),
    path("l2-storage/health/", L2StorageHealthView.as_view(), name="l2-storage-health"),
    path(
        "l2-storage/health/reset/",
        L2StorageHealthResetView.as_view(),
        name="l2-storage-health-reset",
    ),
    # Shadow Log
    path(
        "l2-storage/shadow-log/",
        ShadowLogListView.as_view(),
        name="l2-storage-shadow-log",
    ),
    path(
        "l2-storage/shadow-log/stats/",
        ShadowLogStatsView.as_view(),
        name="l2-storage-shadow-log-stats",
    ),
    path(
        "l2-storage/shadow-log/clear/",
        ShadowLogClearView.as_view(),
        name="l2-storage-shadow-log-clear",
    ),
    path(
        "l2-storage/shadow-log/analyze/",
        ShadowLogAnalyzeView.as_view(),
        name="l2-storage-shadow-log-analyze",
    ),
    path(
        "l2-storage/shadow-log/replay/",
        ShadowLogReplayView.as_view(),
        name="l2-storage-shadow-log-replay",
    ),
    path(
        "l2-storage/shadow-log/service/<str:service_name>/",
        ShadowLogByServiceView.as_view(),
        name="l2-storage-shadow-log-by-service",
    ),
    # Sync Operations
    path(
        "l2-storage/sync/from-l2/",
        L2StorageSyncFromL2View.as_view(),
        name="l2-storage-sync-from-l2",
    ),
    path(
        "l2-storage/sync/to-l2/",
        L2StorageSyncToL2View.as_view(),
        name="l2-storage-sync-to-l2",
    ),
    # Drift Reconciliation
    path(
        "l2-storage/drift/stats/",
        DriftReconciliationStatsView.as_view(),
        name="l2-storage-drift-stats",
    ),
    path(
        "l2-storage/drift/history/",
        DriftReconciliationHistoryView.as_view(),
        name="l2-storage-drift-history",
    ),
    path(
        "l2-storage/drift/reconcile/",
        DriftReconciliationTriggerView.as_view(),
        name="l2-storage-drift-reconcile",
    ),
    path(
        "l2-storage/drift/reconcile/<str:service_name>/",
        DriftReconciliationServiceView.as_view(),
        name="l2-storage-drift-reconcile-service",
    ),
    # Metrics
    path("l2-storage/metrics/", L2StorageMetricsView.as_view(), name="l2-storage-metrics"),
]

# =============================================================================
# Stage DNA API Endpoints (Enterprise DNA Features)
# =============================================================================
try:
    from selfhealing.api.django.views.blast_radius import (
        BlastRadiusAssessmentView,
        BlastRadiusDependencyView,
        BlastRadiusGraphView,
        BlastRadiusIsolationView,
    )
    from selfhealing.api.django.views.blast_radius import (
        BlastRadiusPolicyView as DNABlastRadiusPolicyView,
    )
    from selfhealing.api.django.views.compliance_dna import (
        ComplianceCheckView,
        ComplianceReportView,
        ComplianceStandardsView,
        ComplianceViolationView,
    )
    from selfhealing.api.django.views.finops import (
        FinOpsAlertsView,
        FinOpsBudgetView,
        FinOpsCostView,
        FinOpsReportView,
    )
    from selfhealing.api.django.views.learning import (
        LearningInsightsView,
        LearningMetricView,
        LearningPatternView,
        LearningSessionView,
        LearningSuggestionView,
    )
    from selfhealing.api.django.views.rollback import (
        RollbackCancelView,
        RollbackExecuteView,
        RollbackHistoryView,
        RollbackPolicyView,
        RollbackRequestView,
    )

    urlpatterns += [
        # =====================================================================
        # FinOps DNA API - 비용 관리
        # =====================================================================
        path(
            "dna/finops/budget/",
            FinOpsBudgetView.as_view(),
            name="dna-finops-budget-list",
        ),
        path(
            "dna/finops/budget/<str:stage_name>/",
            FinOpsBudgetView.as_view(),
            name="dna-finops-budget",
        ),
        path("dna/finops/cost/", FinOpsCostView.as_view(), name="dna-finops-cost"),
        path("dna/finops/report/", FinOpsReportView.as_view(), name="dna-finops-report"),
        path("dna/finops/alerts/", FinOpsAlertsView.as_view(), name="dna-finops-alerts"),
        path(
            "dna/finops/alerts/<int:alert_index>/acknowledge/",
            FinOpsAlertsView.as_view(),
            name="dna-finops-alert-ack",
        ),
        # =====================================================================
        # Self-Learning DNA API - 패턴 학습 및 제안
        # =====================================================================
        path(
            "dna/learning/session/<str:action>/",
            LearningSessionView.as_view(),
            name="dna-learning-session",
        ),
        path(
            "dna/learning/patterns/",
            LearningPatternView.as_view(),
            name="dna-learning-patterns",
        ),
        path(
            "dna/learning/suggestions/",
            LearningSuggestionView.as_view(),
            name="dna-learning-suggestions",
        ),
        path(
            "dna/learning/suggestions/<str:suggestion_id>/apply/",
            LearningSuggestionView.as_view(),
            name="dna-learning-suggestion-apply",
        ),
        path(
            "dna/learning/metrics/",
            LearningMetricView.as_view(),
            name="dna-learning-metrics",
        ),
        path(
            "dna/learning/insights/",
            LearningInsightsView.as_view(),
            name="dna-learning-insights",
        ),
        # =====================================================================
        # Rollback DNA API - 안전한 롤백
        # =====================================================================
        path(
            "dna/rollback/policy/<str:stage_name>/",
            RollbackPolicyView.as_view(),
            name="dna-rollback-policy",
        ),
        path(
            "dna/rollback/request/",
            RollbackRequestView.as_view(),
            name="dna-rollback-request",
        ),
        path(
            "dna/rollback/request/<str:request_id>/",
            RollbackRequestView.as_view(),
            name="dna-rollback-request-detail",
        ),
        path(
            "dna/rollback/request/<str:request_id>/execute/",
            RollbackExecuteView.as_view(),
            name="dna-rollback-execute",
        ),
        path(
            "dna/rollback/request/<str:request_id>/cancel/",
            RollbackCancelView.as_view(),
            name="dna-rollback-cancel",
        ),
        path(
            "dna/rollback/history/",
            RollbackHistoryView.as_view(),
            name="dna-rollback-history",
        ),
        # =====================================================================
        # Blast Radius DNA API - 장애 영향 범위
        # =====================================================================
        path(
            "dna/blast-radius/policy/<str:stage_name>/",
            DNABlastRadiusPolicyView.as_view(),
            name="dna-blast-radius-policy",
        ),
        path(
            "dna/blast-radius/dependency/",
            BlastRadiusDependencyView.as_view(),
            name="dna-blast-radius-dependency-add",
        ),
        path(
            "dna/blast-radius/dependency/<str:service_name>/",
            BlastRadiusDependencyView.as_view(),
            name="dna-blast-radius-dependency",
        ),
        path(
            "dna/blast-radius/assessment/",
            BlastRadiusAssessmentView.as_view(),
            name="dna-blast-radius-assessment",
        ),
        path(
            "dna/blast-radius/isolation/",
            BlastRadiusIsolationView.as_view(),
            name="dna-blast-radius-isolation-list",
        ),
        path(
            "dna/blast-radius/isolation/<str:service_name>/",
            BlastRadiusIsolationView.as_view(),
            name="dna-blast-radius-isolation",
        ),
        path(
            "dna/blast-radius/graph/",
            BlastRadiusGraphView.as_view(),
            name="dna-blast-radius-graph",
        ),
        # =====================================================================
        # Compliance DNA API - 규정 준수
        # =====================================================================
        path(
            "dna/compliance/standards/",
            ComplianceStandardsView.as_view(),
            name="dna-compliance-standards",
        ),
        path(
            "dna/compliance/standards/<str:stage_name>/",
            ComplianceStandardsView.as_view(),
            name="dna-compliance-stage-standards",
        ),
        path(
            "dna/compliance/check/<str:stage_name>/",
            ComplianceCheckView.as_view(),
            name="dna-compliance-check",
        ),
        path(
            "dna/compliance/violations/",
            ComplianceViolationView.as_view(),
            name="dna-compliance-violations",
        ),
        path(
            "dna/compliance/violations/<str:violation_id>/resolve/",
            ComplianceViolationView.as_view(),
            name="dna-compliance-violation-resolve",
        ),
        path(
            "dna/compliance/reports/",
            ComplianceReportView.as_view(),
            name="dna-compliance-reports",
        ),
    ]
except ImportError:
    # DNA 서비스가 설치되지 않은 경우 스킵
    pass

# =============================================================================
# X-Test-Mode Endpoints (Chaos Proof)
# =============================================================================
# Always available but protected by X-Test-Mode header + environment checks
from selfhealing.api.django.views.xtest import (  # Stage 51: Observability & Blast Radius; DLQ X-Test Views; Replay X-Test Views; Retry X-Test Views; Rate Limit X-Test Views; Idempotency X-Test Views; Integration X-Test Views
    BackoffPreviewView,
    BlastRadiusTestView,
    CBStatusDetailView,
    CheckDuplicateView,
    ClearKeysView,
    DLQXTestStatusView,
    FastFailTestView,
    ForceStatusView,
    FullSnapshotView,
    GenerateKeyView,
    GetHealingIncidentsView,
    HealingTimelineView,
    IdempotencyStatusView,
    InjectCBFailureView,
    InjectDLQEntryView,
    InjectErrorBudgetView,
    MultiServiceBlastRadiusView,
    PostmortemGeneratorView,
    RateLimitClientView,
    RateLimitConfigXTestView,
    RateLimitHistoryView,
    RateLimitResetView,
    RateLimitStatusView,
    RecordHealingEventView,
    RegisterKeyView,
    ReplayBatchView,
    ReplaySingleView,
    ReplayStatusView,
    ResetCBView,
    ResetDLQXTestView,
    ResetView,
    RetryConfigView,
    RetryRateLimitStatusView,
    RetrySimulateView,
    RunScenarioView,
    ScenarioStatusView,
    SwitchToAutoModeView,
    SystemSnapshotView,
    TriggerCBRecoveryView,
    TriggerReplayOnCBCloseView,
    TryRecoveryTransitionView,
)

urlpatterns += [
    # X-Test-Mode: Chaos Monkey API (requires X-Test-Mode: chaos-monkey header)
    path(
        "xtest/inject-cb-failure/",
        InjectCBFailureView.as_view(),
        name="xtest-inject-cb-failure",
    ),
    path("xtest/reset-cb/", ResetCBView.as_view(), name="xtest-reset-cb"),
    path("xtest/cb-status/", CBStatusDetailView.as_view(), name="xtest-cb-status"),
    path(
        "xtest/switch-to-auto/",
        SwitchToAutoModeView.as_view(),
        name="xtest-switch-to-auto",
    ),  # New!
    path(
        "xtest/try-recovery-transition/",
        TryRecoveryTransitionView.as_view(),
        name="xtest-try-recovery-transition",
    ),  # Domain-free
    path(
        "xtest/inject-error-budget/",
        InjectErrorBudgetView.as_view(),
        name="xtest-inject-error-budget",
    ),
    path("xtest/snapshot/", SystemSnapshotView.as_view(), name="xtest-snapshot"),
    path("xtest/fast-fail-test/", FastFailTestView.as_view(), name="xtest-fast-fail-test"),
    path(
        "xtest/trigger-cb-recovery/",
        TriggerCBRecoveryView.as_view(),
        name="xtest-trigger-cb-recovery",
    ),
    # Stage 51: Observability & Blast Radius
    path(
        "xtest/healing-timeline/",
        HealingTimelineView.as_view(),
        name="xtest-healing-timeline",
    ),
    path(
        "xtest/blast-radius-test/",
        BlastRadiusTestView.as_view(),
        name="xtest-blast-radius-test",
    ),
    path(
        "xtest/generate-postmortem/",
        PostmortemGeneratorView.as_view(),
        name="xtest-generate-postmortem",
    ),
    path(
        "xtest/record-healing-event/",
        RecordHealingEventView.as_view(),
        name="xtest-record-healing-event",
    ),
    path(
        "xtest/healing-incidents/",
        GetHealingIncidentsView.as_view(),
        name="xtest-healing-incidents",
    ),
    path(
        "xtest/multi-blast-radius/",
        MultiServiceBlastRadiusView.as_view(),
        name="xtest-multi-blast-radius",
    ),
    # DLQ X-Test Endpoints
    path("xtest/dlq/inject/", InjectDLQEntryView.as_view(), name="xtest-dlq-inject"),
    path("xtest/dlq/status/", DLQXTestStatusView.as_view(), name="xtest-dlq-status"),
    path(
        "xtest/dlq/force-status/",
        ForceStatusView.as_view(),
        name="xtest-dlq-force-status",
    ),
    path("xtest/dlq/reset/", ResetDLQXTestView.as_view(), name="xtest-dlq-reset"),
    # Replay X-Test Endpoints
    path("xtest/replay/single/", ReplaySingleView.as_view(), name="xtest-replay-single"),
    path("xtest/replay/batch/", ReplayBatchView.as_view(), name="xtest-replay-batch"),
    path(
        "xtest/replay/trigger-on-cb-close/",
        TriggerReplayOnCBCloseView.as_view(),
        name="xtest-replay-trigger-cb-close",
    ),
    path("xtest/replay/status/", ReplayStatusView.as_view(), name="xtest-replay-status"),
    # Retry X-Test Endpoints
    path(
        "xtest/retry/backoff-preview/",
        BackoffPreviewView.as_view(),
        name="xtest-retry-backoff-preview",
    ),
    path(
        "xtest/retry/simulate/",
        RetrySimulateView.as_view(),
        name="xtest-retry-simulate",
    ),
    path(
        "xtest/retry/rate-limit-status/",
        RetryRateLimitStatusView.as_view(),
        name="xtest-retry-rate-limit-status",
    ),
    path("xtest/retry/config/", RetryConfigView.as_view(), name="xtest-retry-config"),
    # Rate Limit X-Test Endpoints
    path(
        "xtest/rate-limit/status/",
        RateLimitStatusView.as_view(),
        name="xtest-rate-limit-status",
    ),
    path(
        "xtest/rate-limit/client/",
        RateLimitClientView.as_view(),
        name="xtest-rate-limit-client",
    ),
    path(
        "xtest/rate-limit/history/",
        RateLimitHistoryView.as_view(),
        name="xtest-rate-limit-history",
    ),
    path(
        "xtest/rate-limit/config/",
        RateLimitConfigXTestView.as_view(),
        name="xtest-rate-limit-config",
    ),
    path(
        "xtest/rate-limit/reset/",
        RateLimitResetView.as_view(),
        name="xtest-rate-limit-reset",
    ),
    # Idempotency X-Test Endpoints
    path(
        "xtest/idempotency/generate-key/",
        GenerateKeyView.as_view(),
        name="xtest-idempotency-generate-key",
    ),
    path(
        "xtest/idempotency/check-duplicate/",
        CheckDuplicateView.as_view(),
        name="xtest-idempotency-check-duplicate",
    ),
    path(
        "xtest/idempotency/status/",
        IdempotencyStatusView.as_view(),
        name="xtest-idempotency-status",
    ),
    path(
        "xtest/idempotency/register/",
        RegisterKeyView.as_view(),
        name="xtest-idempotency-register",
    ),
    path(
        "xtest/idempotency/clear/",
        ClearKeysView.as_view(),
        name="xtest-idempotency-clear",
    ),
    # Integration X-Test Endpoints (Component Integration Tests)
    path(
        "xtest/integration/run-scenario/",
        RunScenarioView.as_view(),
        name="xtest-integration-run-scenario",
    ),
    path(
        "xtest/integration/scenario/<str:scenario_id>/",
        ScenarioStatusView.as_view(),
        name="xtest-integration-scenario-status",
    ),
    path(
        "xtest/integration/full-snapshot/",
        FullSnapshotView.as_view(),
        name="xtest-integration-full-snapshot",
    ),
    path("xtest/integration/reset/", ResetView.as_view(), name="xtest-integration-reset"),
    # =========================================================================
    # Recovery Coordinator API - 복구 프로세스 관리
    # =========================================================================
    # Recovery Status
    path("recovery/status/", RecoveryStatusView.as_view(), name="recovery-status"),
    # Recovery Actions
    path("recovery/start/", RecoveryStartView.as_view(), name="recovery-start"),
    path("recovery/abort/", RecoveryAbortView.as_view(), name="recovery-abort"),
    # Pending Approvals
    path(
        "recovery/pending-approvals/",
        RecoveryPendingApprovalsView.as_view(),
        name="recovery-pending-approvals",
    ),
    path("recovery/approve/", RecoveryApproveView.as_view(), name="recovery-approve"),
    path("recovery/reject/", RecoveryRejectView.as_view(), name="recovery-reject"),
    # Recovery History
    path("recovery/history/", RecoveryHistoryView.as_view(), name="recovery-history"),
    # Dashboard Widget
    path(
        "recovery/widget/",
        RecoveryDashboardWidgetView.as_view(),
        name="recovery-widget",
    ),
    # =========================================================================
    # Canary Rollout API - 설정 변경의 점진적 배포
    # Reference: docs/self_healing/middleware_system/71_CANARY_CONFIG_ROLLOUT.md
    # =========================================================================
    # List & Create
    path("canary/rollouts/", CanaryRolloutListView.as_view(), name="canary-rollout-list"),
    # History (completed rollouts)
    path("canary/history/", CanaryHistoryView.as_view(), name="canary-history"),
    # Panic Rollback (all active rollouts)
    path(
        "canary/panic-rollback/",
        CanaryPanicRollbackView.as_view(),
        name="canary-panic-rollback",
    ),
    # Detail
    path(
        "canary/rollouts/<str:rollout_id>/",
        CanaryRolloutDetailView.as_view(),
        name="canary-rollout-detail",
    ),
    # Metrics
    path(
        "canary/rollouts/<str:rollout_id>/metrics/",
        CanaryMetricsView.as_view(),
        name="canary-rollout-metrics",
    ),
    # Actions: start, promote, rollback, pause, resume, cancel
    path(
        "canary/rollouts/<str:rollout_id>/<str:action>/",
        CanaryRolloutActionView.as_view(),
        name="canary-rollout-action",
    ),
]

# Stress Test Endpoints - DEBUG 모드에서만 활성화 (프로덕션 제외)
if getattr(settings, "DEBUG", False) or getattr(settings, "ENABLE_STRESS_TESTS", False):
    from selfhealing.api.django.stress_views import (  # 🔥 Advisory Lock API - 비침투적 DB 락 테스트; 🔥 Pool Exhaustion & CB Trigger API
        advisory_lock_acquire,
        advisory_lock_contention,
        connection_leak_simulation,
        controlled_burst_failure,
        heavy_concurrent_query,
        pool_exhaust,
        pool_status,
        slow_query_5s,
        slow_query_10s,
        trigger_cb_failure,
    )

    urlpatterns += [
        path("stress/slow-5s/", slow_query_5s, name="stress-slow-5s"),
        path("stress/slow-10s/", slow_query_10s, name="stress-slow-10s"),
        path("stress/leak/", connection_leak_simulation, name="stress-leak"),
        path("stress/pool-status/", pool_status, name="stress-pool-status"),
        path("stress/heavy-query/", heavy_concurrent_query, name="stress-heavy-query"),
        # 🔥 Advisory Lock API - 비침투적 DB 락 테스트
        path(
            "stress/advisory-lock/acquire/",
            advisory_lock_acquire,
            name="stress-advisory-lock-acquire",
        ),
        path(
            "stress/advisory-lock/contention/",
            advisory_lock_contention,
            name="stress-advisory-lock-contention",
        ),
        path(
            "stress/burst-failure/",
            controlled_burst_failure,
            name="stress-burst-failure",
        ),
        # 🔥 Pool Exhaustion & CB Trigger API
        path("stress/pool-exhaust/", pool_exhaust, name="stress-pool-exhaust"),
        path(
            "stress/trigger-cb-failure/",
            trigger_cb_failure,
            name="stress-trigger-cb-failure",
        ),
    ]
