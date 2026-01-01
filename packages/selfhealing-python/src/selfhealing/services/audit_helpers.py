"""
Audit Helpers - DLQ 및 Replay Audit 로깅 헬퍼 함수

DLQ 저장/리플레이 시 Audit 로그를 기록하기 위한 헬퍼 함수들.
AuditLogAdapter가 등록되지 않은 경우 표준 로거로 fallback.

Usage:
    from selfhealing.services.audit_helpers import log_dlq_store_audit, log_dlq_replay_audit
    
    # DLQ 저장 시
    log_dlq_store_audit(dlq_id=123, domain="payment", failure_type="PG_TIMEOUT")
    
    # DLQ 리플레이 시
    log_dlq_replay_audit(dlq_id=123, domain="payment", success=True)
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _get_audit_adapter():
    """Get audit adapter from ProviderRegistry if available."""
    try:
        from selfhealing.factory import ProviderRegistry
        return ProviderRegistry.get_audit_adapter()
    except (ImportError, ValueError, AttributeError):
        return None


def log_dlq_store_audit(
    dlq_id: int,
    domain: str,
    failure_type: str,
    error_message: Optional[str] = None,
) -> None:
    """
    DLQ 저장을 Audit 로그에 기록.
    
    Args:
        dlq_id: 생성된 DLQ 엔트리 ID
        domain: 비즈니스 도메인 (payment, point 등)
        failure_type: 실패 유형 (PG_TIMEOUT 등)
        error_message: 원본 에러 메시지
    """
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
) -> None:
    """
    DLQ 리플레이 결과를 Audit 로그에 기록.
    
    Args:
        dlq_id: DLQ 엔트리 ID
        domain: 비즈니스 도메인
        success: 리플레이 성공 여부
        actor_id: 리플레이 실행자 (None이면 system)
        error_message: 실패 시 에러 메시지
    """
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
