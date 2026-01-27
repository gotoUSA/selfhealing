"""
Daily Report Aggregation Logic.

Collects and aggregates task results from cache/storage.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from selfhealing.settings.daily_report import get_daily_report_settings

from .models import DailyAutonomousReport, TaskResultEntry

logger = logging.getLogger(__name__)

DAILY_REPORT_CACHE_KEY_PREFIX = "selfhealing:daily_report"


class DailyReportCollector:
    """
    Collects and stores task results for daily aggregation.

    Uses Django cache or Redis for storage, with in-memory fallback.
    """

    def __init__(self):
        self._memory_storage: dict[str, list[TaskResultEntry]] = {}

    def add_result(
        self,
        task_name: str,
        result: dict[str, Any],
        severity: str = "info",
    ) -> None:
        """Add a task result to today's report."""
        from selfhealing.core.timezone import now

        entry = TaskResultEntry(
            task_name=task_name,
            result=result,
            timestamp=now(),
            severity=severity,
        )

        date_key = now().strftime("%Y-%m-%d")

        # Try Django cache first
        try:
            from django.core.cache import cache

            cache_key = f"{DAILY_REPORT_CACHE_KEY_PREFIX}:{date_key}"
            current = cache.get(cache_key, [])
            current.append(
                {
                    "task_name": entry.task_name,
                    "result": entry.result,
                    "timestamp": entry.timestamp.isoformat(),
                    "severity": entry.severity,
                }
            )
            _settings = get_daily_report_settings()
            cache.set(cache_key, current, timeout=_settings.cache_ttl)

        except ImportError:
            # Fallback to memory storage
            if date_key not in self._memory_storage:
                self._memory_storage[date_key] = []
            self._memory_storage[date_key].append(entry)
        except Exception as e:
            logger.warning(f"[DailyReportCollector] Cache error: {e}")
            if date_key not in self._memory_storage:
                self._memory_storage[date_key] = []
            self._memory_storage[date_key].append(entry)

    def get_report(self, date: datetime | None = None) -> DailyAutonomousReport:
        """Get aggregated report for a specific date (default: yesterday)."""
        from selfhealing.core.timezone import now

        if date is None:
            # Default to yesterday
            date = now() - timedelta(days=1)

        date_key = date.strftime("%Y-%m-%d")
        report = DailyAutonomousReport(date=date)

        # Try Django cache first
        try:
            from django.core.cache import cache

            cache_key = f"{DAILY_REPORT_CACHE_KEY_PREFIX}:{date_key}"
            entries = cache.get(cache_key, [])

            for entry_dict in entries:
                entry = TaskResultEntry(
                    task_name=entry_dict["task_name"],
                    result=entry_dict["result"],
                    timestamp=datetime.fromisoformat(entry_dict["timestamp"]),
                    severity=entry_dict.get("severity", "info"),
                )
                report.add_entry(entry)

        except ImportError:
            # Fallback to memory storage
            for entry in self._memory_storage.get(date_key, []):
                report.add_entry(entry)
        except Exception as e:
            logger.warning(f"[DailyReportCollector] Cache read error: {e}")
            for entry in self._memory_storage.get(date_key, []):
                report.add_entry(entry)

        return report

    def clear_old_data(self, days_to_keep: int = 7) -> int:
        """Clear data older than specified days."""
        from selfhealing.core.timezone import now

        cutoff = now() - timedelta(days=days_to_keep)
        cleared = 0

        # Clear memory storage
        keys_to_remove = []
        for date_key in self._memory_storage:
            try:
                entry_date = datetime.strptime(date_key, "%Y-%m-%d")
                entry_date = entry_date.replace(tzinfo=timezone.utc)
                if entry_date < cutoff:
                    keys_to_remove.append(date_key)
            except ValueError:
                pass

        for key in keys_to_remove:
            del self._memory_storage[key]
            cleared += 1

        return cleared


# Module-level singleton
_collector: DailyReportCollector | None = None


def get_daily_report_collector() -> DailyReportCollector:
    """Get the singleton DailyReportCollector instance."""
    global _collector
    if _collector is None:
        _collector = DailyReportCollector()
    return _collector


def reset_daily_report_collector() -> None:
    """Reset the collector instance (for testing)."""
    global _collector
    _collector = None


def aggregate_daily_results(
    date: datetime | None = None,
) -> DailyAutonomousReport:
    """
    Aggregate cached task results into a daily report.

    This is a convenience function that uses the singleton collector.

    Args:
        date: Report date (default: yesterday)

    Returns:
        DailyAutonomousReport instance
    """
    collector = get_daily_report_collector()
    return collector.get_report(date)


__all__ = [
    "DAILY_REPORT_CACHE_KEY_PREFIX",
    "DailyReportCollector",
    "get_daily_report_collector",
    "reset_daily_report_collector",
    "aggregate_daily_results",
]
