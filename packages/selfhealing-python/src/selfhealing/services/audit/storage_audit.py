"""
Storage & Task Audit Helpers

Layered Storage, Drift Reconciliation, Celery Task 관련 Audit 로깅.

Usage:
    from selfhealing.services.audit.storage_audit import (
        log_storage_failure_audit,
        log_storage_recovery_audit,
        log_drift_reconciliation_audit,
        log_config_apply_audit,
        log_chaos_scheduler_audit,
        log_governance_task_audit,
        log_traffic_aware_replay_audit,
        log_drift_detection_audit,
    )
"""

from __future__ import annotations

import structlog
from typing import Any

from selfhealing.services.audit.base import (
    _get_audit_adapter,
    _try_add_to_buffer,
    _write_to_wal,
)

logger = structlog.get_logger()


# ============================================================
# Layered Storage Audit Helpers
# ============================================================


def log_storage_failure_audit(
    storage_type: str,
    adapter_type: str,
    operation: str,
    service_name: str,
    error_type: str,
    error_message: str,
    consecutive_failures: int,
    trace_id: str | None = None,
    request: Any = None,
) -> int | None:
    """
    Layered Storage L2 장애 발생을 Audit 로그에 기록.

    L2 저장소(Redis/Django)가 timeout이나 error로 실패할 때 호출됩니다.
    WAL 기반 누락 0 보장.
    """
    details = {
        "storage_type": storage_type,
        "adapter_type": adapter_type,
        "operation": operation,
        "service_name": service_name,
        "error_type": error_type,
        "consecutive_failures": consecutive_failures,
        "trace_id": trace_id,
        "message": f"L2 storage failure: {error_type} during {operation} for {service_name}",
    }
    details = {k: v for k, v in details.items() if v is not None}

    wal_seq = _write_to_wal(
        event_type="STORAGE_FAILURE",
        source="LayeredStorageRepository",
        details=details,
        success=False,
        error_message=error_message,
        target_id=service_name,
    )

    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType

            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.CONFIG_CHANGE,
                source="LayeredStorageRepository",
                details=details,
                success=False,
                error_message=error_message,
                target_id=service_name,
            )
            if added:
                return wal_seq
        except ImportError:
            pass

    logger.warning(
        f"[StorageAudit] STORAGE_FAILURE | storage={storage_type} | "
        f"adapter={adapter_type} | op={operation} | service={service_name} | "
        f"error={error_type} | failures={consecutive_failures}"
    )
    return wal_seq


def log_storage_recovery_audit(
    storage_type: str,
    adapter_type: str,
    total_failures: int,
    downtime_seconds: float | None = None,
    trace_id: str | None = None,
    request: Any = None,
) -> int | None:
    """
    Layered Storage L2 복구를 Audit 로그에 기록.

    L2 저장소가 장애에서 복구되었을 때 호출됩니다.
    WAL 기반 누락 0 보장.
    """
    details = {
        "storage_type": storage_type,
        "adapter_type": adapter_type,
        "total_failures": total_failures,
        "downtime_seconds": downtime_seconds,
        "trace_id": trace_id,
        "message": f"L2 storage recovered after {total_failures} failures",
    }
    details = {k: v for k, v in details.items() if v is not None}

    wal_seq = _write_to_wal(
        event_type="STORAGE_RECOVERY",
        source="LayeredStorageRepository",
        details=details,
        success=True,
        error_message=None,
        target_id=adapter_type,
    )

    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType

            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.CONFIG_CHANGE,
                source="LayeredStorageRepository",
                details=details,
                success=True,
                error_message=None,
                target_id=adapter_type,
            )
            if added:
                return wal_seq
        except ImportError:
            pass

    logger.info(
        f"[StorageAudit] STORAGE_RECOVERY | storage={storage_type} | "
        f"adapter={adapter_type} | total_failures={total_failures} | "
        f"downtime={downtime_seconds}s"
    )
    return wal_seq


def log_drift_reconciliation_audit(
    adapter_type: str,
    total_checked: int,
    reconciled: int,
    l1_wins: int,
    l2_wins: int,
    error_count: int,
    trace_id: str | None = None,
    request: Any = None,
) -> int | None:
    """
    Layered Storage Drift 복구를 Audit 로그에 기록.

    L2 복구 시 L1과 L2 간의 데이터 불일치를 조정했을 때 호출됩니다.
    WAL 기반 누락 0 보장.
    """
    details = {
        "adapter_type": adapter_type,
        "total_checked": total_checked,
        "reconciled": reconciled,
        "l1_wins": l1_wins,
        "l2_wins": l2_wins,
        "error_count": error_count,
        "trace_id": trace_id,
        "message": f"Drift reconciliation: {reconciled}/{total_checked} items reconciled (L1:{l1_wins}, L2:{l2_wins})",
    }
    details = {k: v for k, v in details.items() if v is not None}

    wal_seq = _write_to_wal(
        event_type="DRIFT_RECONCILIATION",
        source="DriftReconciler",
        details=details,
        success=error_count == 0,
        error_message=(
            f"{error_count} errors during reconciliation" if error_count > 0 else None
        ),
        target_id=adapter_type,
    )

    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType

            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.CONFIG_CHANGE,
                source="DriftReconciler",
                details=details,
                success=error_count == 0,
                error_message=f"{error_count} errors" if error_count > 0 else None,
                target_id=adapter_type,
            )
            if added:
                return wal_seq
        except ImportError:
            pass

    log_level = logger.info if error_count == 0 else logger.warning
    log_level(
        f"[StorageAudit] DRIFT_RECONCILIATION | adapter={adapter_type} | "
        f"checked={total_checked} | reconciled={reconciled} | "
        f"l1_wins={l1_wins} | l2_wins={l2_wins} | errors={error_count}"
    )
    return wal_seq


# ============================================================
# Celery Task Audit Helpers
# ============================================================


def log_config_apply_audit(
    pending_id: str | None = None,
    config_key: str | None = None,
    old_value: Any | None = None,
    new_value: Any | None = None,
    status: str = "applied",
    error_message: str | None = None,
    task_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> int | None:
    """
    설정 적용(config_apply) Celery task 실행을 Audit 로그에 기록.

    Celery task는 HTTP 요청 컨텍스트가 없으므로 직접 WAL에 기록합니다.
    """
    audit_details = {
        "pending_id": pending_id,
        "config_key": config_key,
        "old_value": old_value,
        "new_value": new_value,
        "status": status,
        "task_id": task_id,
    }
    if details:
        audit_details.update(details)
    audit_details = {k: v for k, v in audit_details.items() if v is not None}

    success = status in ("applied", "success")

    wal_seq = _write_to_wal(
        event_type="CONFIG_CHANGE",
        source="ConfigApplyTask",
        details=audit_details,
        success=success,
        error_message=error_message,
        target_id=pending_id or config_key,
    )

    adapter = _get_audit_adapter()
    if adapter is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType

            adapter.record(
                event_type=AuditEventType.CONFIG_CHANGE,
                source="ConfigApplyTask",
                details=audit_details,
                success=success,
                error_message=error_message,
                target_id=pending_id or config_key,
            )
        except Exception as e:
            logger.debug(
                "config_apply_audit.adapter_record_failed",
                error=e,
            )

    log_func = logger.info if success else logger.warning
    log_func(
        f"[ConfigApplyAudit] {status.upper()} | key={config_key} | "
        f"pending_id={pending_id} | task_id={task_id}"
    )
    return wal_seq


def log_chaos_scheduler_audit(
    experiment_id: str | None = None,
    experiment_name: str | None = None,
    action: str = "scheduled",
    status: str = "started",
    target_service: str | None = None,
    error_message: str | None = None,
    task_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> int | None:
    """
    Chaos 스케줄러(chaos_scheduler) Celery task 실행을 Audit 로그에 기록.

    Celery task는 HTTP 요청 컨텍스트가 없으므로 직접 WAL에 기록합니다.
    """
    if action == "cleanup":
        event_type_str = "CHAOS_ROLLBACK_TRIGGERED"
    elif status in ("completed", "success"):
        event_type_str = "CHAOS_EXPERIMENT_COMPLETED"
    elif status == "started" or action == "scheduled":
        event_type_str = "CHAOS_EXPERIMENT_STARTED"
    else:
        event_type_str = "CHAOS_INJECTION_APPLIED"

    audit_details = {
        "experiment_id": experiment_id,
        "experiment_name": experiment_name,
        "action": action,
        "status": status,
        "target_service": target_service,
        "task_id": task_id,
    }
    if details:
        audit_details.update(details)
    audit_details = {k: v for k, v in audit_details.items() if v is not None}

    success = status not in ("failed", "blocked", "error")

    wal_seq = _write_to_wal(
        event_type=event_type_str,
        source="ChaosSchedulerTask",
        details=audit_details,
        success=success,
        error_message=error_message,
        domain="chaos",
        target_id=experiment_id,
    )

    adapter = _get_audit_adapter()
    if adapter is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType

            event_type_map = {
                "CHAOS_EXPERIMENT_STARTED": AuditEventType.CHAOS_EXPERIMENT_STARTED,
                "CHAOS_EXPERIMENT_COMPLETED": AuditEventType.CHAOS_EXPERIMENT_COMPLETED,
                "CHAOS_INJECTION_APPLIED": AuditEventType.CHAOS_INJECTION_APPLIED,
                "CHAOS_ROLLBACK_TRIGGERED": AuditEventType.CHAOS_ROLLBACK_TRIGGERED,
            }
            adapter.record(
                event_type=event_type_map.get(
                    event_type_str, AuditEventType.CHAOS_EXPERIMENT_STARTED
                ),
                source="ChaosSchedulerTask",
                details=audit_details,
                success=success,
                error_message=error_message,
                domain="chaos",
                target_id=experiment_id,
            )
        except Exception as e:
            logger.debug(
                "chaos_scheduler_audit.adapter_record_failed",
                error=e,
            )

    log_func = logger.info if success else logger.warning
    log_func(
        f"[ChaosSchedulerAudit] {action.upper()} | status={status} | "
        f"experiment={experiment_name or experiment_id} | task_id={task_id}"
    )
    return wal_seq


def log_governance_task_audit(
    action: str = "expiry_check",
    emergency_level: int | None = None,
    previous_level: int | None = None,
    status: str = "completed",
    notification_sent: bool = False,
    auto_recovered: bool = False,
    hours_elapsed: float | None = None,
    error_message: str | None = None,
    task_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> int | None:
    """
    Governance(emergency mode expiry) Celery task 실행을 Audit 로그에 기록.

    Celery task는 HTTP 요청 컨텍스트가 없으므로 직접 WAL에 기록합니다.
    """
    if auto_recovered:
        event_type_str = "EMERGENCY_MODE_DEACTIVATED"
    elif emergency_level is not None and emergency_level > 0:
        event_type_str = "EMERGENCY_MODE_ACTIVATED"
    else:
        event_type_str = "EMERGENCY_MODE_DEACTIVATED"

    audit_details = {
        "action": action,
        "emergency_level": emergency_level,
        "previous_level": previous_level,
        "status": status,
        "notification_sent": notification_sent,
        "auto_recovered": auto_recovered,
        "hours_elapsed": hours_elapsed,
        "task_id": task_id,
    }
    if details:
        audit_details.update(details)
    audit_details = {k: v for k, v in audit_details.items() if v is not None}

    success = status not in ("failed", "error")

    wal_seq = _write_to_wal(
        event_type=event_type_str,
        source="GovernanceTask",
        details=audit_details,
        success=success,
        error_message=error_message,
        domain="governance",
    )

    adapter = _get_audit_adapter()
    if adapter is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType

            event_type_map = {
                "EMERGENCY_MODE_ACTIVATED": AuditEventType.EMERGENCY_MODE_ACTIVATED,
                "EMERGENCY_MODE_DEACTIVATED": AuditEventType.EMERGENCY_MODE_DEACTIVATED,
            }
            adapter.record(
                event_type=event_type_map.get(
                    event_type_str, AuditEventType.EMERGENCY_MODE_DEACTIVATED
                ),
                source="GovernanceTask",
                details=audit_details,
                success=success,
                error_message=error_message,
                domain="governance",
            )
        except Exception as e:
            logger.debug(
                "governance_task_audit.adapter_record_failed",
                error=e,
            )

    log_func = logger.info if success else logger.warning
    level_str = f"L{emergency_level}" if emergency_level is not None else "N/A"
    log_func(
        f"[GovernanceTaskAudit] {action.upper()} | level={level_str} | "
        f"status={status} | auto_recovered={auto_recovered} | task_id={task_id}"
    )
    return wal_seq


def log_traffic_aware_replay_audit(
    domain: str | None = None,
    status: str = "completed",
    total: int = 0,
    success_count: int = 0,
    failed_count: int = 0,
    skipped_reason: str | None = None,
    health_checks: dict[str, bool] | None = None,
    error_message: str | None = None,
    task_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> int | None:
    """
    Traffic-Aware Replay Celery task 실행을 Audit 로그에 기록.

    Celery task는 HTTP 요청 컨텍스트가 없으므로 직접 WAL에 기록합니다.
    """
    audit_details = {
        "domain": domain,
        "status": status,
        "total": total,
        "success_count": success_count,
        "failed_count": failed_count,
        "skipped_reason": skipped_reason,
        "health_checks": health_checks,
        "task_id": task_id,
    }
    if details:
        audit_details.update(details)
    audit_details = {k: v for k, v in audit_details.items() if v is not None}

    success = status in ("completed", "success") and failed_count == 0

    wal_seq = _write_to_wal(
        event_type="DLQ_REPLAY",
        source="TrafficAwareReplayTask",
        details=audit_details,
        success=success,
        error_message=error_message or skipped_reason,
        domain=domain or "dlq",
    )

    adapter = _get_audit_adapter()
    if adapter is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType

            adapter.record(
                event_type=AuditEventType.DLQ_REPLAY,
                source="TrafficAwareReplayTask",
                details=audit_details,
                success=success,
                error_message=error_message or skipped_reason,
                domain=domain or "dlq",
            )
        except Exception as e:
            logger.debug(
                "traffic_aware_replay_audit.adapter_record_failed",
                error=e,
            )

    log_func = logger.info if status == "completed" else logger.warning
    log_func(
        f"[TrafficAwareReplayAudit] {status.upper()} | domain={domain} | "
        f"total={total} | success={success_count} | failed={failed_count} | task_id={task_id}"
    )
    return wal_seq


def log_drift_detection_audit(
    check_type: str = "sla_drift",
    status: str = "completed",
    drift_detected: bool = False,
    drift_details: dict[str, Any] | None = None,
    operations_analyzed: int = 0,
    error_message: str | None = None,
    task_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> int | None:
    """
    Drift Detection Celery task 실행을 Audit 로그에 기록.

    Celery task는 HTTP 요청 컨텍스트가 없으므로 직접 WAL에 기록합니다.
    """
    audit_details = {
        "check_type": check_type,
        "status": status,
        "drift_detected": drift_detected,
        "drift_details": drift_details,
        "operations_analyzed": operations_analyzed,
        "task_id": task_id,
    }
    if details:
        audit_details.update(details)
    audit_details = {k: v for k, v in audit_details.items() if v is not None}

    success = status not in ("error", "failed")

    wal_seq = _write_to_wal(
        event_type="CONFIG_CHANGE",
        source="DriftDetectionTask",
        details=audit_details,
        success=success,
        error_message=error_message,
        domain="drift_detection",
    )

    adapter = _get_audit_adapter()
    if adapter is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType

            adapter.record(
                event_type=AuditEventType.CONFIG_CHANGE,
                source="DriftDetectionTask",
                details=audit_details,
                success=success,
                error_message=error_message,
                domain="drift_detection",
            )
        except Exception as e:
            logger.debug(
                "drift_detection_audit.adapter_record_failed",
                error=e,
            )

    log_func = logger.info if success else logger.warning
    drift_str = "DRIFT_DETECTED" if drift_detected else "NO_DRIFT"
    log_func(
        f"[DriftDetectionAudit] {check_type.upper()} | {drift_str} | "
        f"analyzed={operations_analyzed} | status={status} | task_id={task_id}"
    )
    return wal_seq
