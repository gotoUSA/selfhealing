"""
Report Formatting for Various Channels.

Provides formatting functions for Slack and email output.

Reference: docs/self_healing/middleware_system/08_NOTIFICATION_ARCHITECTURE.md
"""

from __future__ import annotations

from .models import DailyAutonomousReport


def format_report_for_slack(report: DailyAutonomousReport) -> str:
    """
    Format report as Slack message.

    Args:
        report: DailyAutonomousReport instance

    Returns:
        Formatted Slack message string
    """
    total_operations = (
        report.archived_count
        + report.expired_count
        + report.purged_count
        + report.recovered_count
    )

    status_emoji = "✅" if report.task_failures == 0 else "⚠️"

    lines = [
        f"{status_emoji} *자율 운영 일일 리포트* ({report.date:%Y-%m-%d})",
        "",
        "*📊 자동 처리 현황*",
        f"• 아카이브: {report.archived_count}건",
        f"• 만료 처리: {report.expired_count}건",
        f"• 영구 삭제: {report.purged_count}건",
        f"• 복구 완료: {report.recovered_count}건",
        "",
        "*🔔 알림 현황*",
        f"• 드리프트 경고: {report.drift_warnings_count}건",
        f"• 승인 만료: {report.approval_expired_count}건",
    ]

    # Circuit breaker stats if any
    if report.circuit_transitions > 0:
        lines.extend(
            [
                "",
                "*⚡ Circuit Breaker*",
                f"• 상태 전환: {report.circuit_transitions}회",
                f"• Open: {report.circuits_opened}회 / Close: {report.circuits_closed}회",
            ]
        )

    # Error summary if any
    if report.task_failures > 0 or report.critical_alerts > 0:
        lines.extend(
            [
                "",
                "*❌ 오류 현황*",
                f"• 태스크 실패: {report.task_failures}건",
                f"• 긴급 알림: {report.critical_alerts}건",
            ]
        )

    # Custom metrics
    for key, value in report.custom_counts.items():
        if value > 0:
            lines.append(f"• {key}: {value}건")

    # Summary
    lines.extend(
        [
            "",
            f"📈 *총 처리: {total_operations}건* | 태스크 실행: {len(report.entries)}회",
        ]
    )

    return "\n".join(lines)


def format_report_for_email(report: DailyAutonomousReport) -> str:
    """
    Format report as HTML email.

    Args:
        report: DailyAutonomousReport instance

    Returns:
        Formatted HTML email string
    """
    return f"""
    <html>
    <body>
        <h2>🤖 Self-Healing 일일 리포트 ({report.date:%Y-%m-%d})</h2>

        <h3>📊 자동 처리 현황</h3>
        <ul>
            <li>아카이브: {report.archived_count}건</li>
            <li>만료 처리: {report.expired_count}건</li>
            <li>영구 삭제: {report.purged_count}건</li>
            <li>복구 완료: {report.recovered_count}건</li>
        </ul>

        <h3>🔔 알림 현황</h3>
        <ul>
            <li>드리프트 경고: {report.drift_warnings_count}건</li>
            <li>승인 만료: {report.approval_expired_count}건</li>
        </ul>

        <h3>❌ 오류 현황</h3>
        <ul>
            <li>태스크 실패: {report.task_failures}건</li>
            <li>긴급 알림: {report.critical_alerts}건</li>
        </ul>

        <p><strong>총 태스크 실행: {len(report.entries)}회</strong></p>
    </body>
    </html>
    """


__all__ = [
    "format_report_for_slack",
    "format_report_for_email",
]
