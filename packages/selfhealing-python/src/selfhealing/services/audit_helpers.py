"""
Audit Helpers - DLQ 및 Replay Audit 로깅 헬퍼 함수

DLQ 저장/리플레이 시 Audit 로그를 기록하기 위한 헬퍼 함수들.

하이브리드 로직 (56_AUDIT_MIDDLEWARE_DESIGN.md):
- request 객체가 있으면 → RequestAuditBuffer에 적재 (AuditMiddleware에서 일괄 기록)
- request 객체가 없으면 → 직접 adapter 호출 (Celery 등 비동기 컨텍스트)

이를 통해 미들웨어 컨텍스트에서는 단일 해시 체인으로 기록되고,
Celery 등에서는 기존 방식대로 즉시 기록됩니다.

Usage:
    from selfhealing.services.audit_helpers import log_dlq_store_audit, log_dlq_replay_audit
    
    # HTTP 요청 컨텍스트 (request 전달 시 버퍼에 적재)
    log_dlq_store_audit(
        dlq_id=123, domain="payment", failure_type="PG_TIMEOUT",
        request=request  # AuditMiddleware에서 일괄 기록
    )
    
    # Celery 등 비동기 컨텍스트 (request 없음 - 직접 기록)
    log_dlq_store_audit(dlq_id=123, domain="payment", failure_type="PG_TIMEOUT")
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _get_audit_adapter():
    """Get audit adapter from ProviderRegistry if available."""
    try:
        from selfhealing.factory import ProviderRegistry
        return ProviderRegistry.get_audit_adapter()
    except (ImportError, ValueError, AttributeError):
        return None


def _try_add_to_buffer(
    request: Any,
    event_type: "AuditEventType",
    source: str,
    details: dict,
    success: bool = True,
    error_message: Optional[str] = None,
    domain: Optional[str] = None,
    target_id: Optional[str] = None,
) -> bool:
    """
    request의 버퍼에 이벤트 추가 시도.
    
    Returns:
        True: 버퍼에 추가 성공 (AuditMiddleware에서 기록됨)
        False: request 없거나 버퍼 추가 실패 (직접 기록 필요)
    """
    if request is None:
        return False
    
    try:
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        buffer = RequestAuditBuffer.get_or_create(request)
        buffer.add(
            event_type=event_type,
            source=source,
            details=details,
            success=success,
            error_message=error_message,
            domain=domain,
            target_id=target_id,
        )
        return True
    except Exception as e:
        logger.debug(f"[AuditHelpers] Buffer add failed: {e}")
        return False


def log_dlq_store_audit(
    dlq_id: int,
    domain: str,
    failure_type: str,
    error_message: Optional[str] = None,
    request: Any = None,
) -> None:
    """
    DLQ 저장을 Audit 로그에 기록.
    
    Args:
        dlq_id: 생성된 DLQ 엔트리 ID
        domain: 비즈니스 도메인 (payment, point 등)
        failure_type: 실패 유형 (PG_TIMEOUT 등)
        error_message: 원본 에러 메시지
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)
    """
    # === 하이브리드 로직: request 있으면 버퍼에 적재 ===
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType
            
            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.DLQ_STORE,
                source="DLQService",
                details={
                    "dlq_id": dlq_id,
                    "failure_type": failure_type,
                    "error_message": error_message,
                },
                success=True,
                domain=domain,
                target_id=str(dlq_id),
            )
            if added:
                return  # 버퍼에 추가됨 - AuditMiddleware에서 기록
        except ImportError:
            pass  # event_buffer 사용 불가 - fallback to direct logging
    
    # === request 없거나 버퍼 실패 시 직접 기록 ===
    adapter = _get_audit_adapter()
    
    if adapter is None:
        # Audit adapter not configured - log to standard logger
        logger.info(
            f"[DLQAudit] STORE | id={dlq_id} | domain={domain} | "
            f"failure_type={failure_type}"
        )
        return
    
    try:
        adapter.log_dlq_store(
            dlq_id=dlq_id,
            domain=domain,
            failure_type=failure_type,
            error_message=error_message,
        )
    except Exception as e:
        # Audit logging should never break the main flow
        logger.warning(f"[DLQAudit] Failed to log store: {e}")


def log_dlq_replay_audit(
    dlq_id: int,
    domain: str,
    success: bool,
    actor_id: Optional[str] = None,
    error_message: Optional[str] = None,
    request: Any = None,
) -> None:
    """
    DLQ 리플레이 결과를 Audit 로그에 기록.
    
    Args:
        dlq_id: DLQ 엔트리 ID
        domain: 비즈니스 도메인
        success: 리플레이 성공 여부
        actor_id: 리플레이 실행자 (None이면 system)
        error_message: 실패 시 에러 메시지
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)
    """
    # === 하이브리드 로직: request 있으면 버퍼에 적재 ===
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType
            
            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.DLQ_REPLAY,
                source="ReplayService",
                details={
                    "dlq_id": dlq_id,
                    "actor_id": actor_id,
                    "error_message": error_message,
                },
                success=success,
                error_message=error_message if not success else None,
                domain=domain,
                target_id=str(dlq_id),
            )
            if added:
                return  # 버퍼에 추가됨 - AuditMiddleware에서 기록
        except ImportError:
            pass  # event_buffer 사용 불가 - fallback to direct logging
    
    # === request 없거나 버퍼 실패 시 직접 기록 ===
    adapter = _get_audit_adapter()
    
    if adapter is None:
        # Audit adapter not configured - log to standard logger
        status = "SUCCESS" if success else "FAILED"
        logger.info(
            f"[DLQAudit] REPLAY_{status} | id={dlq_id} | domain={domain} | "
            f"error={error_message or 'none'}"
        )
        return
    
    try:
        adapter.log_dlq_replay(
            dlq_id=dlq_id,
            domain=domain,
            success=success,
            actor_id=actor_id,
            error_message=error_message,
        )
    except Exception as e:
        # Audit logging should never break the main flow
        logger.warning(f"[DLQAudit] Failed to log replay: {e}")


# =============================================================================
# Circuit Breaker Audit Helpers
# =============================================================================

def log_cb_state_change_audit(
    cb_name: str,
    old_state: str,
    new_state: str,
    reason: Optional[str] = None,
    request: Any = None,
) -> None:
    """
    Circuit Breaker 상태 변경을 Audit 로그에 기록.
    
    Args:
        cb_name: Circuit Breaker 이름
        old_state: 이전 상태 (closed, open, half_open)
        new_state: 새 상태
        reason: 상태 변경 사유
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)
    """
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType
            
            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.CB_STATE_CHANGE,
                source="CircuitBreaker",
                details={
                    "cb_name": cb_name,
                    "old_state": old_state,
                    "new_state": new_state,
                },
                success=True,
                target_id=cb_name,
            )
            if added:
                return
        except ImportError:
            pass
    
    # Fallback: 직접 로깅
    logger.info(
        f"[CBAudit] STATE_CHANGE | cb={cb_name} | "
        f"{old_state} -> {new_state} | reason={reason or 'auto'}"
    )


def log_governance_blocked_audit(
    action: str,
    block_reason: str,
    details: Optional[dict] = None,
    request: Any = None,
) -> None:
    """
    Governance 차단을 Audit 로그에 기록.
    
    Args:
        action: 차단된 액션 (e.g., "auto_replay")
        block_reason: 차단 사유 (e.g., "kill_switch_active")
        details: 추가 상세 정보
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)
    """
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType
            
            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.GOVERNANCE_BLOCKED,
                source="GovernanceGuard",
                details={
                    "action": action,
                    "block_reason": block_reason,
                    **(details or {}),
                },
                success=False,
                error_message=block_reason,
            )
            if added:
                return
        except ImportError:
            pass
    
    # Fallback: 직접 로깅
    logger.warning(
        f"[GovernanceAudit] BLOCKED | action={action} | reason={block_reason}"
    )


def log_rate_limited_audit(
    client_ip: str,
    endpoint: str,
    limit_type: str,
    request: Any = None,
) -> None:
    """
    Rate Limit 차단을 Audit 로그에 기록.
    
    Args:
        client_ip: 클라이언트 IP
        endpoint: 차단된 엔드포인트
        limit_type: 제한 유형 (global, endpoint 등)
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)
    """
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType
            
            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.RATE_LIMITED,
                source="RateLimiter",
                details={
                    "client_ip": client_ip,
                    "endpoint": endpoint,
                    "limit_type": limit_type,
                },
                success=False,
                error_message="rate_limited",
            )
            if added:
                return
        except ImportError:
            pass
    
    # Fallback: 직접 로깅
    logger.info(
        f"[RateLimitAudit] BLOCKED | ip={client_ip} | "
        f"endpoint={endpoint} | type={limit_type}"
    )


def log_pool_cb_rejection_audit(
    pool_name: str,
    current_utilization: float,
    threshold: float,
    decision_source: str = "cached_pool_status",
    request: Any = None,
) -> None:
    """
    Pool Circuit Breaker 거부를 Audit 로그에 기록.
    
    Args:
        pool_name: 커넥션 풀 이름
        current_utilization: 현재 사용률 (0.0 ~ 1.0)
        threshold: 거부 임계값
        decision_source: 결정 소스 (cached_pool_status, live_check 등)
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)
    """
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType
            
            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.POOL_CB_REJECTION,
                source="PoolCircuitBreaker",
                details={
                    "pool_name": pool_name,
                    "current_utilization": current_utilization,
                    "threshold": threshold,
                    "decision_source": decision_source,
                },
                success=False,
                error_message="pool_exhaustion_protection",
                target_id=pool_name,
            )
            if added:
                return
        except ImportError:
            pass
    
    # Fallback: 직접 로깅
    logger.warning(
        f"[PoolCBAudit] REJECTION | pool={pool_name} | "
        f"utilization={current_utilization:.2%} | threshold={threshold:.2%}"
    )

