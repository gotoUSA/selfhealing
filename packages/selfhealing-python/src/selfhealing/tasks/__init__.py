"""
Self-Healing Celery Tasks

This package contains task definitions and base classes for self-healing
autonomous operations. Tasks are designed to be framework-agnostic and
can be registered with any task queue system (Celery, RQ, etc.).

Key Components:
- BaseNotifyingTask: Base class with built-in notifications
- NotificationPolicy: Configurable notification policies
- SLADriftDetector: SLA drift detection logic
- ForensicAnalyzer: Forensic analysis for pending operations

Usage:
    from selfhealing.tasks import (
        BaseNotifyingTask,
        NotificationPolicy,
        NotificationTiming,
        DailyAutonomousReport,
    )

Reference: docs/self_healing/middleware_system/08_NOTIFICATION_ARCHITECTURE.md
"""

from .base_notifying_task import (
    BaseNotifyingTask,
    NotificationPolicy,
    NotificationTiming,
    NotificationThreshold,
    DailyAutonomousReport,
    reset_cooldowns,
    get_cooldown_status,
)

from .drift_detection import (
    SLADriftDetector,
    ForensicAnalyzer,
)

from .daily_report import (
    DailyReportData,
    DailyReportCollector,
    TaskResultEntry,
    get_daily_report_collector,
    generate_daily_autonomous_report,
    get_daily_report_beat_schedule,
)

from .cleanup_tasks import (
    ArchiveOldDLQEntriesTask,
    CleanupExpiredConfigTask,
    ExpireApprovalRequestsTask,
    PurgeArchivedDLQEntriesTask,
    CLEANUP_TASKS,
    register_cleanup_tasks_with_celery,
    get_cleanup_beat_schedule,
)

from .intelligence_tasks import (
    CheckSLADriftTask,
    AnalyzeForensicPendingTask,
    AnalyzeCrossStageInsightsTask,
    CheckRecoveryTransitionsTask,
    INTELLIGENCE_TASKS,
    register_intelligence_tasks_with_celery,
    get_intelligence_beat_schedule,
)

from .compliance_tasks import (
    RunComplianceCheckTask,
    GenerateFinOpsReportTask,
    CollectSelfHealingMetricsTask,
    GenerateDailyAutonomousReportTask,
    COMPLIANCE_TASKS,
    register_compliance_tasks_with_celery,
    get_compliance_beat_schedule,
)

__all__ = [
    # Base Notifying Task
    "BaseNotifyingTask",
    "NotificationPolicy",
    "NotificationTiming",
    "NotificationThreshold",
    "DailyAutonomousReport",
    "reset_cooldowns",
    "get_cooldown_status",
    # Drift Detection
    "SLADriftDetector",
    "ForensicAnalyzer",
    # Daily Report
    "DailyReportData",
    "DailyReportCollector",
    "TaskResultEntry",
    "get_daily_report_collector",
    "generate_daily_autonomous_report",
    "get_daily_report_beat_schedule",
    # Cleanup Tasks (청소부 레인)
    "ArchiveOldDLQEntriesTask",
    "CleanupExpiredConfigTask",
    "ExpireApprovalRequestsTask",
    "PurgeArchivedDLQEntriesTask",
    "CLEANUP_TASKS",
    "register_cleanup_tasks_with_celery",
    "get_cleanup_beat_schedule",
    # Intelligence Tasks (지능 레인)
    "CheckSLADriftTask",
    "AnalyzeForensicPendingTask",
    "AnalyzeCrossStageInsightsTask",
    "CheckRecoveryTransitionsTask",
    "INTELLIGENCE_TASKS",
    "register_intelligence_tasks_with_celery",
    "get_intelligence_beat_schedule",
    # Compliance Tasks (증명 레인)
    "RunComplianceCheckTask",
    "GenerateFinOpsReportTask",
    "CollectSelfHealingMetricsTask",
    "GenerateDailyAutonomousReportTask",
    "COMPLIANCE_TASKS",
    "register_compliance_tasks_with_celery",
    "get_compliance_beat_schedule",
]
