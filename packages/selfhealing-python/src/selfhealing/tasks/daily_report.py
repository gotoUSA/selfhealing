"""
Daily Autonomous Report Task

Thin Task, Fat Service Pattern:
- 태스크는 서비스 호출만 담당
- 모든 비즈니스 로직은 services/daily_report/ 패키지에 위치

스케줄: 매일 09:00
큐: reports
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# Thin Wrapper Functions (delegate to service)
# =============================================================================


def generate_daily_autonomous_report(
    date: datetime | None = None,
    channels: list[str] | None = None,
) -> dict[str, Any]:
    """
    Generate and send daily autonomous operations report.

    This is a Thin Wrapper that delegates to DailyReportService.

    Args:
        date: Report date (default: yesterday)
        channels: Notification channels (default: ["slack"])

    Returns:
        dict: Report generation result
    """
    from selfhealing.services.daily_report import get_daily_report_service

    service = get_daily_report_service()
    result = service.generate_and_send_report(date=date, channels=channels)
    return result.to_dict()


# =============================================================================
# Celery Task Registration
# =============================================================================

try:
    from celery import shared_task

    from selfhealing.settings.daily_report import get_daily_report_settings

    # 모듈 로드 시점에 설정값 캐싱
    _daily_report_settings = get_daily_report_settings()

    @shared_task(
        name="selfhealing.tasks.daily_report.generate_daily_autonomous_report",
        bind=False,
        max_retries=_daily_report_settings.max_retries,
        default_retry_delay=_daily_report_settings.retry_delay,
    )
    def generate_daily_autonomous_report_task(
        date_str: str | None = None,
        channels: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Celery task wrapper for daily report generation.

        Args:
            date_str: ISO format date string (default: yesterday)
            channels: Notification channels
        """
        date = None
        if date_str:
            date = datetime.fromisoformat(date_str)

        return generate_daily_autonomous_report(date=date, channels=channels)

except ImportError:
    logger.debug("[DailyReport] Celery not available, skipping task registration")


# =============================================================================
# Beat Schedule
# =============================================================================


def get_daily_report_beat_schedule() -> dict[str, dict[str, Any]]:
    """
    Get Celery Beat schedule for daily report task.

    Returns:
        dict: Celery Beat schedule configuration

    Usage:
        from selfhealing.tasks.daily_report import get_daily_report_beat_schedule
        CELERY_BEAT_SCHEDULE.update(get_daily_report_beat_schedule())
    """
    return {
        "generate-daily-autonomous-report": {
            "task": "selfhealing.tasks.daily_report.generate_daily_autonomous_report",
            "schedule": {
                "hour": 9,
                "minute": 0,
            },
            "options": {
                "queue": "reports",
            },
        },
    }


# =============================================================================
# Backward Compatibility Re-exports (Lazy Import)
# =============================================================================


def __getattr__(name: str):
    """Lazy import for backward compatibility types."""
    _lazy_imports = {
        "TaskResultEntry",
        "DailyAutonomousReport",
        "DailyReportData",
        "DailyReportCollector",
        "get_daily_report_collector",
        "DAILY_REPORT_CACHE_KEY_PREFIX",
    }
    if name in _lazy_imports:
        from selfhealing.services import daily_report as dr_module

        return getattr(dr_module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class _LegacyTaskWrapper:
    """Backward compatibility wrapper for class-based task."""

    name = "selfhealing.generate_daily_autonomous_report"

    def run(self) -> dict[str, Any]:
        """Run via service delegation."""
        return generate_daily_autonomous_report()


# Legacy class-based task alias
GenerateDailyAutonomousReportTask = _LegacyTaskWrapper


__all__ = [
    # Main functions
    "generate_daily_autonomous_report",
    "generate_daily_autonomous_report_task",
    "get_daily_report_beat_schedule",
    # Backward compatibility exports
    "TaskResultEntry",
    "DailyAutonomousReport",
    "DailyReportData",
    "DailyReportCollector",
    "get_daily_report_collector",
    "GenerateDailyAutonomousReportTask",
    "DAILY_REPORT_CACHE_KEY_PREFIX",
]
