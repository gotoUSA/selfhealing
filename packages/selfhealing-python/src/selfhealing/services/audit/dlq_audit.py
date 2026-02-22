"""
DLQ Audit Helpers - DLQ 및 Replay 관련 Audit 로깅

Usage:
    from selfhealing.services.audit.dlq_audit import log_dlq_store_audit, log_dlq_replay_audit

    log_dlq_store_audit(dlq_id=123, domain="payment", failure_type="PG_TIMEOUT", request=request)
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


def log_dlq_store_audit(
    dlq_id: int,
    domain: str,
    failure_type: str,
    error_message: str | None = None,
    request: Any = None,
) -> int | None:
    """
    DLQ 저장을 Audit 로그에 기록.

    WAL 기반 누락 0 보장:
    1. WAL에 먼저 기록 (동기, 로컬 파일)
    2. 버퍼/adapter에 기록 시도 (Best Effort)

    Args:
        dlq_id: 생성된 DLQ 엔트리 ID
        domain: 비즈니스 도메인 (payment, point 등)
        failure_type: 실패 유형 (PG_TIMEOUT 등)
        error_message: 원본 에러 메시지
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)

    Returns:
        WAL 시퀀스 번호 (WAL 기록 성공 시), None (실패 시)
    """
    details = {
        "dlq_id": dlq_id,
        "failure_type": failure_type,
        "error_message": error_message,
    }

    # === Step 1: WAL에 먼저 기록 (누락 0 보장) ===
    wal_seq = _write_to_wal(
        event_type="DLQ_STORE",
        source="DLQService",
        details=details,
        success=True,
        domain=domain,
        target_id=str(dlq_id),
    )

    # === Step 2: 하이브리드 로직 - request 있으면 버퍼에 적재 ===
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType

            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.DLQ_STORE,
                source="DLQService",
                details=details,
                success=True,
                domain=domain,
                target_id=str(dlq_id),
            )
            if added:
                return wal_seq  # 버퍼에 추가됨 - AuditMiddleware에서 기록
        except ImportError:
            pass  # event_buffer 사용 불가 - fallback to direct logging

    # === Step 3: request 없거나 버퍼 실패 시 직접 기록 ===
    adapter = _get_audit_adapter()

    if adapter is None:
        # Audit adapter not configured - log to standard logger
        logger.info(
            f"[DLQAudit] STORE | id={dlq_id} | domain={domain} | "
            f"failure_type={failure_type}"
        )
        return wal_seq

    try:
        adapter.log_dlq_store(
            dlq_id=dlq_id,
            domain=domain,
            failure_type=failure_type,
            error_message=error_message,
        )
    except Exception as e:
        # Audit logging should never break the main flow
        logger.warning(
            "dlq_audit.failed_log_store",
            error=e,
        )

    return wal_seq


def log_dlq_replay_audit(
    dlq_id: int,
    domain: str,
    success: bool,
    actor_id: str | None = None,
    error_message: str | None = None,
    request: Any = None,
) -> int | None:
    """
    DLQ 리플레이 결과를 Audit 로그에 기록.

    WAL 기반 누락 0 보장:
    1. WAL에 먼저 기록 (동기, 로컬 파일)
    2. 버퍼/adapter에 기록 시도 (Best Effort)

    Args:
        dlq_id: DLQ 엔트리 ID
        domain: 비즈니스 도메인
        success: 리플레이 성공 여부
        actor_id: 리플레이 실행자 (None이면 system)
        error_message: 실패 시 에러 메시지
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)

    Returns:
        WAL 시퀀스 번호 (WAL 기록 성공 시), None (실패 시)
    """
    details = {
        "dlq_id": dlq_id,
        "actor_id": actor_id,
        "error_message": error_message,
    }

    # === Step 1: WAL에 먼저 기록 (누락 0 보장) ===
    wal_seq = _write_to_wal(
        event_type="DLQ_REPLAY",
        source="ReplayService",
        details=details,
        success=success,
        error_message=error_message if not success else None,
        domain=domain,
        target_id=str(dlq_id),
    )

    # === Step 2: 하이브리드 로직 - request 있으면 버퍼에 적재 ===
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType

            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.DLQ_REPLAY,
                source="ReplayService",
                details=details,
                success=success,
                error_message=error_message if not success else None,
                domain=domain,
                target_id=str(dlq_id),
            )
            if added:
                return wal_seq  # 버퍼에 추가됨 - AuditMiddleware에서 기록
        except ImportError:
            pass  # event_buffer 사용 불가 - fallback to direct logging

    # === Step 3: request 없거나 버퍼 실패 시 직접 기록 ===
    adapter = _get_audit_adapter()

    if adapter is None:
        # Audit adapter not configured - log to standard logger
        status = "SUCCESS" if success else "FAILED"
        logger.info(
            f"[DLQAudit] REPLAY_{status} | id={dlq_id} | domain={domain} | "
            f"error={error_message or 'none'}"
        )
        return wal_seq

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
        logger.warning(
            "dlq_audit.failed_log_replay",
            error=e,
        )

    return wal_seq
