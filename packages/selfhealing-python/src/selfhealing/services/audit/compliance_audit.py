"""
Compliance & FinOps Audit Helpers

규정 준수, Blast Radius, FinOps 비용, 데이터 접근 관련 Audit 로깅.

Usage:
    from selfhealing.services.audit.compliance_audit import (
        log_compliance_audit,
        log_blast_radius_audit,
        log_finops_audit,
        log_data_access_audit,
    )
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from selfhealing.services.audit.base import _write_to_wal, _try_add_to_buffer

logger = logging.getLogger(__name__)


def log_compliance_audit(
    stage_name: str,
    standard: str,
    check_id: Optional[str] = None,
    passed: bool = True,
    violation_id: Optional[str] = None,
    severity: Optional[str] = None,
    message: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    compliance_score: Optional[float] = None,
    request: Any = None,
) -> Optional[int]:
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
                AuditEventType.COMPLIANCE_CHECK_PASSED if passed 
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
    violations: Optional[list] = None,
    approval_status: Optional[str] = None,
    target_domain: Optional[str] = None,
    traffic_percent: Optional[float] = None,
    reason: Optional[str] = None,
    request: Any = None,
) -> Optional[int]:
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
                AuditEventType.BLAST_RADIUS_ISOLATION if allowed 
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
    current_cost: Optional[float] = None,
    budget_limit: Optional[float] = None,
    usage_percent: Optional[float] = None,
    operation: Optional[str] = None,
    severity: str = "warning",
    message: Optional[str] = None,
    request: Any = None,
) -> Optional[int]:
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
        "FINOPS_BUDGET_EXCEEDED" if alert_type == "over_budget" 
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
                AuditEventType.FINOPS_BUDGET_EXCEEDED if is_critical 
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
    actor_id: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    request: Any = None,
) -> Optional[int]:
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
