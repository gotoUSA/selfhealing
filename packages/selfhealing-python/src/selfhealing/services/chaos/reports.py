"""
Resilience Report Generator

Generates daily resilience reports based on chaos experiment results.

Features:
- Daily resilience summary
- Trend analysis
- SLA compliance tracking
- Metrics recording
- Audit trail integration
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from selfhealing.core.timezone import now

logger = logging.getLogger(__name__)


# =============================================================================
# Enums
# =============================================================================


class ResilienceGrade(str, Enum):
    """Overall resilience grade."""
    
    A = "A"
    """Excellent: All experiments passed, no SLA breaches."""
    
    B = "B"
    """Good: Minor issues, all recovered within SLA."""
    
    C = "C"
    """Acceptable: Some issues, recovery slightly delayed."""
    
    D = "D"
    """Needs Improvement: Multiple failures or slow recovery."""
    
    F = "F"
    """Critical: Major failures, SLA breaches."""


class ExperimentOutcome(str, Enum):
    """Outcome of an experiment."""
    
    PASSED = "passed"
    """System handled chaos gracefully."""
    
    DEGRADED = "degraded"
    """System degraded but recovered."""
    
    FAILED = "failed"
    """System failed to handle chaos."""
    
    SKIPPED = "skipped"
    """Experiment was skipped."""


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class ExperimentSummary:
    """Summary of a single experiment."""
    
    experiment_id: str
    experiment_type: str
    target_service: str
    
    # Outcome
    outcome: str
    status: str
    
    # Timing
    started_at: str = ""
    duration_seconds: float = 0.0
    recovery_time_seconds: float = 0.0
    
    # Impact
    errors_injected: int = 0
    sla_breaches: int = 0
    
    # Steady state
    steady_state_passed: bool = True
    
    # Forensic analysis
    forensic_recommendations: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "experiment_id": self.experiment_id,
            "experiment_type": self.experiment_type,
            "target_service": self.target_service,
            "outcome": self.outcome,
            "status": self.status,
            "started_at": self.started_at,
            "duration_seconds": self.duration_seconds,
            "recovery_time_seconds": self.recovery_time_seconds,
            "errors_injected": self.errors_injected,
            "sla_breaches": self.sla_breaches,
            "steady_state_passed": self.steady_state_passed,
            "forensic_recommendations": self.forensic_recommendations,
        }


@dataclass
class DailyResilienceReport:
    """Daily resilience report."""
    
    # Report metadata
    report_id: str = ""
    report_date: str = ""
    generated_at: str = field(default_factory=lambda: now().isoformat())
    
    # Overall grade
    grade: str = ResilienceGrade.A.value
    grade_explanation: str = ""
    
    # Summary statistics
    total_experiments: int = 0
    passed_experiments: int = 0
    failed_experiments: int = 0
    skipped_experiments: int = 0
    
    # SLA metrics
    total_sla_breaches: int = 0
    average_recovery_time_seconds: float = 0.0
    max_recovery_time_seconds: float = 0.0
    
    # Error budget impact
    error_budget_consumed_percent: float = 0.0
    error_budget_remaining_percent: float = 100.0
    
    # Experiment details
    experiments: List[ExperimentSummary] = field(default_factory=list)
    
    # Trends
    grade_trend: str = ""  # "improving", "stable", "declining"
    week_over_week_change: float = 0.0
    
    # Recommendations
    recommendations: List[str] = field(default_factory=list)
    action_items: List[Dict[str, str]] = field(default_factory=list)
    
    # Forensic insights
    forensic_summary: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "report_id": self.report_id,
            "report_date": self.report_date,
            "generated_at": self.generated_at,
            "grade": self.grade,
            "grade_explanation": self.grade_explanation,
            "total_experiments": self.total_experiments,
            "passed_experiments": self.passed_experiments,
            "failed_experiments": self.failed_experiments,
            "skipped_experiments": self.skipped_experiments,
            "total_sla_breaches": self.total_sla_breaches,
            "average_recovery_time_seconds": self.average_recovery_time_seconds,
            "max_recovery_time_seconds": self.max_recovery_time_seconds,
            "error_budget_consumed_percent": self.error_budget_consumed_percent,
            "error_budget_remaining_percent": self.error_budget_remaining_percent,
            "experiments": [e.to_dict() for e in self.experiments],
            "grade_trend": self.grade_trend,
            "week_over_week_change": self.week_over_week_change,
            "recommendations": self.recommendations,
            "action_items": self.action_items,
            "forensic_summary": self.forensic_summary,
        }


@dataclass
class ReportConfig:
    """Configuration for report generation."""
    
    # Grading thresholds
    grade_a_min_pass_rate: float = 100.0
    grade_b_min_pass_rate: float = 90.0
    grade_c_min_pass_rate: float = 75.0
    grade_d_min_pass_rate: float = 50.0
    
    # Recovery time thresholds (seconds)
    acceptable_recovery_time_seconds: float = 60.0
    warning_recovery_time_seconds: float = 180.0
    
    # Report retention
    keep_reports_days: int = 90
    
    # Notification
    notify_on_grade_drop: bool = True
    notify_on_critical_grade: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "grade_a_min_pass_rate": self.grade_a_min_pass_rate,
            "grade_b_min_pass_rate": self.grade_b_min_pass_rate,
            "grade_c_min_pass_rate": self.grade_c_min_pass_rate,
            "grade_d_min_pass_rate": self.grade_d_min_pass_rate,
            "acceptable_recovery_time_seconds": self.acceptable_recovery_time_seconds,
            "warning_recovery_time_seconds": self.warning_recovery_time_seconds,
            "keep_reports_days": self.keep_reports_days,
            "notify_on_grade_drop": self.notify_on_grade_drop,
            "notify_on_critical_grade": self.notify_on_critical_grade,
        }


# =============================================================================
# Resilience Report Generator
# =============================================================================


class ResilienceReportGenerator:
    """
    Generates resilience reports based on chaos experiment results.
    
    Responsibilities:
    1. Collect experiment results from scheduler
    2. Calculate resilience grade
    3. Generate actionable recommendations
    4. Record to metrics and audit trail
    5. Notify stakeholders
    
    Usage:
        generator = get_report_generator()
        
        # Generate daily report
        report = generator.generate_daily_report()
        
        # Get historical reports
        reports = generator.get_reports(days=7)
    """
    
    def __init__(self, config: Optional[ReportConfig] = None):
        """Initialize ResilienceReportGenerator."""
        self._config = config or ReportConfig()
        self._lock = threading.RLock()
        
        # Report storage
        self._reports: Dict[str, DailyResilienceReport] = {}
        
        # Load from storage
        self._load_reports()
    
    # =========================================================================
    # Configuration
    # =========================================================================
    
    def get_config(self) -> ReportConfig:
        """Get current configuration."""
        return self._config
    
    def update_config(self, **kwargs) -> ReportConfig:
        """Update configuration."""
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._config, key):
                    setattr(self._config, key, value)
            self._persist_config()
            return self._config
    
    def _persist_config(self) -> None:
        """Persist configuration."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager
            manager = get_runtime_config_manager()
            manager.update_chaos_config(report_config=self._config.to_dict())
        except Exception as e:
            logger.warning(f"[ReportGenerator] Could not persist config: {e}")
    
    # =========================================================================
    # Report Generation
    # =========================================================================
    
    def generate_daily_report(
        self,
        report_date: Optional[datetime] = None,
    ) -> DailyResilienceReport:
        """
        Generate a daily resilience report.
        
        Args:
            report_date: Date to generate report for (default: yesterday)
            
        Returns:
            DailyResilienceReport
        """
        if report_date is None:
            report_date = now() - timedelta(days=1)
        
        report_date_str = report_date.strftime("%Y-%m-%d")
        report_id = f"resilience-{report_date_str}"
        
        logger.info(f"[ReportGenerator] Generating daily report for {report_date_str}")
        
        try:
            # 1. Collect experiment results
            experiments = self._collect_experiment_results(report_date)
            
            # 2. Run forensic analysis
            forensic_summary = self._run_forensic_analysis(experiments)
            
            # 3. Calculate statistics
            stats = self._calculate_statistics(experiments)
            
            # 4. Calculate grade
            grade, grade_explanation = self._calculate_grade(stats, experiments)
            
            # 5. Get error budget status
            budget_status = self._get_error_budget_status()
            
            # 6. Generate recommendations
            recommendations = self._generate_recommendations(
                experiments, stats, forensic_summary, grade
            )
            
            # 7. Calculate trends
            trend, wow_change = self._calculate_trends(report_date_str, grade)
            
            # 8. Create report
            report = DailyResilienceReport(
                report_id=report_id,
                report_date=report_date_str,
                grade=grade,
                grade_explanation=grade_explanation,
                total_experiments=stats["total"],
                passed_experiments=stats["passed"],
                failed_experiments=stats["failed"],
                skipped_experiments=stats["skipped"],
                total_sla_breaches=stats["sla_breaches"],
                average_recovery_time_seconds=stats["avg_recovery_time"],
                max_recovery_time_seconds=stats["max_recovery_time"],
                error_budget_consumed_percent=budget_status.get("consumed_percent", 0),
                error_budget_remaining_percent=budget_status.get("remaining_percent", 100),
                experiments=experiments,
                grade_trend=trend,
                week_over_week_change=wow_change,
                recommendations=recommendations,
                action_items=self._generate_action_items(experiments, forensic_summary),
                forensic_summary=forensic_summary,
            )
            
            # 9. Store report
            with self._lock:
                self._reports[report_id] = report
            self._persist_reports()
            
            # 10. Record metrics
            self._record_metrics(report)
            
            # 11. Record audit
            self._record_audit(report)
            
            # 12. Send notifications if needed
            self._send_notifications(report, trend)
            
            logger.info(
                f"[ReportGenerator] Generated report {report_id}: "
                f"Grade={grade}, Passed={stats['passed']}/{stats['total']}"
            )
            
            return report
            
        except Exception as e:
            logger.exception(f"[ReportGenerator] Error generating report: {e}")
            
            # Return minimal report on error
            return DailyResilienceReport(
                report_id=report_id,
                report_date=report_date_str,
                grade=ResilienceGrade.F.value,
                grade_explanation=f"Report generation failed: {e}",
            )
    
    # =========================================================================
    # Data Collection
    # =========================================================================
    
    def _collect_experiment_results(self, report_date: datetime) -> List[ExperimentSummary]:
        """Collect experiment results for the given date."""
        from .scheduler import get_chaos_scheduler
        
        experiments = []
        
        try:
            scheduler = get_chaos_scheduler()
            history = scheduler.get_execution_history(limit=500)
            
            # Filter by date
            date_str = report_date.strftime("%Y-%m-%d")
            
            for result in history:
                if result.started_at.startswith(date_str):
                    # Determine outcome
                    if result.skipped:
                        outcome = ExperimentOutcome.SKIPPED.value
                    elif result.success:
                        outcome = ExperimentOutcome.PASSED.value
                    elif result.experiment_result.get("status") == "completed":
                        outcome = ExperimentOutcome.DEGRADED.value
                    else:
                        outcome = ExperimentOutcome.FAILED.value
                    
                    # Get schedule for additional info
                    schedule = scheduler.get_schedule(result.schedule_id)
                    
                    summary = ExperimentSummary(
                        experiment_id=result.experiment_id,
                        experiment_type=schedule.experiment_type if schedule else "",
                        target_service=schedule.target_service if schedule else "",
                        outcome=outcome,
                        status=result.status,
                        started_at=result.started_at,
                        duration_seconds=result.duration_seconds,
                        recovery_time_seconds=result.experiment_result.get(
                            "recovery_time_seconds", 0
                        ),
                        errors_injected=result.experiment_result.get("errors_injected", 0),
                        sla_breaches=result.experiment_result.get("sla_breaches", 0),
                        steady_state_passed=result.experiment_result.get(
                            "steady_state_hypothesis_passed", True
                        ),
                    )
                    experiments.append(summary)
                    
        except Exception as e:
            logger.warning(f"[ReportGenerator] Could not collect experiments: {e}")
        
        return experiments
    
    def _run_forensic_analysis(self, experiments: List[ExperimentSummary]) -> Dict[str, Any]:
        """Run analysis on experiment results (stub - ForensicAdvisor removed)."""
        # ForensicAdvisor has been removed from the system.
        # This method returns empty analysis results.
        return {
            "patterns_detected": [],
            "recommendations_by_service": {},
            "total_issues_analyzed": len([e for e in experiments if e.outcome != ExperimentOutcome.PASSED.value]),
        }
    
    def _get_error_budget_status(self) -> Dict[str, Any]:
        """Get current error budget status."""
        try:
            from selfhealing.services.error_budget_service import get_error_budget_service
            
            service = get_error_budget_service()
            status = service.get_status()
            
            return {
                "remaining_percent": status.get("remaining_percent", 100),
                "consumed_percent": 100 - status.get("remaining_percent", 100),
            }
        except Exception as e:
            logger.warning(f"[ReportGenerator] Could not get error budget: {e}")
            return {"remaining_percent": 100, "consumed_percent": 0}
    
    # =========================================================================
    # Calculations
    # =========================================================================
    
    def _calculate_statistics(self, experiments: List[ExperimentSummary]) -> Dict[str, Any]:
        """Calculate summary statistics."""
        total = len(experiments)
        passed = len([e for e in experiments if e.outcome == ExperimentOutcome.PASSED.value])
        failed = len([e for e in experiments if e.outcome == ExperimentOutcome.FAILED.value])
        skipped = len([e for e in experiments if e.outcome == ExperimentOutcome.SKIPPED.value])
        
        recovery_times = [
            e.recovery_time_seconds for e in experiments
            if e.recovery_time_seconds > 0
        ]
        
        sla_breaches = sum(e.sla_breaches for e in experiments)
        
        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "degraded": total - passed - failed - skipped,
            "pass_rate": (passed / total * 100) if total > 0 else 100.0,
            "avg_recovery_time": sum(recovery_times) / len(recovery_times) if recovery_times else 0,
            "max_recovery_time": max(recovery_times) if recovery_times else 0,
            "sla_breaches": sla_breaches,
        }
    
    def _calculate_grade(
        self,
        stats: Dict[str, Any],
        experiments: List[ExperimentSummary],
    ) -> tuple[str, str]:
        """Calculate resilience grade."""
        pass_rate = stats["pass_rate"]
        sla_breaches = stats["sla_breaches"]
        avg_recovery = stats["avg_recovery_time"]
        
        # No experiments is grade A
        if stats["total"] == 0:
            return ResilienceGrade.A.value, "No experiments executed"
        
        # Critical failures automatically lower grade
        if sla_breaches > 5 or stats["failed"] > stats["total"] * 0.5:
            return ResilienceGrade.F.value, f"Critical: {sla_breaches} SLA breaches, {stats['failed']} failures"
        
        # Calculate base grade from pass rate
        if pass_rate >= self._config.grade_a_min_pass_rate:
            grade = ResilienceGrade.A
            explanation = "Excellent: All experiments passed"
        elif pass_rate >= self._config.grade_b_min_pass_rate:
            grade = ResilienceGrade.B
            explanation = f"Good: {pass_rate:.1f}% pass rate"
        elif pass_rate >= self._config.grade_c_min_pass_rate:
            grade = ResilienceGrade.C
            explanation = f"Acceptable: {pass_rate:.1f}% pass rate"
        elif pass_rate >= self._config.grade_d_min_pass_rate:
            grade = ResilienceGrade.D
            explanation = f"Needs improvement: {pass_rate:.1f}% pass rate"
        else:
            grade = ResilienceGrade.F
            explanation = f"Critical: {pass_rate:.1f}% pass rate"
        
        # Adjust for recovery time
        if avg_recovery > self._config.warning_recovery_time_seconds:
            if grade.value in ("A", "B"):
                grade = ResilienceGrade.C
                explanation += f"; Slow recovery: {avg_recovery:.1f}s avg"
        
        # Adjust for SLA breaches
        if sla_breaches > 0:
            if grade == ResilienceGrade.A:
                grade = ResilienceGrade.B
            explanation += f"; {sla_breaches} SLA breaches"
        
        return grade.value, explanation
    
    def _calculate_trends(self, current_date: str, current_grade: str) -> tuple[str, float]:
        """Calculate grade trends."""
        grade_values = {"A": 5, "B": 4, "C": 3, "D": 2, "F": 1}
        
        # Get last 7 days of reports
        with self._lock:
            recent_reports = sorted(
                [r for r in self._reports.values() if r.report_date < current_date],
                key=lambda r: r.report_date,
                reverse=True,
            )[:7]
        
        if not recent_reports:
            return "stable", 0.0
        
        # Calculate average grade
        recent_grades = [grade_values.get(r.grade, 3) for r in recent_reports]
        avg_recent = sum(recent_grades) / len(recent_grades)
        current_value = grade_values.get(current_grade, 3)
        
        # Determine trend
        diff = current_value - avg_recent
        
        if diff > 0.5:
            trend = "improving"
        elif diff < -0.5:
            trend = "declining"
        else:
            trend = "stable"
        
        # Week over week change
        if len(recent_reports) >= 7:
            week_ago_grade = grade_values.get(recent_reports[6].grade, 3)
            wow_change = current_value - week_ago_grade
        else:
            wow_change = 0.0
        
        return trend, wow_change
    
    # =========================================================================
    # Recommendations
    # =========================================================================
    
    def _generate_recommendations(
        self,
        experiments: List[ExperimentSummary],
        stats: Dict[str, Any],
        forensic_summary: Dict[str, Any],
        grade: str,
    ) -> List[str]:
        """Generate actionable recommendations."""
        recommendations = []
        
        # Grade-based recommendations
        if grade in ("D", "F"):
            recommendations.append(
                "⚠️ Critical: System resilience is below acceptable levels. "
                "Prioritize stability improvements before enabling more chaos experiments."
            )
        
        # Recovery time recommendations
        if stats["avg_recovery_time"] > self._config.warning_recovery_time_seconds:
            recommendations.append(
                f"Recovery time averaging {stats['avg_recovery_time']:.1f}s exceeds "
                f"threshold of {self._config.warning_recovery_time_seconds}s. "
                "Consider implementing faster failover mechanisms."
            )
        
        # SLA breach recommendations
        if stats["sla_breaches"] > 0:
            recommendations.append(
                f"{stats['sla_breaches']} SLA breaches detected. "
                "Review error budget consumption and consider increasing circuit breaker thresholds."
            )
        
        # Failed experiment recommendations
        failed_services = set(
            e.target_service for e in experiments
            if e.outcome == ExperimentOutcome.FAILED.value
        )
        for service in failed_services:
            recommendations.append(
                f"Service '{service}' failed chaos experiments. "
                "Investigate service resilience and retry mechanisms."
            )
        
        # Forensic recommendations
        forensic_recs = forensic_summary.get("recommendations_by_service", {})
        for service, recs in forensic_recs.items():
            for rec in recs[:2]:  # Top 2 per service
                recommendations.append(f"[{service}] {rec}")
        
        return recommendations[:10]  # Limit to 10 recommendations
    
    def _generate_action_items(
        self,
        experiments: List[ExperimentSummary],
        forensic_summary: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        """Generate specific action items."""
        action_items = []
        
        # Failed experiments become action items
        for exp in experiments:
            if exp.outcome == ExperimentOutcome.FAILED.value:
                action_items.append({
                    "title": f"Investigate failure: {exp.target_service}",
                    "description": (
                        f"Experiment {exp.experiment_id} ({exp.experiment_type}) failed. "
                        f"Recovery time: {exp.recovery_time_seconds:.1f}s"
                    ),
                    "priority": "high",
                    "service": exp.target_service,
                })
        
        # Slow recovery becomes action items
        for exp in experiments:
            if exp.recovery_time_seconds > self._config.warning_recovery_time_seconds:
                action_items.append({
                    "title": f"Improve recovery time: {exp.target_service}",
                    "description": (
                        f"Recovery took {exp.recovery_time_seconds:.1f}s, "
                        f"exceeding threshold of {self._config.warning_recovery_time_seconds}s"
                    ),
                    "priority": "medium",
                    "service": exp.target_service,
                })
        
        return action_items[:20]  # Limit to 20 items
    
    # =========================================================================
    # Recording & Notifications
    # =========================================================================
    
    def _record_metrics(self, report: DailyResilienceReport) -> None:
        """Record report metrics to Prometheus.
        
        Note: Resilience grade and chaos experiment metrics are not yet implemented.
        When needed, add record_resilience_grade and record_chaos_experiment_outcome
        to selfhealing.services.metrics.recorders module.
        """
        # TODO: Implement when metrics are defined
        # grade_value = {"A": 5, "B": 4, "C": 3, "D": 2, "F": 1}.get(report.grade, 0)
        logger.debug(
            f"[ReportGenerator] Metrics recording skipped - grade={report.grade}, "
            f"experiments={report.total_experiments}"
        )
    
    def _record_audit(self, report: DailyResilienceReport) -> None:
        """Record report to audit trail."""
        logger.info(
            f"[ResilienceReportAudit] report_id={report.report_id} "
            f"date={report.report_date} grade={report.grade} "
            f"experiments={report.total_experiments} "
            f"passed={report.passed_experiments}"
        )
    
    def _send_notifications(self, report: DailyResilienceReport, trend: str) -> None:
        """Send notifications based on report."""
        try:
            from selfhealing.adapters.alert import get_alert_adapter
            
            adapter = get_alert_adapter()
            if not adapter:
                return
            
            # Notify on critical grade
            if self._config.notify_on_critical_grade and report.grade == ResilienceGrade.F.value:
                adapter.alert(
                    severity="critical",
                    title="Daily Resilience Report: CRITICAL",
                    message=(
                        f"Resilience grade: {report.grade}\n"
                        f"Explanation: {report.grade_explanation}\n\n"
                        f"Experiments: {report.total_experiments}\n"
                        f"Passed: {report.passed_experiments}\n"
                        f"Failed: {report.failed_experiments}\n"
                        f"SLA Breaches: {report.total_sla_breaches}"
                    ),
                    tags=["chaos", "resilience", "daily-report"],
                )
            
            # Notify on grade drop
            elif self._config.notify_on_grade_drop and trend == "declining":
                adapter.alert(
                    severity="warning",
                    title=f"Daily Resilience Report: Grade {report.grade} (Declining)",
                    message=(
                        f"Resilience grade is declining.\n"
                        f"Current: {report.grade}\n"
                        f"Trend: {trend}\n\n"
                        f"Recommendations:\n" +
                        "\n".join(f"- {r}" for r in report.recommendations[:3])
                    ),
                    tags=["chaos", "resilience", "daily-report"],
                )
                
        except Exception as e:
            logger.warning(f"[ReportGenerator] Could not send notification: {e}")
    
    # =========================================================================
    # Report Retrieval
    # =========================================================================
    
    def get_report(self, report_id: str) -> Optional[DailyResilienceReport]:
        """Get a specific report by ID."""
        return self._reports.get(report_id)
    
    def get_report_by_date(self, date: str) -> Optional[DailyResilienceReport]:
        """Get report for a specific date (YYYY-MM-DD)."""
        report_id = f"resilience-{date}"
        return self._reports.get(report_id)
    
    def get_reports(
        self,
        days: int = 30,
        grade_filter: Optional[str] = None,
    ) -> List[DailyResilienceReport]:
        """
        Get historical reports.
        
        Args:
            days: Number of days to retrieve
            grade_filter: Optional grade to filter by
            
        Returns:
            List of reports
        """
        with self._lock:
            reports = sorted(
                self._reports.values(),
                key=lambda r: r.report_date,
                reverse=True,
            )[:days]
            
            if grade_filter:
                reports = [r for r in reports if r.grade == grade_filter]
            
            return reports
    
    def get_grade_history(self, days: int = 30) -> List[Dict[str, str]]:
        """Get grade history for trending."""
        reports = self.get_reports(days=days)
        return [
            {"date": r.report_date, "grade": r.grade}
            for r in reports
        ]
    
    # =========================================================================
    # Storage
    # =========================================================================
    
    def _persist_reports(self) -> None:
        """Persist reports to storage."""
        try:
            from selfhealing.core.state_backend import get_state_backend
            backend = get_state_backend()
            
            # Only keep recent reports
            cutoff = (now() - timedelta(days=self._config.keep_reports_days)).strftime("%Y-%m-%d")
            
            data = {
                rid: r.to_dict()
                for rid, r in self._reports.items()
                if r.report_date >= cutoff
            }
            backend.set("chaos:resilience_reports", data)
        except Exception as e:
            logger.warning(f"[ReportGenerator] Could not persist reports: {e}")
    
    def _load_reports(self) -> None:
        """Load reports from storage."""
        try:
            from selfhealing.core.state_backend import get_state_backend
            backend = get_state_backend()
            
            data = backend.get("chaos:resilience_reports")
            if data:
                for rid, rdata in data.items():
                    self._reports[rid] = DailyResilienceReport(
                        report_id=rdata.get("report_id", ""),
                        report_date=rdata.get("report_date", ""),
                        generated_at=rdata.get("generated_at", ""),
                        grade=rdata.get("grade", ""),
                        grade_explanation=rdata.get("grade_explanation", ""),
                        total_experiments=rdata.get("total_experiments", 0),
                        passed_experiments=rdata.get("passed_experiments", 0),
                        failed_experiments=rdata.get("failed_experiments", 0),
                        skipped_experiments=rdata.get("skipped_experiments", 0),
                        total_sla_breaches=rdata.get("total_sla_breaches", 0),
                        average_recovery_time_seconds=rdata.get("average_recovery_time_seconds", 0),
                        max_recovery_time_seconds=rdata.get("max_recovery_time_seconds", 0),
                        error_budget_consumed_percent=rdata.get("error_budget_consumed_percent", 0),
                        error_budget_remaining_percent=rdata.get("error_budget_remaining_percent", 100),
                        grade_trend=rdata.get("grade_trend", ""),
                        week_over_week_change=rdata.get("week_over_week_change", 0),
                        recommendations=rdata.get("recommendations", []),
                        action_items=rdata.get("action_items", []),
                        forensic_summary=rdata.get("forensic_summary", {}),
                    )
        except Exception as e:
            logger.warning(f"[ReportGenerator] Could not load reports: {e}")


# =============================================================================
# Singleton
# =============================================================================


_report_generator: Optional[ResilienceReportGenerator] = None
_generator_lock = threading.Lock()


def get_report_generator() -> ResilienceReportGenerator:
    """Get the singleton ResilienceReportGenerator instance."""
    global _report_generator
    
    if _report_generator is None:
        with _generator_lock:
            if _report_generator is None:
                _report_generator = ResilienceReportGenerator()
    
    return _report_generator


def reset_report_generator() -> None:
    """Reset the singleton (for testing)."""
    global _report_generator
    with _generator_lock:
        _report_generator = None
