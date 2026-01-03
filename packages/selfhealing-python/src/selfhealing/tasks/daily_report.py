"""
Daily Autonomous Report Task

Aggregates daily self-healing operations into a single summary notification,
reducing notification fatigue while maintaining visibility.

Key Features:
- Collects cached task results from the day
- Generates formatted Slack/email summary
- Runs once daily at configured time (default: 09:00)

Reference: docs/self_healing/middleware_system/08_NOTIFICATION_ARCHITECTURE.md §3.1
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

DAILY_REPORT_CACHE_KEY_PREFIX = "selfhealing:daily_report"


# =============================================================================
# Daily Report Data Classes
# =============================================================================


@dataclass
class TaskResultEntry:
    """Individual task result entry for aggregation."""

    task_name: str
    result: Dict[str, Any]
    timestamp: datetime
    severity: str = "info"


@dataclass
class DailyAutonomousReport:
    """
    Daily autonomous operations summary report.
    
    Aggregates counts and statistics from all self-healing tasks
    executed during the day. This is the main class specified in
    09_AUTONOMOUS_TASK_EXPANSION.md §6.1 for daily report aggregation.
    
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
    custom_counts: Dict[str, int] = field(default_factory=dict)
    
    # Raw entries for detailed analysis
    entries: List[TaskResultEntry] = field(default_factory=list)

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

    def merge(self, other: "DailyAutonomousReport") -> None:
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

    def to_dict(self) -> Dict[str, Any]:
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

    def to_slack_message(self) -> str:
        """Format report as Slack message."""
        total_operations = (
            self.archived_count
            + self.expired_count
            + self.purged_count
            + self.recovered_count
        )
        
        status_emoji = "✅" if self.task_failures == 0 else "⚠️"
        
        lines = [
            f"{status_emoji} *자율 운영 일일 리포트* ({self.date:%Y-%m-%d})",
            "",
            "*📊 자동 처리 현황*",
            f"• 아카이브: {self.archived_count}건",
            f"• 만료 처리: {self.expired_count}건",
            f"• 영구 삭제: {self.purged_count}건",
            f"• 복구 완료: {self.recovered_count}건",
            "",
            "*🔔 알림 현황*",
            f"• 드리프트 경고: {self.drift_warnings_count}건",
            f"• 승인 만료: {self.approval_expired_count}건",
        ]
        
        # Circuit breaker stats if any
        if self.circuit_transitions > 0:
            lines.extend([
                "",
                "*⚡ Circuit Breaker*",
                f"• 상태 전환: {self.circuit_transitions}회",
                f"• Open: {self.circuits_opened}회 / Close: {self.circuits_closed}회",
            ])
        
        # Error summary if any
        if self.task_failures > 0 or self.critical_alerts > 0:
            lines.extend([
                "",
                "*❌ 오류 현황*",
                f"• 태스크 실패: {self.task_failures}건",
                f"• 긴급 알림: {self.critical_alerts}건",
            ])
        
        # Custom metrics
        for key, value in self.custom_counts.items():
            if value > 0:
                lines.append(f"• {key}: {value}건")
        
        # Summary
        lines.extend([
            "",
            f"📈 *총 처리: {total_operations}건* | 태스크 실행: {len(self.entries)}회",
        ])
        
        return "\n".join(lines)

    def to_email_html(self) -> str:
        """Format report as HTML email."""
        return f"""
        <html>
        <body>
            <h2>🤖 Self-Healing 일일 리포트 ({self.date:%Y-%m-%d})</h2>
            
            <h3>📊 자동 처리 현황</h3>
            <ul>
                <li>아카이브: {self.archived_count}건</li>
                <li>만료 처리: {self.expired_count}건</li>
                <li>영구 삭제: {self.purged_count}건</li>
                <li>복구 완료: {self.recovered_count}건</li>
            </ul>
            
            <h3>🔔 알림 현황</h3>
            <ul>
                <li>드리프트 경고: {self.drift_warnings_count}건</li>
                <li>승인 만료: {self.approval_expired_count}건</li>
            </ul>
            
            <h3>❌ 오류 현황</h3>
            <ul>
                <li>태스크 실패: {self.task_failures}건</li>
                <li>긴급 알림: {self.critical_alerts}건</li>
            </ul>
            
            <p><strong>총 태스크 실행: {len(self.entries)}회</strong></p>
        </body>
        </html>
        """


# =============================================================================
# Backward Compatibility Alias
# =============================================================================

# DailyReportData was the original implementation name.
# DailyAutonomousReport is the canonical name per 09_AUTONOMOUS_TASK_EXPANSION.md §6.1
DailyReportData = DailyAutonomousReport


# =============================================================================
# Daily Report Collector
# =============================================================================


class DailyReportCollector:
    """
    Collects and stores task results for daily aggregation.
    
    Uses Django cache or Redis for storage, with in-memory fallback.
    """

    def __init__(self):
        self._memory_storage: Dict[str, List[TaskResultEntry]] = {}

    def add_result(
        self,
        task_name: str,
        result: Dict[str, Any],
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
            current.append({
                "task_name": entry.task_name,
                "result": entry.result,
                "timestamp": entry.timestamp.isoformat(),
                "severity": entry.severity,
            })
            cache.set(cache_key, current, timeout=86400 * 2)  # 2 days TTL
            
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

    def get_report(self, date: Optional[datetime] = None) -> "DailyAutonomousReport":
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
_collector: Optional[DailyReportCollector] = None


def get_daily_report_collector() -> DailyReportCollector:
    """Get the singleton DailyReportCollector instance."""
    global _collector
    if _collector is None:
        _collector = DailyReportCollector()
    return _collector


# =============================================================================
# GenerateDailyAutonomousReportTask (Phase 5 - 문서 §6.2)
# =============================================================================


class GenerateDailyAutonomousReportTask:
    """
    일일 자율 운영 리포트 생성 태스크.
    
    하루 동안의 자율 운영 결과를 요약하여 리포트를 생성합니다.
    BaseNotifyingTask를 상속하여 알림 정책을 적용합니다.
    
    스케줄: 매일 09:00
    큐: reports
    알림: 매일 발송 (Slack)
    
    Reference: docs/self_healing/middleware_system/09_AUTONOMOUS_TASK_EXPANSION.md §6.2 Phase 5
    
    Returns:
        dict: {
            "success": bool,
            "date": str,
            "total_tasks": int,
            "summary": dict,
        }
    """

    name = "selfhealing.generate_daily_autonomous_report"

    def __init__(self):
        """Initialize with lazy import to avoid circular dependency."""
        # Lazy import NotificationPolicy and NotificationTiming
        pass

    @property
    def notification_policy(self):
        """Return notification policy with lazy import."""
        from selfhealing.tasks.notification_policy import (
            NotificationPolicy,
            NotificationTiming,
        )
        return NotificationPolicy(
            timing=NotificationTiming.AFTER,
            aggregate=False,
            default_severity="info",
            channels=["slack"],
        )

    def run(self) -> Dict[str, Any]:
        """일일 리포트 생성 태스크 실행."""
        logger.info("[GenerateDailyAutonomousReportTask] Generating daily report")
        
        try:
            collector = get_daily_report_collector()
            report_data = collector.get_report()
            
            # 요약 생성
            summary = {
                "archived_count": report_data.archived_count,
                "expired_count": report_data.expired_count,
                "purged_count": report_data.purged_count,
                "recovered_count": report_data.recovered_count,
                "circuit_transitions": report_data.circuit_transitions,
                "task_failures": report_data.task_failures,
                "critical_alerts": report_data.critical_alerts,
            }
            
            logger.info(
                f"[GenerateDailyAutonomousReportTask] Report generated - "
                f"{len(report_data.entries)} entries"
            )
            
            return {
                "success": True,
                "date": report_data.date.strftime("%Y-%m-%d"),
                "total_tasks": len(report_data.entries),
                "summary": summary,
            }
            
        except Exception as e:
            logger.error(
                f"[GenerateDailyAutonomousReportTask] Failed: {e}", exc_info=True
            )
            return {
                "success": False,
                "error": str(e),
            }

    def _get_summary_message(self, result: Dict[str, Any]) -> str:
        """알림 메시지 생성."""
        if result.get("error"):
            return f"❌ 일일 리포트 생성 실패: {result['error']}"
        
        summary = result.get("summary", {})
        
        return (
            f"📊 *자율 운영 일일 리포트* ({result.get('date', 'N/A')})\n"
            f"• 아카이브: {summary.get('archived_count', 0)}건\n"
            f"• 만료 처리: {summary.get('expired_count', 0)}건\n"
            f"• 영구 삭제: {summary.get('purged_count', 0)}건\n"
            f"• 복구 완료: {summary.get('recovered_count', 0)}건\n"
            f"• CB 전환: {summary.get('circuit_transitions', 0)}건\n"
            f"• 태스크 실패: {summary.get('task_failures', 0)}건\n"
            f"• 위험 알림: {summary.get('critical_alerts', 0)}건"
        )


# =============================================================================
# Celery Task Function
# =============================================================================


def generate_daily_autonomous_report(
    date: Optional[datetime] = None,
    channels: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Generate and send daily autonomous operations report.
    
    This should be scheduled via Celery Beat at 09:00 daily.
    
    Args:
        date: Report date (default: yesterday)
        channels: Notification channels (default: ["slack"])
    
    Returns:
        dict: Report generation result
    """
    from selfhealing.core.timezone import now
    
    channels = channels or ["slack"]
    
    if date is None:
        date = now() - timedelta(days=1)
    
    logger.info(f"[DailyReport] Generating report for {date:%Y-%m-%d}")
    
    # Collect report data
    collector = get_daily_report_collector()
    report = collector.get_report(date)
    
    # Skip if no data
    if len(report.entries) == 0:
        logger.info("[DailyReport] No entries for report, skipping")
        return {
            "success": True,
            "skipped": True,
            "reason": "no_entries",
            "date": date.isoformat(),
        }
    
    # Send notification
    try:
        from selfhealing.services.security_notification_service import (
            get_security_notification_service,
        )
        
        service = get_security_notification_service()
        
        # Determine severity based on failures
        severity = "info"
        if report.critical_alerts > 0:
            severity = "critical"
        elif report.task_failures > 0:
            severity = "warning"
        
        result = service.send_alert(
            title=f"[Self-Healing] 일일 리포트 ({date:%Y-%m-%d})",
            message=report.to_slack_message(),
            severity=severity,
            channels=channels,
            metadata=report.to_dict(),
        )
        
        logger.info(
            f"[DailyReport] Report sent: {len(report.entries)} entries, "
            f"severity={severity}"
        )
        
        return {
            "success": True,
            "date": date.isoformat(),
            "entry_count": len(report.entries),
            "severity": severity,
            "report": report.to_dict(),
        }
        
    except Exception as e:
        logger.error(f"[DailyReport] Failed to send report: {e}")
        return {
            "success": False,
            "error": str(e),
            "date": date.isoformat(),
        }


def get_daily_report_beat_schedule() -> Dict[str, Dict[str, Any]]:
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
# Celery Task Registration
# =============================================================================

try:
    from celery import shared_task

    @shared_task(
        name="selfhealing.tasks.daily_report.generate_daily_autonomous_report",
        bind=False,
        max_retries=2,
        default_retry_delay=300,
    )
    def generate_daily_autonomous_report_task(
        date_str: Optional[str] = None,
        channels: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
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
