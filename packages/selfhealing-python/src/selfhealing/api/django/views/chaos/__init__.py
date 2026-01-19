"""
Chaos Engineering API Views Package.

Full API control for the Chaos Engineering system.
All settings and operations are controllable via these endpoints.

Endpoints:
- /chaos/config/safety-guard/ - Safety guard configuration
- /chaos/config/blast-radius/ - Blast radius policy
- /chaos/config/scheduler/ - Scheduler configuration
- /chaos/config/reports/ - Report configuration
- /chaos/schedules/ - Scheduled experiments CRUD
- /chaos/schedules/{id}/approve/ - Approve/deny experiments
- /chaos/schedules/{id}/execute/ - Execute immediately
- /chaos/kill-switch/ - Kill switch controls
- /chaos/safety-check/ - Run safety checks
- /chaos/blast-radius/check/ - Check blast radius
- /chaos/reports/ - Get resilience reports
- /chaos/reports/generate/ - Generate report now
"""

from selfhealing.api.django.views.chaos.config_views import (
    SafetyGuardConfigView,
    BlastRadiusPolicyView,
    SchedulerConfigView,
    ReportConfigView,
)
from selfhealing.api.django.views.chaos.schedule_views import (
    ScheduleListView,
    ScheduleDetailView,
    ScheduleApprovalView,
    ScheduleExecuteView,
    PendingApprovalsView,
)
from selfhealing.api.django.views.chaos.safety_views import (
    KillSwitchView,
    SafetyCheckView,
    BlastRadiusCheckView,
    StopConditionsConfigView,
    TTLConfigView,
    DryRunConfigView,
    KillAllView,
)
from selfhealing.api.django.views.chaos.report_views import (
    ReportListView,
    ReportDetailView,
    ReportGenerateView,
    GradeHistoryView,
    DryRunAnalysisView,
)

__all__ = [
    # Config Views
    "SafetyGuardConfigView",
    "BlastRadiusPolicyView",
    "SchedulerConfigView",
    "ReportConfigView",
    # Schedule Views
    "ScheduleListView",
    "ScheduleDetailView",
    "ScheduleApprovalView",
    "ScheduleExecuteView",
    "PendingApprovalsView",
    # Safety Views
    "KillSwitchView",
    "SafetyCheckView",
    "BlastRadiusCheckView",
    "StopConditionsConfigView",
    "TTLConfigView",
    "DryRunConfigView",
    "KillAllView",
    # Report Views
    "ReportListView",
    "ReportDetailView",
    "ReportGenerateView",
    "GradeHistoryView",
    "DryRunAnalysisView",
]
