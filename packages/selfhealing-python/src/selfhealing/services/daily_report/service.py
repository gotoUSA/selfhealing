"""
Daily Report Service

Thin Task, Fat Service 원칙:
- 모든 일일 리포트 비즈니스 로직을 담당

Reference: docs/self_healing/middleware_system/08_NOTIFICATION_ARCHITECTURE.md
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .models import DailyAutonomousReport
from .aggregator import aggregate_daily_results
from .formatters import format_report_for_slack, format_report_for_email

logger = logging.getLogger(__name__)


@dataclass
class ReportResult:
    """Report generation result."""

    success: bool
    report: Optional[DailyAutonomousReport] = None
    channels_sent: List[str] = field(default_factory=list)
    skipped: bool = False
    skip_reason: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = {
            "success": self.success,
            "date": self.report.date.isoformat() if self.report else None,
            "channels_sent": self.channels_sent,
        }

        if self.skipped:
            result["skipped"] = True
            result["reason"] = self.skip_reason

        if self.error:
            result["error"] = self.error

        if self.report:
            result["entry_count"] = len(self.report.entries)
            result["summary"] = {
                "archived_count": self.report.archived_count,
                "expired_count": self.report.expired_count,
                "purged_count": self.report.purged_count,
                "recovered_count": self.report.recovered_count,
                "circuit_transitions": self.report.circuit_transitions,
                "task_failures": self.report.task_failures,
                "critical_alerts": self.report.critical_alerts,
            }

        return result


class DailyReportService:
    """
    일일 리포트 생성 및 전송 서비스.

    All daily report generation logic is handled here.
    Tasks delegate to this service following the Thin Task, Fat Service principle.
    """

    def generate_and_send_report(
        self,
        date: Optional[datetime] = None,
        channels: Optional[List[str]] = None,
    ) -> ReportResult:
        """
        일일 리포트 생성 및 전송.

        Args:
            date: 리포트 대상 날짜 (기본: 어제)
            channels: 전송 채널 목록 (기본: ["slack"])

        Returns:
            ReportResult
        """
        from selfhealing.core.timezone import now as get_now
        from datetime import timedelta

        channels = channels or ["slack"]

        if date is None:
            date = get_now() - timedelta(days=1)

        logger.info(f"[DailyReportService] Generating report for {date:%Y-%m-%d}")

        try:
            # 1. Aggregate results
            report = aggregate_daily_results(date)

            # 2. Skip if no data
            if len(report.entries) == 0:
                logger.info("[DailyReportService] No entries for report, skipping")
                return ReportResult(
                    success=True,
                    report=report,
                    skipped=True,
                    skip_reason="no_entries",
                )

            # 3. Send to channels
            sent_channels = []
            for channel in channels:
                try:
                    self._send_to_channel(report, channel)
                    sent_channels.append(channel)
                except Exception as e:
                    logger.error(
                        f"[DailyReportService] Failed to send to {channel}: {e}"
                    )

            logger.info(
                f"[DailyReportService] Report sent - {len(report.entries)} entries, "
                f"channels: {sent_channels}"
            )

            return ReportResult(
                success=True,
                report=report,
                channels_sent=sent_channels,
            )

        except Exception as e:
            logger.error(f"[DailyReportService] Generation failed: {e}", exc_info=True)
            return ReportResult(success=False, error=str(e))

    def _send_to_channel(
        self, report: DailyAutonomousReport, channel: str
    ) -> None:
        """채널로 리포트 전송."""
        # Determine severity based on failures
        severity = "info"
        if report.critical_alerts > 0:
            severity = "critical"
        elif report.task_failures > 0:
            severity = "warning"

        if channel == "slack":
            message = format_report_for_slack(report)
            self._send_slack(report, message, severity)
        elif channel == "email":
            message = format_report_for_email(report)
            self._send_email(report, message, severity)
        else:
            logger.warning(f"[DailyReportService] Unknown channel: {channel}")

    def _send_slack(
        self,
        report: DailyAutonomousReport,
        message: str,
        severity: str,
    ) -> None:
        """Send report to Slack."""
        try:
            from selfhealing.services.security_notification_service import (
                get_security_notification_service,
            )

            service = get_security_notification_service()
            service.send_alert(
                title=f"[Self-Healing] 일일 리포트 ({report.date:%Y-%m-%d})",
                message=message,
                severity=severity,
                channels=["slack"],
                metadata=report.to_dict(),
            )
        except Exception as e:
            logger.error(f"[DailyReportService] Slack send failed: {e}")
            raise

    def _send_email(
        self,
        report: DailyAutonomousReport,
        message: str,
        severity: str,
    ) -> None:
        """Send report via email."""
        try:
            from selfhealing.services.security_notification_service import (
                get_security_notification_service,
            )

            service = get_security_notification_service()
            service.send_alert(
                title=f"[Self-Healing] 일일 리포트 ({report.date:%Y-%m-%d})",
                message=message,
                severity=severity,
                channels=["email"],
                metadata=report.to_dict(),
            )
        except Exception as e:
            logger.error(f"[DailyReportService] Email send failed: {e}")
            raise


# =============================================================================
# Singleton
# =============================================================================

_daily_report_service: Optional[DailyReportService] = None


def get_daily_report_service() -> DailyReportService:
    """Get or create the daily report service instance."""
    global _daily_report_service
    if _daily_report_service is None:
        _daily_report_service = DailyReportService()
    return _daily_report_service


def reset_daily_report_service() -> None:
    """Reset the service instance (for testing)."""
    global _daily_report_service
    _daily_report_service = None


__all__ = [
    "ReportResult",
    "DailyReportService",
    "get_daily_report_service",
    "reset_daily_report_service",
]
