"""
SLA Drift Detection Tasks - Django/Celery Adapter

Celery tasks that wrap the framework-agnostic drift detection logic.
The actual logic lives in selfhealing.tasks.drift_detection.

Core Principle: "System provides data, humans make decisions."
These tasks ONLY generate warnings - they NEVER auto-adjust settings.

Reference: docs/self_healing/middleware_system/02_LOGIC_ENGINE.md
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


# =============================================================================
# Django-specific Dependencies
# =============================================================================


def _get_sla_thresholds():
    """Get SLA thresholds from Django config."""
    from selfhealing.services import get_sla_thresholds

    return get_sla_thresholds()


def _get_failed_operations(**kwargs):
    """Get FailedOperation queryset with filters."""
    from shopping.models.failed_operation import FailedOperation

    return FailedOperation.objects.filter(**kwargs)


def _get_failed_operation_by_id(operation_id: int):
    """Get single FailedOperation by ID."""
    from shopping.models.failed_operation import FailedOperation

    return FailedOperation.objects.get(id=operation_id)


def _record_sla_breach(domain: str):
    """Record SLA breach metric."""
    try:
        from selfhealing.services.metrics.recorders import record_sla_breach

        record_sla_breach(domain)
    except Exception:
        pass


def _resolve_expired_chaos_experiments() -> int:
    """Resolve expired chaos experiments using Django models."""
    from django.utils import timezone
    from shopping.models.failed_operation import FailedOperation
    from selfhealing.services.chaos_context import get_chaos_context, ChaosExperimentContext

    pending_chaos = FailedOperation.objects.filter(
        status__in=["pending", "replayed"],
        metadata__is_chaos_experiment=True,
    )

    resolved_count = 0
    now = timezone.now()

    for operation in pending_chaos:
        context = get_chaos_context(operation)
        if context and context.auto_resolve and context.is_expired():
            # Mark completed
            context.mark_completed("Auto-resolved: Experiment duration expired")
            operation.metadata["chaos_experiment_context"] = context.to_dict()

            operation.status = "resolved"
            operation.resolution_type = "auto_replay"
            operation.resolution_note = "[CHAOS Experiment] Auto-resolved: Experiment duration expired"
            operation.resolved_at = now

            operation.save(
                update_fields=[
                    "status",
                    "resolution_type",
                    "resolution_note",
                    "resolved_at",
                    "metadata",
                    "updated_at",
                ]
            )
            resolved_count += 1

    if resolved_count > 0:
        logger.info(f"[ChaosContext] Auto-resolved {resolved_count} expired chaos experiments")

    return resolved_count


# =============================================================================
# Celery Tasks
# =============================================================================


@shared_task(
    bind=True,
    name="shopping.tasks.drift_detection_tasks.check_sla_drift",
    queue="maintenance",
    max_retries=1,
    time_limit=120,
    soft_time_limit=110,
)
def check_sla_drift(self) -> dict[str, Any]:
    """
    Compare configured SLA thresholds with actual recovery metrics.

    This task:
    1. Loads SLA thresholds from configuration
    2. Queries actual recovery times from database
    3. Generates SLADriftWarning if actual performance deviates from SLA
    
    Audit 기록 (Phase 4: 20_AUDIT_UNIFICATION_PLAN.md):
    - CONFIG_CHANGE 이벤트로 drift 감지 결과 기록

    IMPORTANT: This task ONLY generates warnings.
    It NEVER modifies system configuration.

    Returns:
        Dictionary with drift detection results
    """
    from selfhealing.tasks.drift_detection import SLADriftDetector

    task_id = self.request.id
    
    try:
        detector = SLADriftDetector(
            get_sla_thresholds=_get_sla_thresholds,
            get_failed_operations=_get_failed_operations,
            record_sla_breach=_record_sla_breach,
        )

        result = detector.check_drift()
        
        # === Audit 기록 (Phase 4) ===
        try:
            from selfhealing.services.audit_helpers import log_drift_detection_audit
            
            log_drift_detection_audit(
                check_type="sla_drift",
                status=result.get("status", "completed"),
                drift_detected=result.get("drift_detected", False),
                drift_details=result.get("drift_details"),
                operations_analyzed=result.get("operations_analyzed", 0),
                task_id=task_id,
            )
        except Exception as audit_error:
            logger.debug(f"[DriftDetection] Audit logging failed: {audit_error}")
        
        return result
        
    except Exception as e:
        # === Audit 기록 (실패) ===
        try:
            from selfhealing.services.audit_helpers import log_drift_detection_audit
            
            log_drift_detection_audit(
                check_type="sla_drift",
                status="error",
                error_message=str(e),
                task_id=task_id,
            )
        except Exception:
            pass
        raise


@shared_task(
    bind=True,
    name="shopping.tasks.drift_detection_tasks.cleanup_expired_chaos_experiments",
    queue="maintenance",
    max_retries=1,
    time_limit=60,
    soft_time_limit=55,
)
def cleanup_expired_chaos_experiments(self) -> dict[str, Any]:
    """
    Clean up expired chaos experiments by auto-resolving them.

    This task runs periodically to:
    1. Find chaos experiment entries that have expired
    2. Auto-resolve them if auto_resolve=True
    
    Audit 기록 (Phase 4: 20_AUDIT_UNIFICATION_PLAN.md):
    - CONFIG_CHANGE 이벤트로 정리 결과 기록

    Returns:
        Dictionary with cleanup results
    """
    from selfhealing.tasks.drift_detection import ChaosExperimentCleaner

    task_id = self.request.id
    
    try:
        cleaner = ChaosExperimentCleaner(
            resolve_expired_experiments=_resolve_expired_chaos_experiments,
        )

        result = cleaner.cleanup()
        
        # === Audit 기록 (Phase 4) ===
        try:
            from selfhealing.services.audit_helpers import log_drift_detection_audit
            
            log_drift_detection_audit(
                check_type="chaos_cleanup",
                status=result.get("status", "completed"),
                task_id=task_id,
                details={
                    "resolved_count": result.get("resolved_count", 0),
                },
            )
        except Exception as audit_error:
            logger.debug(f"[DriftDetection] Audit logging failed: {audit_error}")
        
        return result
        
    except Exception as e:
        # === Audit 기록 (실패) ===
        try:
            from selfhealing.services.audit_helpers import log_drift_detection_audit
            
            log_drift_detection_audit(
                check_type="chaos_cleanup",
                status="error",
                error_message=str(e),
                task_id=task_id,
            )
        except Exception:
            pass
        raise


@shared_task(
    bind=True,
    name="shopping.tasks.drift_detection_tasks.record_advisory_decision",
    queue="maintenance",
    max_retries=2,
    time_limit=30,
    soft_time_limit=25,
)
def record_advisory_decision(
    self,
    operation_id: int,
    decision: str,
    decided_by: str,
    notes: str = "",
) -> dict[str, Any]:
    """
    Record a human decision made based on forensic advisory.

    This task creates an audit trail for decisions made on DLQ items.

    Args:
        operation_id: ID of the FailedOperation
        decision: Decision made (e.g., "approved_replay", "rejected", "escalated")
        decided_by: Username or ID of the decision maker
        notes: Additional notes about the decision

    Returns:
        Dictionary with recording result
    """
    from selfhealing.tasks.drift_detection import DecisionRecorder

    recorder = DecisionRecorder(
        get_failed_operation=_get_failed_operation_by_id,
    )

    return recorder.record(
        operation_id=operation_id,
        decision=decision,
        decided_by=decided_by,
        notes=notes,
    )
