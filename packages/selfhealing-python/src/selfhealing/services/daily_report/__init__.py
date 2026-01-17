"""
Daily Report Service Package

Provides daily report generation and notification services.

일일 보고서 생성 및 알림 서비스를 제공합니다.

Module Structure:
- models.py: TaskResultEntry, DailyAutonomousReport
- aggregator.py: DailyReportCollector, aggregate_daily_results
- formatters.py: format_report_for_slack, format_report_for_email
- service.py: DailyReportService
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
