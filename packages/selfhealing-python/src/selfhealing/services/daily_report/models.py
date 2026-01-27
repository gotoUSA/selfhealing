"""
Daily Report Data Models.

Contains data classes for task results and daily report aggregation.
일일 자율 운영 보고서 데이터 집계를 위한 데이터 클래스.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class TaskResultEntry:
    """Individual task result entry for aggregation."""

    task_name: str
    result: dict[str, Any]
    timestamp: datetime
    severity: str = "info"


@dataclass
class DailyAutonomousReport:
    """
    Daily autonomous operations summary report.

    Aggregates counts and statistics from all self-healing tasks
    executed during the day. This is the main class for daily report aggregation.

    Contains counts and statistics from all self-healing tasks
    executed during the day.
    """

    date: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Core counts
    archived_count: int = 0
    expired_count: int = 0
    purged_count: int = 0
    approval_expired_count: int = 0
    recovered_count: int = 0
    drift_warnings_count: int = 0

    # Circuit breaker stats
    circuit_transitions: int = 0
    circuits_opened: int = 0
    circuits_closed: int = 0

    # Error counts
    task_failures: int = 0
    critical_alerts: int = 0

    # Custom metrics (extensible)
    custom_counts: dict[str, int] = field(default_factory=dict)

    # Raw entries for detailed analysis
    entries: list[TaskResultEntry] = field(default_factory=list)

    def add_entry(self, entry: TaskResultEntry) -> None:
        """Add a task result entry and update counts."""
        self.entries.append(entry)
        self._update_counts_from_entry(entry)

    def _update_counts_from_entry(self, entry: TaskResultEntry) -> None:
        """Update aggregate counts based on entry result."""
        result = entry.result

        # Map result fields to our counts
        field_mapping = {
            "archived_count": "archived_count",
            "expired_count": "expired_count",
            "purged_count": "purged_count",
            "approval_expired_count": "approval_expired_count",
            "recovered_count": "recovered_count",
            "drift_warnings_count": "drift_warnings_count",
            "circuit_transitions": "circuit_transitions",
            "circuits_opened": "circuits_opened",
            "circuits_closed": "circuits_closed",
        }

        for result_field, attr_name in field_mapping.items():
            if result_field in result:
                current = getattr(self, attr_name)
                setattr(self, attr_name, current + int(result[result_field]))

        # Track failures
        if result.get("error") or result.get("success") is False:
            self.task_failures += 1

        # Track critical alerts
        if entry.severity == "critical":
            self.critical_alerts += 1

    def merge(self, other: DailyAutonomousReport) -> None:
        """Merge another report's data into this one."""
        self.archived_count += other.archived_count
        self.expired_count += other.expired_count
        self.purged_count += other.purged_count
        self.approval_expired_count += other.approval_expired_count
        self.recovered_count += other.recovered_count
        self.drift_warnings_count += other.drift_warnings_count
        self.circuit_transitions += other.circuit_transitions
        self.circuits_opened += other.circuits_opened
        self.circuits_closed += other.circuits_closed
        self.task_failures += other.task_failures
        self.critical_alerts += other.critical_alerts

        for key, value in other.custom_counts.items():
            self.custom_counts[key] = self.custom_counts.get(key, 0) + value

        self.entries.extend(other.entries)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "date": self.date.isoformat(),
            "archived_count": self.archived_count,
            "expired_count": self.expired_count,
            "purged_count": self.purged_count,
            "approval_expired_count": self.approval_expired_count,
            "recovered_count": self.recovered_count,
            "drift_warnings_count": self.drift_warnings_count,
            "circuit_transitions": self.circuit_transitions,
            "circuits_opened": self.circuits_opened,
            "circuits_closed": self.circuits_closed,
            "task_failures": self.task_failures,
            "critical_alerts": self.critical_alerts,
            "custom_counts": self.custom_counts,
            "entry_count": len(self.entries),
        }


# Backward compatibility alias
DailyReportData = DailyAutonomousReport


__all__ = [
    "TaskResultEntry",
    "DailyAutonomousReport",
    "DailyReportData",
]
