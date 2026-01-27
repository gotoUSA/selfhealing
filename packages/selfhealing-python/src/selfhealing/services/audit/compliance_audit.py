"""
Compliance, Security, FinOps Audit Helpers

규정 준수, 보안 위반, Blast Radius, FinOps 비용, 데이터 접근 관련 Audit 로깅.

Usage:
    from selfhealing.services.audit.compliance_audit import (
        log_compliance_audit,
        log_security_violation_audit,
        log_blast_radius_audit,
        log_finops_audit,
        log_data_access_audit,
    )
"""

from __future__ import annotations

import logging
from typing import Any

from selfhealing.services.audit.base import _try_add_to_buffer, _write_to_wal

logger = logging.getLogger(__name__)


# =============================================================================
# Security Audit Helpers (Complexity Reduction)
# =============================================================================

# Event type mapping based on action
_SECURITY_EVENT_TYPE_MAP = {
    "block_ip": "SECURITY_IP_BLOCKED",
    "invalidate_session": "SECURITY_SESSION_INVALIDATED",
}


def _get_security_event_type(action: str) -> str:
    """Get event type based on action."""
    return _SECURITY_EVENT_TYPE_MAP.get(action, "SECURITY_VIOLATION")


def _get_buffer_event_type(action: str):
    """Get buffer event type based on action."""
    from selfhealing.audit.event_buffer import AuditEventType

    mapping = {
        "block_ip": AuditEventType.SECURITY_IP_BLOCKED,
        "invalidate_session": AuditEventType.SECURITY_SESSION_INVALIDATED,
    }
    return mapping.get(action, AuditEventType.SECURITY_VIOLATION)


def _log_security_event(
    event_type: str,
    violation_type: str,
    action: str,
    target: str,
    result: str,
    severity: str,
):
    """Log security event with appropriate level based on severity."""
    log_msg = f"[SecurityAudit] {event_type} | type={violation_type} | action={action} | target={target} | result={result}"
    severity_logger = {"critical": logger.critical, "high": logger.warning}.get(
        severity, logger.info
    )
    severity_logger(log_msg)


def log_security_violation_audit(
    violation_type: str,
    action: str,
    target: str,
    result: str,
    severity: str = "medium",
    operator: str = "system",
    incident_id: int | None = None,
    source_ip: str | None = None,
    user_id: int | None = None,
    details: dict[str, Any] | None = None,
    request: Any = None,
) -> int | None:
    """
    보안 위반 처리 이벤트를 Audit 로그에 기록.

    Args:
        violation_type: 위반 유형 (e.g., "token_forged", "injection_attempt")
        action: 수행된 조치 (e.g., "block_ip", "invalidate_session", "handle_violation")
        target: 조치 대상 (e.g., "ip:1.2.3.4", "user:123")
        result: 결과 (e.g., "success", "failed")
        severity: 심각도 (low, medium, high, critical)
        operator: 수행 주체 (e.g., "system", "admin@example.com")
        incident_id: 보안 인시던트 ID
        source_ip: 원본 IP 주소
        user_id: 관련 사용자 ID
        details: 추가 상세 정보
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)

    Returns:
        WAL 시퀀스 번호 (WAL 기록 성공 시), None (실패 시)
    """
    audit_details = {
        "violation_type": violation_type,
        "action": action,
        "target": target,
        "result": result,
        "severity": severity,
        "operator": operator,
        "incident_id": incident_id,
        "source_ip": source_ip,
        "user_id": user_id,
    }
    if details:
        audit_details["extra_details"] = details
    audit_details = {k: v for k, v in audit_details.items() if v is not None}

    event_type = _get_security_event_type(action)
    success = result == "success"
    error_message = None if success else f"Security action failed: {result}"
    target_id = str(incident_id) if incident_id else None

    wal_seq = _write_to_wal(
        event_type=event_type,
        source="SecurityViolationService",
        details=audit_details,
        success=success,
        error_message=error_message,
        target_id=target_id,
    )

    if request is not None:
        try:
            buffer_event_type = _get_buffer_event_type(action)
            added = _try_add_to_buffer(
                request=request,
                event_type=buffer_event_type,
                source="SecurityViolationService",
                details=audit_details,
                success=success,
                error_message=error_message,
                target_id=target_id,
            )
            if added:
                return wal_seq
        except ImportError:
            pass

    _log_security_event(event_type, violation_type, action, target, result, severity)

    return wal_seq


def log_region_isolation_audit(
    region: str,
    action: str,
    result: str,
    reason: str | None = None,
    duration_seconds: int | None = None,
    operator: str = "system",
    details: dict[str, Any] | None = None,
    request: Any = None,
) -> int | None:
    """
    리전 격리/복원 이벤트를 Audit 로그에 기록.

    리전 단위 트래픽 차단 및 복원을 기록합니다.
    WAL 기반 누락 0 보장.

    Args:
        region: 대상 리전 (e.g., "tokyo", "seoul")
        action: 수행된 조치 ("isolate" 또는 "restore")
        result: 결과 (e.g., "success", "failed")
        reason: 격리/복원 사유
        duration_seconds: 격리 지속 시간 (초)
        operator: 수행 주체 (클러스터 ID 또는 운영자)
        details: 추가 상세 정보
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)

    Returns:
        WAL 시퀀스 번호 (WAL 기록 성공 시), None (실패 시)
    """
    audit_details = {
        "region": region,
        "action": action,
        "result": result,
        "reason": reason,
        "duration_seconds": duration_seconds,
        "operator": operator,
    }
    if details:
        audit_details["extra_details"] = details
    audit_details = {k: v for k, v in audit_details.items() if v is not None}

    event_type = "REGION_ISOLATED" if action == "isolate" else "REGION_RESTORED"
    success = result == "success"

    wal_seq = _write_to_wal(
        event_type=event_type,
        source="RegionalIsolationGate",
        details=audit_details,
        success=success,
        error_message=None if success else f"Region {action} failed",
        target_id=region,
    )

    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType

            buffer_event_type = (
                AuditEventType.REGION_ISOLATED
                if action == "isolate"
                else AuditEventType.REGION_RESTORED
            )

            added = _try_add_to_buffer(
                request=request,
                event_type=buffer_event_type,
                source="RegionalIsolationGate",
                details=audit_details,
                success=success,
                error_message=None if success else f"Region {action} failed",
                target_id=region,
            )
            if added:
                return wal_seq
        except ImportError:
            pass

    if action == "isolate":
        logger.warning(
            f"[RegionIsolationAudit] ISOLATED | region={region} | "
            f"reason={reason} | duration={duration_seconds}s | by={operator}"
        )
    else:
        logger.info(
            f"[RegionIsolationAudit] RESTORED | region={region} | by={operator}"
        )

    return wal_seq


def log_compliance_audit(
    stage_name: str,
    standard: str,
    check_id: str | None = None,
    passed: bool = True,
    violation_id: str | None = None,
    severity: str | None = None,
    message: str | None = None,
    details: dict[str, Any] | None = None,
    compliance_score: float | None = None,
    request: Any = None,
) -> int | None:
    """
    Compliance 검사 결과를 Audit 로그에 기록.

    규정 준수 검사 결과 (통과 또는 위반)를 기록합니다.
    WAL 기반 누락 0 보장.
    """
    audit_details = {
        "stage_name": stage_name,
        "standard": standard,
        "check_id": check_id,
        "passed": passed,
        "violation_id": violation_id,
        "severity": severity,
        "message": message,
        "compliance_score": compliance_score,
    }
    if details:
        audit_details["extra_details"] = details
    audit_details = {k: v for k, v in audit_details.items() if v is not None}

    event_type = "COMPLIANCE_CHECK_PASSED" if passed else "COMPLIANCE_VIOLATION"

    wal_seq = _write_to_wal(
        event_type=event_type,
        source="ComplianceService",
        details=audit_details,
        success=passed,
        error_message=message if not passed else None,
        target_id=check_id or violation_id,
    )

    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType

            buffer_event_type = (
                AuditEventType.COMPLIANCE_CHECK_PASSED
                if passed
                else AuditEventType.COMPLIANCE_VIOLATION
            )
            added = _try_add_to_buffer(
                request=request,
                event_type=buffer_event_type,
                source="ComplianceService",
                details=audit_details,
                success=passed,
                error_message=message if not passed else None,
                target_id=check_id or violation_id,
            )
            if added:
                return wal_seq
        except ImportError:
            pass

    if passed:
        logger.info(
            f"[ComplianceAudit] PASSED | stage={stage_name} | "
            f"standard={standard} | check={check_id}"
        )
    else:
        logger.warning(
            f"[ComplianceAudit] VIOLATION | stage={stage_name} | "
            f"standard={standard} | check={check_id} | "
            f"severity={severity} | msg={message}"
        )
    return wal_seq


def log_blast_radius_audit(
    experiment_id: str,
    blast_radius: str,
    target_service: str,
    action: str,
    allowed: bool = True,
    violations: list | None = None,
    approval_status: str | None = None,
    target_domain: str | None = None,
    traffic_percent: float | None = None,
    reason: str | None = None,
    request: Any = None,
) -> int | None:
    """
    Blast Radius 관련 이벤트를 Audit 로그에 기록.

    Chaos 실험의 영향 범위 검증, 격리 결정, 위반 감지를 기록합니다.
    WAL 기반 누락 0 보장.
    """
    details = {
        "experiment_id": experiment_id,
        "blast_radius": blast_radius,
        "target_service": target_service,
        "action": action,
        "allowed": allowed,
        "violations": violations,
        "approval_status": approval_status,
        "target_domain": target_domain,
        "traffic_percent": traffic_percent,
        "reason": reason,
    }
    details = {k: v for k, v in details.items() if v is not None}

    event_type = "BLAST_RADIUS_ISOLATION" if allowed else "BLAST_RADIUS_VIOLATION"

    wal_seq = _write_to_wal(
        event_type=event_type,
        source="BlastRadiusManager",
        details=details,
        success=allowed,
        error_message="; ".join(violations) if violations else None,
        target_id=experiment_id,
    )

    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType

            buffer_event_type = (
                AuditEventType.BLAST_RADIUS_ISOLATION
                if allowed
                else AuditEventType.BLAST_RADIUS_VIOLATION
            )
            added = _try_add_to_buffer(
                request=request,
                event_type=buffer_event_type,
                source="BlastRadiusManager",
                details=details,
                success=allowed,
                error_message="; ".join(violations) if violations else None,
                target_id=experiment_id,
            )
            if added:
                return wal_seq
        except ImportError:
            pass

    if allowed:
        logger.info(
            f"[BlastRadiusAudit] {action.upper()} | exp={experiment_id} | "
            f"radius={blast_radius} | service={target_service}"
        )
    else:
        logger.warning(
            f"[BlastRadiusAudit] VIOLATION | exp={experiment_id} | "
            f"radius={blast_radius} | service={target_service} | "
            f"violations={violations}"
        )
    return wal_seq


def log_finops_audit(
    stage_name: str,
    alert_type: str,
    current_cost: float | None = None,
    budget_limit: float | None = None,
    usage_percent: float | None = None,
    operation: str | None = None,
    severity: str = "warning",
    message: str | None = None,
    request: Any = None,
) -> int | None:
    """
    FinOps 비용 관련 이벤트를 Audit 로그에 기록.

    예산 임계값 초과, 예산 초과 차단 등을 기록합니다.
    WAL 기반 누락 0 보장.
    """
    details = {
        "stage_name": stage_name,
        "alert_type": alert_type,
        "current_cost": float(current_cost) if current_cost is not None else None,
        "budget_limit": float(budget_limit) if budget_limit is not None else None,
        "usage_percent": usage_percent,
        "operation": operation,
        "severity": severity,
        "message": message,
    }
    details = {k: v for k, v in details.items() if v is not None}

    event_type = (
        "FINOPS_BUDGET_EXCEEDED"
        if alert_type == "over_budget"
        else "FINOPS_THRESHOLD_EXCEEDED"
    )
    is_critical = alert_type == "over_budget"

    wal_seq = _write_to_wal(
        event_type=event_type,
        source="FinOpsService",
        details=details,
        success=not is_critical,
        error_message=message,
        target_id=stage_name,
    )

    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType

            buffer_event_type = (
                AuditEventType.FINOPS_BUDGET_EXCEEDED
                if is_critical
                else AuditEventType.FINOPS_THRESHOLD_EXCEEDED
            )
            added = _try_add_to_buffer(
                request=request,
                event_type=buffer_event_type,
                source="FinOpsService",
                details=details,
                success=not is_critical,
                error_message=message,
                target_id=stage_name,
            )
            if added:
                return wal_seq
        except ImportError:
            pass

    cost_str = f"${current_cost:.4f}" if current_cost is not None else "N/A"
    limit_str = f"${budget_limit:.2f}" if budget_limit is not None else "N/A"
    log_func = logger.critical if is_critical else logger.warning
    log_func(
        f"[FinOpsAudit] {alert_type.upper()} | stage={stage_name} | "
        f"cost={cost_str} | limit={limit_str} | severity={severity}"
    )
    return wal_seq


def log_data_access_audit(
    path: str,
    method: str,
    actor_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
    request: Any = None,
) -> int | None:
    """
    민감 데이터 접근을 Audit 로그에 기록.

    ADR-002에 따라 설정된 경로 패턴에 매칭되는 조회(Read) 요청을 기록합니다.
    WAL 기반 누락 0 보장.
    """
    audit_details = {
        "path": path,
        "method": method,
        "actor_id": actor_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
    }
    if details:
        audit_details["extra_details"] = details
    audit_details = {k: v for k, v in audit_details.items() if v is not None}

    wal_seq = _write_to_wal(
        event_type="DATA_ACCESS",
        source="DataAccessAudit",
        details=audit_details,
        success=True,
        target_id=resource_id,
    )

    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType

            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.DATA_ACCESS,
                source="DataAccessAudit",
                details=audit_details,
                success=True,
                target_id=resource_id,
            )
            if added:
                return wal_seq
        except ImportError:
            pass

    logger.info(
        f"[DataAccessAudit] {method} {path} | actor={actor_id} | "
        f"resource={resource_type}:{resource_id}"
    )
    return wal_seq
