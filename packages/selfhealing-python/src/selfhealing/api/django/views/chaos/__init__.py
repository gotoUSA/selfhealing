"""
Chaos Engineering API Views Package.

Full API control for the Chaos Engineering system.
All settings and operations are controllable via these endpoints.

Usage:
    # Recommended: Import from specific submodules
    from selfhealing.api.django.views.chaos.config_views import SafetyGuardConfigView
    from selfhealing.api.django.views.chaos.schedule_views import ScheduleListView
    from selfhealing.api.django.views.chaos.safety_views import KillSwitchView
    from selfhealing.api.django.views.chaos.report_views import ReportListView

    # Legacy: Still works via lazy import (backward compatible)
    from selfhealing.api.django.views.chaos import SafetyGuardConfigView

Submodules:
    - config_views: Configuration management (SafetyGuard, BlastRadius, Scheduler, Report)
    - schedule_views: Scheduled experiments CRUD and execution
    - safety_views: Kill switch, safety checks, TTL, dry-run
    - report_views: Report generation and history
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# =============================================================================
# LAZY IMPORTS - All views loaded on-demand
# =============================================================================

# Mapping of symbol names to their module paths
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # config_views.py
    "SafetyGuardConfigView": ("selfhealing.api.django.views.chaos.config_views", "SafetyGuardConfigView"),
    "BlastRadiusPolicyView": ("selfhealing.api.django.views.chaos.config_views", "BlastRadiusPolicyView"),
    "SchedulerConfigView": ("selfhealing.api.django.views.chaos.config_views", "SchedulerConfigView"),
    "ReportConfigView": ("selfhealing.api.django.views.chaos.config_views", "ReportConfigView"),
    # schedule_views.py
    "ScheduleListView": ("selfhealing.api.django.views.chaos.schedule_views", "ScheduleListView"),
    "ScheduleDetailView": ("selfhealing.api.django.views.chaos.schedule_views", "ScheduleDetailView"),
    "ScheduleApprovalView": ("selfhealing.api.django.views.chaos.schedule_views", "ScheduleApprovalView"),
    "ScheduleExecuteView": ("selfhealing.api.django.views.chaos.schedule_views", "ScheduleExecuteView"),
    "PendingApprovalsView": ("selfhealing.api.django.views.chaos.schedule_views", "PendingApprovalsView"),
    # safety_views.py
    "KillSwitchView": ("selfhealing.api.django.views.chaos.safety_views", "KillSwitchView"),
    "SafetyCheckView": ("selfhealing.api.django.views.chaos.safety_views", "SafetyCheckView"),
    "BlastRadiusCheckView": ("selfhealing.api.django.views.chaos.safety_views", "BlastRadiusCheckView"),
    "StopConditionsConfigView": ("selfhealing.api.django.views.chaos.safety_views", "StopConditionsConfigView"),
    "TTLConfigView": ("selfhealing.api.django.views.chaos.safety_views", "TTLConfigView"),
    "DryRunConfigView": ("selfhealing.api.django.views.chaos.safety_views", "DryRunConfigView"),
    "KillAllView": ("selfhealing.api.django.views.chaos.safety_views", "KillAllView"),
    # report_views.py
    "ReportListView": ("selfhealing.api.django.views.chaos.report_views", "ReportListView"),
    "ReportDetailView": ("selfhealing.api.django.views.chaos.report_views", "ReportDetailView"),
    "ReportGenerateView": ("selfhealing.api.django.views.chaos.report_views", "ReportGenerateView"),
    "GradeHistoryView": ("selfhealing.api.django.views.chaos.report_views", "GradeHistoryView"),
    "DryRunAnalysisView": ("selfhealing.api.django.views.chaos.report_views", "DryRunAnalysisView"),
}

# Cache for lazily loaded modules
_loaded_symbols: dict[str, object] = {}


def __getattr__(name: str) -> object:
    """Lazy import for backward compatibility.
    
    This allows:
        from selfhealing.api.django.views.chaos import SafetyGuardConfigView
    
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
    
    raise AttributeError(f"module 'selfhealing.api.django.views.chaos' has no attribute '{name}'")


def __dir__() -> list[str]:
    """List available symbols for IDE autocompletion."""
    return list(__all__)


# TYPE_CHECKING block for IDE support without runtime import
if TYPE_CHECKING:
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


# =============================================================================
# __all__ - All views available (via lazy import)
# =============================================================================
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
