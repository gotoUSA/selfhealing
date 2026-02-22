"""
Circuit Breaker & Governance Audit Helpers

CB 상태 변경, Governance 차단, Rate Limit, Pool CB 관련 Audit 로깅.

Usage:
    from selfhealing.services.audit.cb_audit import (
        log_cb_state_change_audit,
        log_governance_blocked_audit,
        log_rate_limited_audit,
        log_pool_cb_rejection_audit,
    )
"""

from __future__ import annotations

import structlog
from typing import Any

from selfhealing.services.audit.base import _try_add_to_buffer, _write_to_wal

logger = structlog.get_logger()


def log_cb_state_change_audit(
    cb_name: str,
    old_state: str,
    new_state: str,
    reason: str | None = None,
    request: Any = None,
) -> int | None:
    """
    Circuit Breaker 상태 변경을 Audit 로그에 기록.

    WAL 기반 누락 0 보장.

    Args:
        cb_name: Circuit Breaker 이름
        old_state: 이전 상태 (closed, open, half_open)
        new_state: 새 상태
        reason: 상태 변경 사유
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)

    Returns:
        WAL 시퀀스 번호 (WAL 기록 성공 시), None (실패 시)
    """
    details = {
        "cb_name": cb_name,
        "old_state": old_state,
        "new_state": new_state,
        "reason": reason,
    }

    # === Step 1: WAL에 먼저 기록 ===
    wal_seq = _write_to_wal(
        event_type="CB_STATE_CHANGE",
        source="CircuitBreaker",
        details=details,
        success=True,
        target_id=cb_name,
    )

    # === Step 2: 버퍼 또는 직접 로깅 ===
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType

            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.CB_STATE_CHANGE,
                source="CircuitBreaker",
                details=details,
                success=True,
                target_id=cb_name,
            )
            if added:
                return wal_seq
        except ImportError:
            pass

    # Fallback: 직접 로깅
    logger.info(
        f"[CBAudit] STATE_CHANGE | cb={cb_name} | "
        f"{old_state} -> {new_state} | reason={reason or 'auto'}"
    )
    return wal_seq


def log_governance_blocked_audit(
    action: str,
    block_reason: str,
    details: dict | None = None,
    request: Any = None,
) -> int | None:
    """
    Governance 차단을 Audit 로그에 기록.

    WAL 기반 누락 0 보장.

    Args:
        action: 차단된 액션 (e.g., "auto_replay")
        block_reason: 차단 사유 (e.g., "kill_switch_active")
        details: 추가 상세 정보
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)

    Returns:
        WAL 시퀀스 번호 (WAL 기록 성공 시), None (실패 시)
    """
    event_details = {
        "action": action,
        "block_reason": block_reason,
        **(details or {}),
    }

    # === Step 1: WAL에 먼저 기록 ===
    wal_seq = _write_to_wal(
        event_type="GOVERNANCE_BLOCKED",
        source="GovernanceGuard",
        details=event_details,
        success=False,
        error_message=block_reason,
    )

    # === Step 2: 버퍼 또는 직접 로깅 ===
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType

            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.GOVERNANCE_BLOCKED,
                source="GovernanceGuard",
                details=event_details,
                success=False,
                error_message=block_reason,
            )
            if added:
                return wal_seq
        except ImportError:
            pass

    # Fallback: 직접 로깅
    logger.warning(
        f"[GovernanceAudit] BLOCKED | action={action} | reason={block_reason}"
    )
    return wal_seq


def log_rate_limited_audit(
    client_ip: str,
    endpoint: str,
    limit_type: str,
    request: Any = None,
) -> int | None:
    """
    Rate Limit 차단을 Audit 로그에 기록.

    WAL 기반 누락 0 보장.

    Args:
        client_ip: 클라이언트 IP
        endpoint: 차단된 엔드포인트
        limit_type: 제한 유형 (global, endpoint 등)
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)

    Returns:
        WAL 시퀀스 번호 (WAL 기록 성공 시), None (실패 시)
    """
    details = {
        "client_ip": client_ip,
        "endpoint": endpoint,
        "limit_type": limit_type,
    }

    # === Step 1: WAL에 먼저 기록 ===
    wal_seq = _write_to_wal(
        event_type="RATE_LIMITED",
        source="RateLimiter",
        details=details,
        success=False,
        error_message="rate_limited",
    )

    # === Step 2: 버퍼 또는 직접 로깅 ===
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType

            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.RATE_LIMITED,
                source="RateLimiter",
                details=details,
                success=False,
                error_message="rate_limited",
            )
            if added:
                return wal_seq
        except ImportError:
            pass

    # Fallback: 직접 로깅
    logger.info(
        f"[RateLimitAudit] BLOCKED | ip={client_ip} | "
        f"endpoint={endpoint} | type={limit_type}"
    )
    return wal_seq


def log_pool_cb_rejection_audit(
    pool_name: str,
    current_utilization: float,
    threshold: float,
    decision_source: str = "cached_pool_status",
    request: Any = None,
) -> int | None:
    """
    Pool Circuit Breaker 거부를 Audit 로그에 기록.

    WAL 기반 누락 0 보장.

    Args:
        pool_name: 커넥션 풀 이름
        current_utilization: 현재 사용률 (0.0 ~ 1.0)
        threshold: 거부 임계값
        decision_source: 결정 소스 (cached_pool_status, live_check 등)
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)

    Returns:
        WAL 시퀀스 번호 (WAL 기록 성공 시), None (실패 시)
    """
    details = {
        "pool_name": pool_name,
        "current_utilization": current_utilization,
        "threshold": threshold,
        "decision_source": decision_source,
    }

    # === Step 1: WAL에 먼저 기록 ===
    wal_seq = _write_to_wal(
        event_type="POOL_CB_REJECTION",
        source="PoolCircuitBreaker",
        details=details,
        success=False,
        error_message="pool_exhaustion_protection",
        target_id=pool_name,
    )

    # === Step 2: 버퍼 또는 직접 로깅 ===
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType

            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.POOL_CB_REJECTION,
                source="PoolCircuitBreaker",
                details=details,
                success=False,
                error_message="pool_exhaustion_protection",
                target_id=pool_name,
            )
            if added:
                return wal_seq
        except ImportError:
            pass

    # Fallback: 직접 로깅
    logger.warning(
        f"[PoolCBAudit] REJECTION | pool={pool_name} | "
        f"utilization={current_utilization:.2%} | threshold={threshold:.2%}"
    )
    return wal_seq


def log_cb_state_change_with_trace_audit(
    cb_name: str,
    old_state: str,
    new_state: str,
    trigger: str,
    reason: str | None = None,
    trace_id: str | None = None,
    triggering_request_info: dict[str, Any] | None = None,
    request: Any = None,
) -> int | None:
    """
    Circuit Breaker 상태 변경을 trace 정보와 함께 Audit 로그에 기록.

    운영자가 "서킷이 왜 열렸지?"라고 물었을 때, 로그의 trace_id 하나로
    전체 서비스 호출 흐름을 1초 만에 시각화할 수 있습니다.

    WAL 기반 누락 0 보장.
    """
    details = {
        "cb_name": cb_name,
        "old_state": old_state,
        "new_state": new_state,
        "trigger": trigger,
        "reason": reason,
        "trace_id": trace_id,
        "triggering_request": triggering_request_info,
        "debug_hint": (
            "위 trace_id로 Jaeger/Zipkin에서 전체 호출 흐름 확인 가능"
            if trace_id
            else None
        ),
    }
    # None 값 제거
    details = {k: v for k, v in details.items() if v is not None}

    # === Step 1: WAL에 먼저 기록 ===
    wal_seq = _write_to_wal(
        event_type="CB_STATE_CHANGE_WITH_TRACE",
        source="CircuitBreakerTracing",
        details=details,
        success=True,
        target_id=cb_name,
    )

    # === Step 2: 버퍼 또는 직접 로깅 ===
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType

            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.CB_STATE_CHANGE,
                source="CircuitBreakerTracing",
                details=details,
                success=True,
                target_id=cb_name,
            )
            if added:
                return wal_seq
        except ImportError:
            pass

    # Fallback: 직접 로깅
    trace_info = f" | trace_id={trace_id}" if trace_id else ""
    logger.info(
        f"[CBAudit] STATE_CHANGE_WITH_TRACE | cb={cb_name} | "
        f"{old_state} -> {new_state} | trigger={trigger}{trace_info}"
    )
    return wal_seq


def log_governance_blocked_cb_audit(
    service_id: str,
    action: str,
    block_reason: str,
    blast_radius_level: str | None = None,
    affected_services: list | None = None,
    assessment_id: str | None = None,
    cascading_risk: bool = False,
    trace_id: str | None = None,
    requires_manual_approval: bool = False,
    request: Any = None,
) -> int | None:
    """
    Circuit Breaker 자동 OPEN이 Blast Radius CRITICAL로 차단됨을 Audit 로그에 기록.

    CB가 자동 OPEN되기 전에 연쇄 장애 영향을 분석하여,
    CRITICAL 수준이면 OPEN을 보류합니다.

    WAL 기반 누락 0 보장.
    """
    affected_count = len(affected_services) if affected_services else 0

    details = {
        "action": action,
        "blocked_reason": block_reason,
        "blast_radius_level": blast_radius_level,
        "affected_services": affected_services,
        "affected_count": affected_count,
        "cascading_risk": cascading_risk,
        "assessment_id": assessment_id,
        "trace_id": trace_id,
        "message": (
            (
                f"CB가 열려야 했으나, 연쇄 장애 위험(Blast Radius: {blast_radius_level})으로 "
                f"인해 시스템이 차단을 보류함"
            )
            if blast_radius_level
            else None
        ),
    }
    # None 값 제거
    details = {k: v for k, v in details.items() if v is not None}

    # === Step 1: WAL에 먼저 기록 ===
    wal_seq = _write_to_wal(
        event_type="GOVERNANCE_BLOCKED",
        source="CircuitBreakerBlastRadius",
        details=details,
        success=False,
        error_message=block_reason,
        target_id=service_id,
    )

    # === Step 2: 버퍼 또는 직접 로깅 ===
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType

            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.GOVERNANCE_BLOCKED,
                source="CircuitBreakerBlastRadius",
                details=details,
                success=False,
                error_message=block_reason,
                target_id=service_id,
            )
            if added:
                return wal_seq
        except ImportError:
            pass

    # Fallback: 직접 로깅
    logger.warning(
        f"[CBAudit] GOVERNANCE_BLOCKED | cb={service_id} | "
        f"action={action} | blast_radius={blast_radius_level} | "
        f"affected={affected_count} | requires_approval={requires_manual_approval}"
    )
    return wal_seq
