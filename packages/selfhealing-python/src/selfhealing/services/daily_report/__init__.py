"""
Daily Report Service Package

Provides daily report generation and notification services.

Module Structure:
- models.py: TaskResultEntry, DailyAutonomousReport
- aggregator.py: DailyReportCollector, aggregate_daily_results
- formatters.py: format_report_for_slack, format_report_for_email
- service.py: DailyReportService

Reference: docs/self_healing/middleware_system/09_AUTONOMOUS_TASK_EXPANSION.md §6
"""

from .models import TaskResultEntry, DailyAutonomousReport, DailyReportData
from .aggregator import (
    DAILY_REPORT_CACHE_KEY_PREFIX,
    DailyReportCollector,
    get_daily_report_collector,
    reset_daily_report_collector,
    aggregate_daily_results,
)
from .formatters import format_report_for_slack, format_report_for_email
from .service import (
    ReportResult,
    DailyReportService,
    get_daily_report_service,
    reset_daily_report_service,
)

__all__ = [
    # Models
    "TaskResultEntry",
    "DailyAutonomousReport",
    "DailyReportData",
    # Aggregator
    "DAILY_REPORT_CACHE_KEY_PREFIX",
    "DailyReportCollector",
    "get_daily_report_collector",
    "reset_daily_report_collector",
    "aggregate_daily_results",
    # Formatters
    "format_report_for_slack",
    "format_report_for_email",
    # Service
    "ReportResult",
    "DailyReportService",
    "get_daily_report_service",
    "reset_daily_report_service",
]
