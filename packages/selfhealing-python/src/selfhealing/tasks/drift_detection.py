"""
SLA Drift Detection Tasks

Task functions for detecting drift between configured SLA thresholds
and actual recovery performance metrics.

Core Principle: "System provides data, humans make decisions."
These tasks ONLY generate warnings - they NEVER auto-adjust settings.

NOTE: This module provides task functions that can be registered with
any task queue system (Celery, RQ, etc.). The actual task registration
is done in the framework-specific adapter layer.
"""

from __future__ import annotations

import structlog
from collections.abc import Callable
from datetime import timedelta
from typing import Any, Protocol

from selfhealing.core.timezone import now

logger = structlog.get_logger()


# =============================================================================
# Protocols (Framework-agnostic interfaces)
# =============================================================================


class FailedOperationQuerySet(Protocol):
    """Protocol for queryset-like objects."""

    def filter(self, **kwargs) -> FailedOperationQuerySet: ...

    def count(self) -> int: ...

    def order_by(self, *args) -> FailedOperationQuerySet: ...

    def __iter__(self): ...

    def __getitem__(self, key): ...


class DriftDetectionOperationProtocol(Protocol):
    """Protocol for FailedOperation-like objects."""

    id: Any
    domain: str
    status: str
    created_at: Any
    resolved_at: Any | None
    metadata: dict[str, Any] | None

    def save(self, update_fields: list[str] | None = None) -> None: ...


class SLAThresholdsProtocol(Protocol):
    """Protocol for SLA thresholds configuration."""

    def get_all_thresholds(self) -> dict[str, timedelta]: ...


# =============================================================================
# SLA Drift Detection
# =============================================================================


class SLADriftDetector:
    """
    Detects drift between configured SLA thresholds and actual performance.

    This is a framework-agnostic implementation that can be used with
    any data access layer.
    """

    def __init__(
        self,
        get_sla_thresholds: Callable[[], SLAThresholdsProtocol],
        get_failed_operations: Callable[..., FailedOperationQuerySet],
        record_sla_breach: Callable[[str], None] | None = None,
    ):
        """
        Initialize detector with required dependencies.

        Args:
            get_sla_thresholds: Function to get SLA thresholds config
            get_failed_operations: Function to query failed operations
            record_sla_breach: Optional function to record metrics
        """
        self.get_sla_thresholds = get_sla_thresholds
        self.get_failed_operations = get_failed_operations
        self.record_sla_breach = record_sla_breach

    def check_drift(self) -> dict[str, Any]:
        """
        Compare configured SLA thresholds with actual recovery metrics.

        Returns:
            Dictionary with drift detection results
        """
        logger.info("drift_detection.sla_check_started")

        try:
            sla_config = self.get_sla_thresholds()
            all_thresholds = sla_config.get_all_thresholds()

            results = {
                "success": True,
                "checked_at": now().isoformat(),
                "domains_checked": [],
                "warnings": [],
                "metrics": {},
            }

            # Time windows for analysis - Settings에서 조회
            current_time = now()
            analysis_window = timedelta(hours=self._get_analysis_window_hours())
            window_start = current_time - analysis_window

            for domain, sla_threshold in all_thresholds.items():
                domain_result = self._analyze_domain_sla(
                    domain=domain,
                    sla_threshold=sla_threshold,
                    window_start=window_start,
                    current_time=current_time,
                )

                results["domains_checked"].append(domain)
                results["metrics"][domain] = domain_result["metrics"]

                if domain_result["warning"]:
                    results["warnings"].append(domain_result["warning"])
                    logger.warning(
                        f"[SLA Drift] WARNING: {domain_result['warning']['message']}"
                    )

            if results["warnings"]:
                self._send_drift_notifications(results["warnings"])
                logger.warning(
                    f"[SLA Drift] Completed with {len(results['warnings'])} warning(s)"
                )
            else:
                logger.info("drift_detection.sla_check_no_violations")

            return results

        except Exception as e:
            logger.error(
                f"[SLA Drift] Error during drift detection: {e}", exc_info=True
            )
            return {
                "success": False,
                "error": str(e),
                "checked_at": now().isoformat(),
            }

    @staticmethod
    def _get_analysis_window_hours() -> int:
        """Settings에서 analysis_window_hours 조회."""
        try:
            from selfhealing.settings.drift_detection import (
                get_drift_detection_settings,
            )

            return get_drift_detection_settings().analysis_window_hours
        except Exception:
            return 24  # 기본값

    def _analyze_domain_sla(
        self,
        domain: str,
        sla_threshold: timedelta,
        window_start,
        current_time,
    ) -> dict[str, Any]:
        """Analyze SLA performance for a specific domain."""
        sla_seconds = sla_threshold.total_seconds()

        # Query resolved operations in the window
        resolved_ops = self.get_failed_operations(
            domain=domain,
            status__in=["resolved", "rejected"],
            resolved_at__isnull=False,
            resolved_at__gte=window_start,
        )

        total_resolved = resolved_ops.count()

        if total_resolved == 0:
            return {
                "metrics": {
                    "total_resolved": 0,
                    "avg_recovery_seconds": None,
                    "max_recovery_seconds": None,
                    "sla_threshold_seconds": sla_seconds,
                    "sla_breach_count": 0,
                    "sla_breach_rate": 0.0,
                },
                "warning": None,
            }

        # Calculate recovery times
        recovery_stats = []
        sla_breaches = 0

        for op in resolved_ops:
            if op.resolved_at and op.created_at:
                recovery_seconds = (op.resolved_at - op.created_at).total_seconds()
                recovery_stats.append(recovery_seconds)
                if recovery_seconds > sla_seconds:
                    sla_breaches += 1

        if not recovery_stats:
            return {
                "metrics": {
                    "total_resolved": total_resolved,
                    "avg_recovery_seconds": None,
                    "max_recovery_seconds": None,
                    "sla_threshold_seconds": sla_seconds,
                    "sla_breach_count": 0,
                    "sla_breach_rate": 0.0,
                },
                "warning": None,
            }

        avg_recovery = sum(recovery_stats) / len(recovery_stats)
        max_recovery = max(recovery_stats)
        breach_rate = (sla_breaches / total_resolved) * 100

        # Count pending items approaching SLA breach
        pending_ops = self.get_failed_operations(
            domain=domain,
            status="pending",
        )

        pending_at_risk = 0
        for op in pending_ops:
            age = (current_time - op.created_at).total_seconds()
            if age > sla_seconds * 0.8:
                pending_at_risk += 1

        metrics = {
            "total_resolved": total_resolved,
            "avg_recovery_seconds": round(avg_recovery, 2),
            "max_recovery_seconds": round(max_recovery, 2),
            "sla_threshold_seconds": sla_seconds,
            "sla_breach_count": sla_breaches,
            "sla_breach_rate": round(breach_rate, 2),
            "pending_at_risk": pending_at_risk,
        }

        # Generate warning if needed
        warning = None

        if breach_rate > 10:
            warning = {
                "type": "SLA_BREACH_RATE_HIGH",
                "domain": domain,
                "severity": "critical" if breach_rate > 25 else "warning",
                "message": (
                    f"[{domain}] SLA 위반율이 {breach_rate:.1f}%입니다. "
                    f"(임계값: 10%) 설정 검토가 필요합니다."
                ),
                "metrics": metrics,
                "recommendation": (
                    "현재 복구 속도로는 설정된 SLA를 달성하기 어렵습니다. "
                    "SLA 조정 또는 복구 프로세스 개선을 검토하세요. "
                    "[ACTION REQUIRED: 운영자 검토 필요]"
                ),
            }
        elif avg_recovery > sla_seconds * 0.8:
            warning = {
                "type": "SLA_APPROACHING_LIMIT",
                "domain": domain,
                "severity": "warning",
                "message": (
                    f"[{domain}] 평균 복구 시간({avg_recovery/3600:.2f}시간)이 "
                    f"SLA({sla_seconds/3600:.1f}시간)의 80%를 초과했습니다."
                ),
                "metrics": metrics,
                "recommendation": (
                    "SLA 위반 가능성이 높아지고 있습니다. "
                    "사전 조치를 검토하세요. "
                    "[ACTION REQUIRED: 운영자 검토 필요]"
                ),
            }
        elif pending_at_risk > 5:
            warning = {
                "type": "PENDING_ITEMS_AT_RISK",
                "domain": domain,
                "severity": "warning",
                "message": (
                    f"[{domain}] {pending_at_risk}개 항목이 SLA 위반 위험에 있습니다. "
                    f"(SLA 80% 이상 소진)"
                ),
                "metrics": metrics,
                "recommendation": (
                    "PENDING 상태의 항목 중 다수가 SLA 만료에 근접해 있습니다. "
                    "즉시 검토가 필요합니다. "
                    "[ACTION REQUIRED: 운영자 검토 필요]"
                ),
            }

        return {
            "metrics": metrics,
            "warning": warning,
        }

    def _send_drift_notifications(self, warnings: list[dict]) -> None:
        """Send notifications for SLA drift warnings."""
        from selfhealing.services.security_notification import (
            get_security_notification_service,
        )

        service = get_security_notification_service()

        for warning in warnings:
            domain = warning.get("domain", "unknown")
            severity = warning.get("severity", "warning")
            warning_type = warning.get("type", "SLA_DRIFT")

            # Record SLA breach metric if applicable
            if self.record_sla_breach and warning_type == "SLA_BREACH_RATE_HIGH":
                self.record_sla_breach(domain)

            # Log warning
            logger.warning(
                f"[SLADriftWarning] domain={domain} "
                f"type={warning_type} "
                f"severity={severity} "
                f"message={warning.get('message')} "
                f"recommendation={warning.get('recommendation')}"
            )

            # Send notification via SecurityNotificationService
            try:
                service.send_alert(
                    title=f"[SLA Drift] {domain}",
                    message=warning.get("message", ""),
                    severity=severity,
                    channels=["slack"],
                    metadata={
                        "type": warning_type,
                        "domain": domain,
                        "recommendation": warning.get("recommendation", ""),
                        **warning.get("metrics", {}),
                    },
                )
            except Exception as e:
                logger.error(
                    "sla_drift_warning.failed_send_notification",
                    error=e,
                )


# =============================================================================
# Chaos Experiment Cleanup
# =============================================================================


class ChaosExperimentCleaner:
    """Cleans up expired chaos experiments."""

    def __init__(
        self,
        resolve_expired_experiments: Callable[[], int],
    ):
        """
        Initialize cleaner with dependencies.

        Args:
            resolve_expired_experiments: Function to resolve expired experiments
        """
        self.resolve_expired_experiments = resolve_expired_experiments

    def cleanup(self) -> dict[str, Any]:
        """
        Clean up expired chaos experiments.

        Returns:
            Dictionary with cleanup results
        """
        logger.info("chaos_cleanup.starting_expired_chaos_experiment")

        try:
            resolved_count = self.resolve_expired_experiments()

            logger.info(
                f"[ChaosCleanup] Completed - resolved {resolved_count} expired experiments"
            )

            return {
                "success": True,
                "cleaned_at": now().isoformat(),
                "resolved_count": resolved_count,
            }

        except Exception as e:
            logger.error(f"[ChaosCleanup] Error during cleanup: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "cleaned_at": now().isoformat(),
            }


# =============================================================================
# Decision Recording
# =============================================================================


class DecisionRecorder:
    """Records human decisions made based on forensic advisories."""

    def __init__(
        self,
        get_failed_operation: Callable[[int], DriftDetectionOperationProtocol],
    ):
        """
        Initialize recorder with dependencies.

        Args:
            get_failed_operation: Function to get operation by ID
        """
        self.get_failed_operation = get_failed_operation

    def record(
        self,
        operation_id: int,
        decision: str,
        decided_by: str,
        notes: str = "",
    ) -> dict[str, Any]:
        """
        Record a human decision.

        Args:
            operation_id: ID of the FailedOperation
            decision: Decision made (approved_replay, rejected, escalated)
            decided_by: Username or ID of decision maker
            notes: Additional notes

        Returns:
            Dictionary with recording result
        """
        logger.info(
            f"[DecisionRecord] Recording decision for operation {operation_id}: "
            f"decision={decision}, decided_by={decided_by}"
        )

        try:
            operation = self.get_failed_operation(operation_id)

            advisory = (
                operation.metadata.get("forensic_advisory", {})
                if operation.metadata
                else {}
            )

            decision_record = {
                "decided_at": now().isoformat(),
                "decided_by": decided_by,
                "decision": decision,
                "notes": notes,
                "advisory_at_decision": advisory.get("analyzed_at", ""),
                "advisory_recommendation": advisory.get("recommended_action", ""),
                "advisory_confidence": advisory.get("confidence", 0),
            }

            if operation.metadata is None:
                operation.metadata = {}

            if "decision_records" not in operation.metadata:
                operation.metadata["decision_records"] = []

            operation.metadata["decision_records"].append(decision_record)
            operation.save(update_fields=["metadata", "updated_at"])

            logger.info(
                f"[DecisionRecord] Recorded: operation={operation_id} "
                f"decision={decision} decided_by={decided_by} "
                f"advisory_recommendation={advisory.get('recommended_action', 'N/A')}"
            )

            return {
                "success": True,
                "operation_id": operation_id,
                "decision_record": decision_record,
            }

        except Exception as e:
            logger.error(
                f"[DecisionRecord] Error recording decision: {e}", exc_info=True
            )
            return {
                "success": False,
                "error": str(e),
            }
