"""
Retry & System Control Audit Helpers

재시도, 시스템 제어, 롤백 관련 Audit 로깅.

Usage:
    from selfhealing.services.audit.retry_audit import (
        log_retry_audit,
        log_system_control_audit,
        log_rollback_audit,
    )
"""

from __future__ import annotations

from typing import Any

import structlog

from selfhealing.services.audit.base import _try_add_to_buffer, _write_to_wal

logger = structlog.get_logger()


def log_retry_audit(
    domain: str,
    attempt: int,
    max_attempts: int,
    success: bool,
    error_type: str | None = None,
    error_message: str | None = None,
    wait_time: float | None = None,
    rate_limited: bool = False,
    context: dict[str, Any] | None = None,
    request: Any = None,
) -> int | None:
    """
    재시도 이벤트를 Audit 로그에 기록.

    WAL 기반 누락 0 보장.

    Args:
        domain: 비즈니스 도메인 (payment, point 등)
        attempt: 현재 시도 횟수
        max_attempts: 최대 시도 횟수
        success: 재시도 성공 여부
        error_type: 에러 유형 (실패 시)
        error_message: 에러 메시지 (실패 시)
        wait_time: 대기 시간 (초)
        rate_limited: 레이트 리밋으로 인한 대기 여부
        context: 추가 컨텍스트 (order_id, payment_id 등)
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)

    Returns:
        WAL 시퀀스 번호 (WAL 기록 성공 시), None (실패 시)
    """
    # 이벤트 타입 결정: 마지막 시도 실패면 EXHAUSTED, 아니면 ATTEMPTED
    is_exhausted = not success and attempt >= max_attempts
    event_type_str = "RETRY_EXHAUSTED" if is_exhausted else "RETRY_ATTEMPTED"

    details = {
        "domain": domain,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "error_type": error_type,
        "error_message": error_message,
        "wait_time": wait_time,
        "rate_limited": rate_limited,
        **(context or {}),
    }

    # === Step 1: WAL에 먼저 기록 (누락 0 보장) ===
    wal_seq = _write_to_wal(
        event_type=event_type_str,
        source="RetryHandler",
        details=details,
        success=success,
        error_message=error_message if not success else None,
        domain=domain,
    )

    # === Step 2: 하이브리드 로직 - request 있으면 버퍼에 적재 ===
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType

            audit_event_type = AuditEventType.RETRY_EXHAUSTED if is_exhausted else AuditEventType.RETRY_ATTEMPTED

            added = _try_add_to_buffer(
                request=request,
                event_type=audit_event_type,
                source="RetryHandler",
                details=details,
                success=success,
                error_message=error_message if not success else None,
                domain=domain,
            )
            if added:
                return wal_seq
        except ImportError:
            pass

    # === Step 3: request 없거나 버퍼 실패 시 직접 기록 ===
    status = "SUCCESS" if success else ("EXHAUSTED" if is_exhausted else "RETRY")
    logger.info(
        "retry_audit.event",
        retry_status=status,
        healing_domain=domain,
        attempt=attempt,
        max_attempts=max_attempts,
        error_type_name=error_type or "none",
    )
    return wal_seq


def log_system_control_audit(
    action: str,
    actor: str,
    old_state: dict[str, Any] | None = None,
    new_state: dict[str, Any] | None = None,
    reason: str | None = None,
    request: Any = None,
) -> int | None:
    """
    시스템 제어 변경을 Audit 로그에 기록.

    Kill Switch 활성화/비활성화, Dry Run 모드 변경 등을 기록합니다.
    WAL 기반 누락 0 보장.

    Args:
        action: 수행된 액션 (enable, disable, enable_dry_run, disable_dry_run, reset)
        actor: 액션 수행자 (admin, system 등)
        old_state: 변경 전 상태 (enabled, dry_run 등)
        new_state: 변경 후 상태
        reason: 변경 사유
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)

    Returns:
        WAL 시퀀스 번호 (WAL 기록 성공 시), None (실패 시)
    """
    details = {
        "action": action,
        "actor": actor,
        "old_state": old_state,
        "new_state": new_state,
        "reason": reason,
    }

    # === Step 1: WAL에 먼저 기록 (누락 0 보장) ===
    wal_seq = _write_to_wal(
        event_type="SYSTEM_CONTROL_CHANGED",
        source="SystemControl",
        details=details,
        success=True,
    )

    # === Step 2: 하이브리드 로직 - request 있으면 버퍼에 적재 ===
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType

            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.SYSTEM_CONTROL_CHANGED,
                source="SystemControl",
                details=details,
                success=True,
            )
            if added:
                return wal_seq
        except ImportError:
            pass

    # === Step 3: request 없거나 버퍼 실패 시 직접 기록 ===
    logger.info(
        "system_control_audit.event",
        control_action=action.upper(),
        actor_id=actor,
        control_reason=reason or "N/A",
    )
    return wal_seq


def log_rollback_audit(
    request_id: str,
    stage_name: str,
    state: str,
    triggered_by: str,
    reason: str | None = None,
    source_version: str | None = None,
    target_version: str | None = None,
    affected_components: list | None = None,
    errors: list | None = None,
    duration_seconds: float | None = None,
    request: Any = None,
) -> int | None:
    """
    롤백 이벤트를 Audit 로그에 기록.

    롤백 요청, 실행 시작, 완료, 실패 등을 기록합니다.
    WAL 기반 누락 0 보장.

    Args:
        request_id: 롤백 요청 ID
        stage_name: 대상 Stage 이름
        state: 롤백 상태 (pending, in_progress, completed, failed, cancelled)
        triggered_by: 트리거 주체 (system, admin 등)
        reason: 롤백 사유
        source_version: 원본 버전
        target_version: 롤백 대상 버전
        affected_components: 영향받은 컴포넌트 목록
        errors: 발생한 에러 목록
        duration_seconds: 롤백 소요 시간
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)

    Returns:
        WAL 시퀀스 번호 (WAL 기록 성공 시), None (실패 시)
    """
    success = state in ("completed", "pending", "in_progress")

    details = {
        "request_id": request_id,
        "stage_name": stage_name,
        "state": state,
        "triggered_by": triggered_by,
        "reason": reason,
        "source_version": source_version,
        "target_version": target_version,
        "affected_components": affected_components,
        "errors": errors,
        "duration_seconds": duration_seconds,
    }

    # === Step 1: WAL에 먼저 기록 (누락 0 보장) ===
    error_msg = "; ".join(errors) if errors else None
    wal_seq = _write_to_wal(
        event_type="ROLLBACK_PERFORMED",
        source="RollbackService",
        details=details,
        success=success,
        error_message=error_msg,
        target_id=request_id,
    )

    # === Step 2: 하이브리드 로직 - request 있으면 버퍼에 적재 ===
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType

            added = _try_add_to_buffer(
                request=request,
                event_type=AuditEventType.ROLLBACK_PERFORMED,
                source="RollbackService",
                details=details,
                success=success,
                error_message=error_msg,
                target_id=request_id,
            )
            if added:
                return wal_seq
        except ImportError:
            pass

    # === Step 3: request 없거나 버퍼 실패 시 직접 기록 ===
    logger.info(
        "rollback_audit.event",
        rollback_state=state.upper(),
        request_id=request_id,
        stage_name=stage_name,
        triggered_by=triggered_by,
        duration_seconds=duration_seconds or 0,
    )
    return wal_seq
