"""
Chaos & Emergency Mode Audit Helpers

Chaos 실험, Kill Switch, Panic Threshold, Freeze Mode, Emergency Mode 관련 Audit 로깅.

Usage:
    from selfhealing.services.audit.chaos_audit import (
        log_chaos_experiment_audit,
        log_emergency_mode_audit,
        log_kill_switch_override_audit,
        log_panic_threshold_audit,
        log_freeze_mode_audit,
    )
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from selfhealing.services.audit.base import _write_to_wal, _try_add_to_buffer, _get_audit_adapter

logger = logging.getLogger(__name__)


def log_kill_switch_override_audit(
    service_name: str,
    action: str,
    reason: str = "",
    controlled_by_id: Optional[int] = None,
    request: Any = None,
) -> Optional[int]:
    """
    Kill Switch Override 이벤트를 Audit 로그에 기록.
    
    LOCKDOWN 상태에서 운영자가 수동으로 Kill Switch를 무시하고
    CB 상태를 변경할 때 기록합니다.
    """
    details = {
        "service_name": service_name,
        "action": action,
        "reason": reason,
        "controlled_by_id": controlled_by_id,
        "message": f"Kill Switch override for {action} on {service_name}",
        "override_type": "manual_control",
    }
    
    wal_seq = _write_to_wal(
        event_type="KILL_SWITCH_OVERRIDE",
        source="CircuitBreaker",
        details=details,
        success=True,
        target_id=service_name,
    )
    
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType
            
            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.MANUAL_OVERRIDE,
                source="CircuitBreaker",
                details=details,
                success=True,
                target_id=service_name,
            )
            if added:
                return wal_seq
        except ImportError:
            pass
    
    logger.warning(
        f"[KillSwitchOverride] {action.upper()} | service={service_name} | "
        f"by={controlled_by_id} | reason={reason or 'N/A'}"
    )
    return wal_seq


def log_panic_threshold_audit(
    open_rate: float,
    threshold: float,
    open_count: int,
    total_count: int,
    open_circuits: Optional[list] = None,
    action_taken: str = "emergency_level_3_escalation",
    halted_systems: Optional[list] = None,
    triggered_by: str = "PanicThresholdMonitor",
    request: Any = None,
) -> Optional[int]:
    """
    Panic Threshold 발동 이벤트를 Audit 로그에 기록.
    
    전체 CB 중 70% 이상이 OPEN 상태일 때 시스템 전체 붕괴로 판단하고
    Emergency Level 3를 자동 선포할 때 기록합니다.
    """
    details = {
        "open_rate": open_rate,
        "threshold": threshold,
        "open_count": open_count,
        "total_count": total_count,
        "open_circuits": open_circuits or [],
        "action_taken": action_taken,
        "halted_systems": halted_systems or ["replay", "canary_recovery", "auto_open", "auto_close"],
        "triggered_by": triggered_by,
        "root_cause_hypothesis": "인프라 전체 붕괴 감지 - 개별 서비스 장애 아님",
        "message": f"Panic Threshold triggered (Open Rate: {open_rate:.1f}%) - "
                   f"Escalating to Emergency Level 3",
    }
    
    wal_seq = _write_to_wal(
        event_type="PANIC_THRESHOLD_TRIGGERED",
        source="PanicThresholdMonitor",
        details=details,
        success=True,
        target_id="global",
    )
    
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType
            
            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.EMERGENCY_MODE_ACTIVATED,
                source="PanicThresholdMonitor",
                details=details,
                success=True,
                target_id="global",
            )
            if added:
                return wal_seq
        except ImportError:
            pass
    
    logger.critical(
        f"🚨 [PanicThreshold] TRIGGERED | open_rate={open_rate:.1f}% | "
        f"circuits={open_count}/{total_count} | action={action_taken}"
    )
    return wal_seq


def log_freeze_mode_audit(
    active: bool,
    reason: str = "",
    activated_by: str = "system",
    previous_state: Optional[bool] = None,
    emergency_level: Optional[str] = None,
    request: Any = None,
) -> Optional[int]:
    """
    Freeze Mode 활성화/비활성화 이벤트를 Audit 로그에 기록.
    
    LOCKDOWN 상태에서 모든 CB 자동 변경을 금지할 때 기록합니다.
    """
    action = "activated" if active else "deactivated"
    
    details = {
        "active": active,
        "reason": reason,
        "activated_by": activated_by,
        "previous_state": previous_state,
        "emergency_level": emergency_level,
        "message": f"Freeze Mode {action}: {reason}",
    }
    
    wal_seq = _write_to_wal(
        event_type="FREEZE_MODE_CHANGED",
        source="FreezeModeManager",
        details=details,
        success=True,
        target_id="global",
    )
    
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType
            
            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.GOVERNANCE_KILL_SWITCH,
                source="FreezeModeManager",
                details=details,
                success=True,
                target_id="global",
            )
            if added:
                return wal_seq
        except ImportError:
            pass
    
    logger.warning(
        f"[FreezeMode] {action.upper()} | by={activated_by} | "
        f"level={emergency_level or 'N/A'} | reason={reason or 'N/A'}"
    )
    return wal_seq


def log_chaos_experiment_audit(
    experiment_id: str,
    event_type: str,
    experiment_type: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    result: Optional[Dict[str, Any]] = None,
    dry_run: bool = False,
    ttl_seconds: Optional[int] = None,
    expires_at: Optional[str] = None,
    violations: Optional[list] = None,
    reason: Optional[str] = None,
    request: Any = None,
) -> str:
    """
    Chaos 실험 이벤트를 Audit 로그에 기록.
    
    실험 시작, 완료, 롤백 트리거 등을 기록합니다.
    WAL 기반 누락 0 보장.
    
    Returns:
        생성된 audit record ID
    """
    record_id = f"audit-{uuid.uuid4().hex[:8]}"
    
    details = {
        "experiment_id": experiment_id,
        "experiment_type": experiment_type,
        "event_type": event_type,
        "config": config,
        "result": result,
        "dry_run": dry_run,
        "ttl_seconds": ttl_seconds,
        "expires_at": expires_at,
        "violations": violations,
        "reason": reason,
        "record_id": record_id,
    }
    details = {k: v for k, v in details.items() if v is not None}
    
    event_type_mapping = {
        "experiment_started": "CHAOS_EXPERIMENT_STARTED",
        "experiment_completed": "CHAOS_EXPERIMENT_COMPLETED",
        "chaos_injection_started": "CHAOS_INJECTION_APPLIED",
        "chaos_injection_simulated": "CHAOS_INJECTION_APPLIED",
        "rollback_started": "CHAOS_ROLLBACK_TRIGGERED",
        "rollback_completed": "CHAOS_ROLLBACK_TRIGGERED",
        "kill_requested": "CHAOS_ROLLBACK_TRIGGERED",
        "auto_abort_ttl_expired": "CHAOS_ROLLBACK_TRIGGERED",
        "auto_abort_stop_condition": "CHAOS_ROLLBACK_TRIGGERED",
        "auto_rollback_triggered": "CHAOS_ROLLBACK_TRIGGERED",
        "steady_state_captured": "CHAOS_EXPERIMENT_STARTED",
    }
    wal_event_type = event_type_mapping.get(event_type, "CHAOS_EXPERIMENT_STARTED")
    
    _write_to_wal(
        event_type=wal_event_type,
        source="ChaosExperiment",
        details=details,
        success=True,
        target_id=experiment_id,
    )
    
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType as BufferEventType
            
            buffer_event_mapping = {
                "CHAOS_EXPERIMENT_STARTED": BufferEventType.CHAOS_EXPERIMENT_STARTED,
                "CHAOS_EXPERIMENT_COMPLETED": BufferEventType.CHAOS_EXPERIMENT_COMPLETED,
                "CHAOS_INJECTION_APPLIED": BufferEventType.CHAOS_INJECTION_APPLIED,
                "CHAOS_ROLLBACK_TRIGGERED": BufferEventType.CHAOS_ROLLBACK_TRIGGERED,
            }
            buffer_event_type = buffer_event_mapping.get(
                wal_event_type, 
                BufferEventType.CHAOS_EXPERIMENT_STARTED
            )
            
            added = _try_add_to_buffer(
                request=request,
                event_type=buffer_event_type,
                source="ChaosExperiment",
                details=details,
                success=True,
                target_id=experiment_id,
            )
            if added:
                return record_id
        except ImportError:
            pass
    
    logger.info(
        f"[ChaosAudit] {experiment_id} | {event_type} | {record_id}",
        extra={"audit_data": details}
    )
    return record_id


def log_emergency_mode_audit(
    action: str,
    level: str,
    is_active: bool,
    activated_by: Optional[str] = None,
    deactivated_by: Optional[str] = None,
    reason: Optional[str] = None,
    is_auto_triggered: bool = False,
    expires_at: Optional[str] = None,
    request: Any = None,
) -> Optional[int]:
    """
    Emergency Mode 상태 변경을 Audit 로그에 기록.
    
    비상 모드 활성화/비활성화/레벨 변경 등을 기록합니다.
    WAL 기반 누락 0 보장.
    """
    if action in ("activate", "auto_activate", "escalate"):
        event_type_str = "EMERGENCY_MODE_ACTIVATED"
    else:
        event_type_str = "EMERGENCY_MODE_DEACTIVATED"
    
    user = activated_by or deactivated_by or "system"
    
    details = {
        "action": action,
        "level": level,
        "is_active": is_active,
        "activated_by": activated_by,
        "deactivated_by": deactivated_by,
        "reason": reason,
        "is_auto_triggered": is_auto_triggered,
        "expires_at": expires_at,
        "severity": "warning" if action == "deactivate" else "critical",
        "tag": f"EMERGENCY_{action.upper()}",
    }
    details = {k: v for k, v in details.items() if v is not None}
    
    wal_seq = _write_to_wal(
        event_type=event_type_str,
        source="EmergencyModeManager",
        details=details,
        success=True,
    )
    
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType
            
            buffer_event_type = (
                AuditEventType.EMERGENCY_MODE_ACTIVATED 
                if event_type_str == "EMERGENCY_MODE_ACTIVATED"
                else AuditEventType.EMERGENCY_MODE_DEACTIVATED
            )
            
            added = _try_add_to_buffer(
                request=request,
                event_type=buffer_event_type,
                source="EmergencyModeManager",
                details=details,
                success=True,
            )
            if added:
                return wal_seq
        except ImportError:
            pass
    
    logger.info(
        f"[EmergencyModeAudit] {action.upper()} | level={level} | "
        f"user={user} | reason={reason or 'N/A'}"
    )
    
    # 기존 log_config_change 호환 호출
    try:
        from selfhealing.audit import log_config_change
        
        log_config_change(
            config_type="emergency_mode",
            config_key="state",
            old_value=None,
            new_value=details,
            user=user,
        )
    except Exception as e:
        logger.debug(f"[EmergencyModeAudit] Fallback log_config_change failed: {e}")
    
    return wal_seq


def log_error_budget_blocked_audit(
    action: str,
    gate_status: str,
    error_budget_percent: Optional[float] = None,
    threshold_percent: Optional[float] = None,
    reason: Optional[str] = None,
    request: Any = None,
    # 포렌식 필드: 명시적 전달 또는 자동 추출
    blocked_request_trace_id: Optional[str] = None,
    actor_roles: Optional[list] = None,
) -> Optional[int]:
    """
    Error Budget Gate 차단을 Audit 로그에 기록.
    
    에러 예산 부족으로 인한 자동화 차단을 기록합니다.
    WAL 기반 누락 0 보장.
    
    Args:
        action: 차단된 액션 이름 (예: "dlq_auto_replay")
        gate_status: 게이트 상태 (BLOCKED, CRITICAL 등)
        error_budget_percent: 현재 에러 예산 잔여율 (%)
        threshold_percent: 차단 임계치 (%)
        reason: 차단 사유
        request: Django request 객체 (선택)
        blocked_request_trace_id: 차단된 요청의 Trace ID.
                                  None이면 현재 컨텍스트에서 자동 추출.
        actor_roles: 실행 주체의 역할 목록.
                    None이면 현재 컨텍스트에서 자동 추출.
    
    Returns:
        WAL 시퀀스 번호 (성공 시), None (실패 시)
    
    Example:
        >>> log_error_budget_blocked_audit(
        ...     action="dlq_auto_replay",
        ...     gate_status="CRITICAL",
        ...     error_budget_percent=5.2,
        ...     threshold_percent=10.0,
        ...     reason="에러 예산 부족으로 자동 실행 차단",
        ... )
    """
    import time
    
    # trace_id 자동 추출 (명시적으로 전달되지 않은 경우)
    if blocked_request_trace_id is None:
        try:
            from selfhealing.audit.trace import get_trace_id
            blocked_request_trace_id = get_trace_id()
        except ImportError:
            pass
        except Exception:
            blocked_request_trace_id = None
    
    # actor_roles 자동 추출 (명시적으로 전달되지 않은 경우)
    final_actor_roles = actor_roles
    if final_actor_roles is None:
        try:
            from selfhealing.context.actor_context import ActorContext
            if ActorContext.is_set():
                final_actor_roles = ActorContext.get_current().roles
            else:
                final_actor_roles = []
        except ImportError:
            final_actor_roles = []
        except Exception:
            final_actor_roles = []
    
    details = {
        "action": action,
        "gate_status": gate_status,
        "error_budget_percent": error_budget_percent,
        "threshold_percent": threshold_percent,
        "reason": reason,
        "manual_mode_enforced": True,
        # 포렌식 필드: 차단 시점의 컨텍스트 정보
        "blocked_request_trace_id": blocked_request_trace_id,
        "actor_roles_at_block": final_actor_roles,
        "blocked_at": time.time(),
    }
    details = {k: v for k, v in details.items() if v is not None}
    
    wal_seq = _write_to_wal(
        event_type="ERROR_BUDGET_BLOCKED",
        source="ErrorBudgetGate",
        details=details,
        success=False,
        error_message=reason,
        target_id=action,
        # 명시적 전달 (WAL 최상위 레벨에도 기록)
        actor_roles=final_actor_roles,
        trace_id=blocked_request_trace_id,
    )
    
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType
            
            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.ERROR_BUDGET_BLOCKED,
                source="ErrorBudgetGate",
                details=details,
                success=False,
                error_message=reason,
                target_id=action,
            )
            if added:
                return wal_seq
        except ImportError:
            pass
    
    budget_str = f"{error_budget_percent:.1f}%" if error_budget_percent is not None else "N/A"
    trace_str = blocked_request_trace_id[:8] if blocked_request_trace_id else "N/A"
    logger.warning(
        f"[ErrorBudgetAudit] BLOCKED | action={action} | "
        f"budget={budget_str} | status={gate_status} | trace_id={trace_str}"
    )
    return wal_seq
