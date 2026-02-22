"""
Execution Services - Result Types

Chaos 실험 및 설정 적용 서비스에서 사용하는 결과 데이터 클래스.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# =============================================================================
# Result Types
# =============================================================================


@dataclass
class ExperimentExecutionResult:
    """실험 실행 결과."""

    checked: int = 0
    """체크된 실험 수."""

    executed: int = 0
    """실행된 실험 수."""

    skipped: int = 0
    """스킵된 실험 수."""

    blocked: int = 0
    """차단된 실험 수."""

    errors: list[dict[str, Any]] = field(default_factory=list)
    """에러 목록."""

    experiments: list[dict[str, Any]] = field(default_factory=list)
    """개별 실험 결과."""

    governance_blocked: bool = False
    """거버넌스에 의해 전체 차단되었는지."""

    governance_block_reason: str = ""
    """거버넌스 차단 사유."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "checked": self.checked,
            "executed": self.executed,
            "skipped": self.skipped,
            "blocked": self.blocked,
            "errors": self.errors,
            "experiments": self.experiments,
            "governance_blocked": self.governance_blocked,
            "governance_block_reason": self.governance_block_reason,
        }


@dataclass
class DailyReportResult:
    """일일 보고서 결과."""

    success: bool
    report_id: str | None = None
    grade: str | None = None
    summary: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "report_id": self.report_id,
            "grade": self.grade,
            "summary": self.summary,
            "error": self.error,
        }


@dataclass
class ApprovalCleanupResult:
    """승인 정리 결과."""

    schedule_expired: int = 0
    blast_radius_expired: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schedule_expired": self.schedule_expired,
            "blast_radius_expired": self.blast_radius_expired,
            "errors": self.errors,
        }


@dataclass
class PendingApprovalCheckResult:
    """대기 중인 승인 체크 결과."""

    pending_schedules: int = 0
    pending_blast_radius: int = 0
    alerts_sent: int = 0
    notification_status: str = "sent"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pending_schedules": self.pending_schedules,
            "pending_blast_radius": self.pending_blast_radius,
            "alerts_sent": self.alerts_sent,
            "notification_status": self.notification_status,
            "error": self.error,
        }
