"""
Audit Helpers - DLQ 및 Replay Audit 로깅 헬퍼 함수

DLQ 저장/리플레이 시 Audit 로그를 기록하기 위한 헬퍼 함수들.

하이브리드 로직 (56_AUDIT_MIDDLEWARE_DESIGN.md):
- request 객체가 있으면 → RequestAuditBuffer에 적재 (AuditMiddleware에서 일괄 기록)
- request 객체가 없으면 → 직접 adapter 호출 (Celery 등 비동기 컨텍스트)

WAL 기반 누락 0 보장 (20_AUDIT_UNIFICATION_PLAN.md ADR-005):
- 모든 audit 이벤트는 WAL에 먼저 기록 (로컬 파일, 거의 실패 안함)
- 이후 중앙 저장소에 기록 시도 (Best Effort)
- Background Sync Worker가 WAL → 중앙 저장소 동기화
- Reconciler가 주기적으로 누락 감지 및 재전송

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
import os
import time
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# WAL Singleton for Audit Helpers
# =============================================================================

_wal_instance = None
_wal_enabled = True  # 환경변수로 비활성화 가능


def _get_wal():
    """Get WAL singleton instance for audit helpers."""
    global _wal_instance, _wal_enabled
    
    if not _wal_enabled:
        return None
    
    if _wal_instance is not None:
        return _wal_instance
    
    try:
        from selfhealing.audit.wal import WriteAheadLog, WALConfig
        
        # WAL 디렉토리 설정 (환경변수 또는 기본값)
        wal_dir = os.environ.get("AUDIT_WAL_DIR", "/var/log/audit/wal")
        
        # Group Commit 설정으로 I/O 최적화
        config = WALConfig(
            wal_dir=wal_dir,
            max_file_size_mb=int(os.environ.get("AUDIT_WAL_MAX_FILE_SIZE_MB", 100)),
            sync_on_write=os.environ.get("AUDIT_WAL_SYNC_ON_WRITE", "true").lower() == "true",
            max_files=int(os.environ.get("AUDIT_WAL_MAX_FILES", 10)),
            file_prefix="audit_helpers_wal",
            group_commit_enabled=os.environ.get("AUDIT_WAL_GROUP_COMMIT", "false").lower() == "true",
            group_commit_max_entries=int(os.environ.get("AUDIT_WAL_GROUP_COMMIT_MAX_ENTRIES", 100)),
            group_commit_max_wait_ms=int(os.environ.get("AUDIT_WAL_GROUP_COMMIT_MAX_WAIT_MS", 10)),
        )
        
        _wal_instance = WriteAheadLog(config=config)
        logger.info(f"[AuditHelpers] WAL initialized at {wal_dir}")
        return _wal_instance
    except Exception as e:
        logger.warning(f"[AuditHelpers] WAL initialization failed: {e}")
        _wal_enabled = False  # 실패 시 비활성화
        return None


def _write_to_wal(
    event_type: str,
    source: str,
    details: Dict[str, Any],
    success: bool = True,
    error_message: Optional[str] = None,
    domain: Optional[str] = None,
    target_id: Optional[str] = None,
) -> Optional[int]:
    """
    WAL에 audit 이벤트 기록.
    
    Returns:
        WAL 시퀀스 번호 (성공 시), None (실패 시)
    """
    wal = _get_wal()
    if wal is None:
        return None
    
    try:
        from selfhealing.audit.resilience import AuditMetrics
        metrics = AuditMetrics.get_instance()
    except Exception:
        metrics = None
    
    try:
        record_id = f"audit-{uuid.uuid4().hex[:12]}"
        wal_entry = {
            "record_id": record_id,
            "event_type": event_type,
            "source": source,
            "details": details,
            "success": success,
            "error_message": error_message,
            "domain": domain,
            "target_id": target_id,
            "timestamp": time.time(),
            "synced": False,  # Background Sync Worker가 처리 후 True로 변경
        }
        
        seq = wal.write(wal_entry)
        
        if metrics:
            metrics.record_write("wal", success=True)
        
        logger.debug(f"[AuditHelpers] WAL write success: seq={seq}, event={event_type}")
        return seq
    except Exception as e:
        logger.error(f"[AuditHelpers] WAL write failed (CRITICAL): {e}")
        if metrics:
            metrics.record_write("wal", success=False)
            metrics.record_failure("wal", type(e).__name__)
        return None


def disable_wal():
    """WAL 비활성화 (테스트용)."""
    global _wal_enabled, _wal_instance
    _wal_enabled = False
    if _wal_instance:
        try:
            _wal_instance.close()
        except Exception:
            pass
    _wal_instance = None


def enable_wal():
    """WAL 활성화."""
    global _wal_enabled
    _wal_enabled = True


def get_wal_stats() -> Optional[Dict[str, Any]]:
    """WAL 통계 조회."""
    wal = _get_wal()
    if wal is None:
        return None
    
    try:
        stats = wal.get_stats()
        return {
            "state": stats.state.value,
            "current_file": stats.current_file,
            "current_size_bytes": stats.current_size_bytes,
            "total_entries": stats.total_entries,
            "total_files": stats.total_files,
            "last_sequence": stats.last_sequence,
            "last_write_time": stats.last_write_time,
            "corrupted_entries": stats.corrupted_entries,
            "recovered_entries": stats.recovered_entries,
        }
    except Exception as e:
        logger.warning(f"[AuditHelpers] Failed to get WAL stats: {e}")
        return None


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
) -> Optional[int]:
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
        logger.warning(f"[DLQAudit] Failed to log store: {e}")
    
    return wal_seq


def log_dlq_replay_audit(
    dlq_id: int,
    domain: str,
    success: bool,
    actor_id: Optional[str] = None,
    error_message: Optional[str] = None,
    request: Any = None,
) -> Optional[int]:
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
        logger.warning(f"[DLQAudit] Failed to log replay: {e}")
    
    return wal_seq


# =============================================================================
# Circuit Breaker Audit Helpers
# =============================================================================

def log_cb_state_change_audit(
    cb_name: str,
    old_state: str,
    new_state: str,
    reason: Optional[str] = None,
    request: Any = None,
) -> Optional[int]:
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
    details: Optional[dict] = None,
    request: Any = None,
) -> Optional[int]:
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
) -> Optional[int]:
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
) -> Optional[int]:
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


# =============================================================================
# Retry Audit Helpers (Phase 1: 20_AUDIT_UNIFICATION_PLAN.md)
# =============================================================================


def log_retry_audit(
    domain: str,
    attempt: int,
    max_attempts: int,
    success: bool,
    error_type: Optional[str] = None,
    error_message: Optional[str] = None,
    wait_time: Optional[float] = None,
    rate_limited: bool = False,
    context: Optional[Dict[str, Any]] = None,
    request: Any = None,
) -> Optional[int]:
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
            
            audit_event_type = (
                AuditEventType.RETRY_EXHAUSTED if is_exhausted 
                else AuditEventType.RETRY_ATTEMPTED
            )
            
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
        f"[RetryAudit] {status} | domain={domain} | "
        f"attempt={attempt}/{max_attempts} | error={error_type or 'none'}"
    )
    return wal_seq


# =============================================================================
# System Control Audit Helpers (Phase 1: 20_AUDIT_UNIFICATION_PLAN.md)
# =============================================================================


def log_system_control_audit(
    action: str,
    actor: str,
    old_state: Optional[Dict[str, Any]] = None,
    new_state: Optional[Dict[str, Any]] = None,
    reason: Optional[str] = None,
    request: Any = None,
) -> Optional[int]:
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
        f"[SystemControlAudit] {action.upper()} | actor={actor} | reason={reason or 'N/A'}"
    )
    return wal_seq


# =============================================================================
# Rollback Audit Helpers (Phase 1: 20_AUDIT_UNIFICATION_PLAN.md)
# =============================================================================


def log_rollback_audit(
    request_id: str,
    stage_name: str,
    state: str,
    triggered_by: str,
    reason: Optional[str] = None,
    source_version: Optional[str] = None,
    target_version: Optional[str] = None,
    affected_components: Optional[list] = None,
    errors: Optional[list] = None,
    duration_seconds: Optional[float] = None,
    request: Any = None,
) -> Optional[int]:
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
        f"[RollbackAudit] {state.upper()} | request={request_id} | "
        f"stage={stage_name} | by={triggered_by} | "
        f"duration={duration_seconds or 0:.2f}s"
    )
    return wal_seq


# =============================================================================
# Chaos Experiment Audit Helpers (Phase 2: 20_AUDIT_UNIFICATION_PLAN.md)
# =============================================================================


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
    
    Args:
        experiment_id: 실험 ID
        event_type: 이벤트 유형 (experiment_started, experiment_completed, 
                    chaos_injection_started, rollback_started, rollback_completed,
                    kill_requested, auto_abort_ttl_expired, auto_abort_stop_condition 등)
        experiment_type: 실험 유형 (latency_injection, error_5xx 등)
        config: 실험 설정
        result: 실험 결과
        dry_run: Dry Run 모드 여부
        ttl_seconds: TTL 설정값
        expires_at: 만료 시간 (ISO format)
        violations: Stop Condition 위반 목록
        reason: 이벤트 사유
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)
        
    Returns:
        생성된 audit record ID
    """
    import uuid
    
    # record_id 생성 (기존 ChaosExperiment._audit과 호환)
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
    # None 값 제거
    details = {k: v for k, v in details.items() if v is not None}
    
    # AuditEventType 매핑
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
    
    # === Step 1: WAL에 먼저 기록 ===
    _write_to_wal(
        event_type=wal_event_type,
        source="ChaosExperiment",
        details=details,
        success=True,
        target_id=experiment_id,
    )
    
    # === Step 2: 버퍼 또는 직접 로깅 ===
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
    
    # Fallback: 직접 로깅 (기존 ChaosExperiment._audit과 동일한 형식)
    logger.info(
        f"[ChaosAudit] {experiment_id} | {event_type} | {record_id}",
        extra={"audit_data": details}
    )
    return record_id


# =============================================================================
# Emergency Mode Audit Helpers (Phase 2: 20_AUDIT_UNIFICATION_PLAN.md)
# =============================================================================


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
    
    Args:
        action: 수행된 액션 (activate, auto_activate, deactivate, escalate, de_escalate)
        level: Emergency 레벨 (NORMAL, LEVEL_1, LEVEL_2, LEVEL_3, LOCKDOWN)
        is_active: 비상 모드 활성화 여부
        activated_by: 활성화한 사용자 (활성화 시)
        deactivated_by: 비활성화한 사용자 (비활성화 시)
        reason: 변경 사유
        is_auto_triggered: 자동 트리거 여부
        expires_at: 만료 시간 (ISO format)
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)
        
    Returns:
        WAL 시퀀스 번호 (WAL 기록 성공 시), None (실패 시)
    """
    # 이벤트 타입 결정
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
    # None 값 제거
    details = {k: v for k, v in details.items() if v is not None}
    
    # === Step 1: WAL에 먼저 기록 ===
    wal_seq = _write_to_wal(
        event_type=event_type_str,
        source="EmergencyModeManager",
        details=details,
        success=True,
    )
    
    # === Step 2: 버퍼 또는 직접 로깅 ===
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
    
    # Fallback: 직접 로깅
    logger.info(
        f"[EmergencyModeAudit] {action.upper()} | level={level} | "
        f"user={user} | reason={reason or 'N/A'}"
    )
    
    # === 기존 log_config_change 호환 호출 ===
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


# =============================================================================
# Error Budget Gate Audit Helpers (Phase 2: 20_AUDIT_UNIFICATION_PLAN.md)
# =============================================================================


def log_error_budget_blocked_audit(
    action: str,
    gate_status: str,
    error_budget_percent: Optional[float] = None,
    threshold_percent: Optional[float] = None,
    reason: Optional[str] = None,
    request: Any = None,
) -> Optional[int]:
    """
    Error Budget Gate 차단을 Audit 로그에 기록.
    
    에러 예산 부족으로 인한 자동화 차단을 기록합니다.
    WAL 기반 누락 0 보장.
    
    Args:
        action: 차단된 액션 이름 (chaos_experiment, auto_replay 등)
        gate_status: 게이트 상태 (blocked, fail_open_rate_limited 등)
        error_budget_percent: 현재 에러 예산 잔여율
        threshold_percent: 차단 임계값
        reason: 차단 사유
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)
        
    Returns:
        WAL 시퀀스 번호 (WAL 기록 성공 시), None (실패 시)
    """
    details = {
        "action": action,
        "gate_status": gate_status,
        "error_budget_percent": error_budget_percent,
        "threshold_percent": threshold_percent,
        "reason": reason,
        "manual_mode_enforced": True,
    }
    # None 값 제거
    details = {k: v for k, v in details.items() if v is not None}
    
    # === Step 1: WAL에 먼저 기록 ===
    wal_seq = _write_to_wal(
        event_type="ERROR_BUDGET_BLOCKED",
        source="ErrorBudgetGate",
        details=details,
        success=False,
        error_message=reason,
        target_id=action,
    )
    
    # === Step 2: 버퍼 또는 직접 로깅 ===
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
    
    # Fallback: 직접 로깅
    budget_str = f"{error_budget_percent:.1f}%" if error_budget_percent is not None else "N/A"
    logger.warning(
        f"[ErrorBudgetAudit] BLOCKED | action={action} | "
        f"budget={budget_str} | status={gate_status}"
    )
    return wal_seq


# =============================================================================
# Compliance Audit Helpers (Phase 3: 20_AUDIT_UNIFICATION_PLAN.md)
# =============================================================================


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
    
    Args:
        stage_name: Stage 이름
        standard: 규정 표준 (DORA_2025, PCI_DSS, SOC2 등)
        check_id: 검사 ID
        passed: 검사 통과 여부
        violation_id: 위반 ID (위반 시)
        severity: 위반 심각도 (high, medium, low)
        message: 위반 메시지
        details: 추가 상세 정보
        compliance_score: 전체 규정 준수 점수
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)
        
    Returns:
        WAL 시퀀스 번호 (WAL 기록 성공 시), None (실패 시)
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
    # None 값 제거
    audit_details = {k: v for k, v in audit_details.items() if v is not None}
    
    event_type = "COMPLIANCE_CHECK_PASSED" if passed else "COMPLIANCE_VIOLATION"
    
    # === Step 1: WAL에 먼저 기록 ===
    wal_seq = _write_to_wal(
        event_type=event_type,
        source="ComplianceService",
        details=audit_details,
        success=passed,
        error_message=message if not passed else None,
        target_id=check_id or violation_id,
    )
    
    # === Step 2: 버퍼 또는 직접 로깅 ===
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
    
    # Fallback: 직접 로깅
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


# =============================================================================
# Blast Radius Audit Helpers (Phase 3: 20_AUDIT_UNIFICATION_PLAN.md)
# =============================================================================


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
    
    Args:
        experiment_id: 실험 ID
        blast_radius: 영향 범위 (instance, service, region)
        target_service: 대상 서비스
        action: 액션 (check, register, unregister, isolation, violation)
        allowed: 허용 여부
        violations: 위반 목록
        approval_status: 승인 상태
        target_domain: 대상 도메인
        traffic_percent: 트래픽 비율
        reason: 사유
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)
        
    Returns:
        WAL 시퀀스 번호 (WAL 기록 성공 시), None (실패 시)
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
    # None 값 제거
    details = {k: v for k, v in details.items() if v is not None}
    
    event_type = "BLAST_RADIUS_ISOLATION" if allowed else "BLAST_RADIUS_VIOLATION"
    
    # === Step 1: WAL에 먼저 기록 ===
    wal_seq = _write_to_wal(
        event_type=event_type,
        source="BlastRadiusManager",
        details=details,
        success=allowed,
        error_message="; ".join(violations) if violations else None,
        target_id=experiment_id,
    )
    
    # === Step 2: 버퍼 또는 직접 로깅 ===
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
    
    # Fallback: 직접 로깅
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


# =============================================================================
# FinOps Audit Helpers (Phase 3: 20_AUDIT_UNIFICATION_PLAN.md)
# =============================================================================


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
    
    Args:
        stage_name: Stage 이름
        alert_type: 알림 유형 (threshold, over_budget, cost_spike 등)
        current_cost: 현재 비용
        budget_limit: 예산 한도
        usage_percent: 사용률 (%)
        operation: 관련 작업
        severity: 심각도 (warning, critical)
        message: 알림 메시지
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)
        
    Returns:
        WAL 시퀀스 번호 (WAL 기록 성공 시), None (실패 시)
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
    # None 값 제거
    details = {k: v for k, v in details.items() if v is not None}
    
    event_type = (
        "FINOPS_BUDGET_EXCEEDED" if alert_type == "over_budget" 
        else "FINOPS_THRESHOLD_EXCEEDED"
    )
    is_critical = alert_type == "over_budget"
    
    # === Step 1: WAL에 먼저 기록 ===
    wal_seq = _write_to_wal(
        event_type=event_type,
        source="FinOpsService",
        details=details,
        success=not is_critical,
        error_message=message,
        target_id=stage_name,
    )
    
    # === Step 2: 버퍼 또는 직접 로깅 ===
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
    
    # Fallback: 직접 로깅
    cost_str = f"${current_cost:.4f}" if current_cost is not None else "N/A"
    limit_str = f"${budget_limit:.2f}" if budget_limit is not None else "N/A"
    log_func = logger.critical if is_critical else logger.warning
    log_func(
        f"[FinOpsAudit] {alert_type.upper()} | stage={stage_name} | "
        f"cost={cost_str} | limit={limit_str} | severity={severity}"
    )
    return wal_seq


# =============================================================================
# Data Access Audit Helpers (Phase 3: ADR-002 설정 기반 조회 기록)
# =============================================================================


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
    
    Args:
        path: 요청 경로
        method: HTTP 메서드 (GET, POST 등)
        actor_id: 접근자 ID
        resource_type: 리소스 유형 (user, payment, order 등)
        resource_id: 리소스 ID
        details: 추가 상세 정보
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)
        
    Returns:
        WAL 시퀀스 번호 (WAL 기록 성공 시), None (실패 시)
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
    # None 값 제거
    audit_details = {k: v for k, v in audit_details.items() if v is not None}
    
    # === Step 1: WAL에 먼저 기록 ===
    wal_seq = _write_to_wal(
        event_type="DATA_ACCESS",
        source="DataAccessAudit",
        details=audit_details,
        success=True,
        target_id=resource_id,
    )
    
    # === Step 2: 버퍼 또는 직접 로깅 ===
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
    
    # Fallback: 직접 로깅
    logger.info(
        f"[DataAccessAudit] {method} {path} | actor={actor_id} | "
        f"resource={resource_type}:{resource_id}"
    )
    return wal_seq


# =============================================================================
# Celery Task Audit Helpers (Phase 4: 20_AUDIT_UNIFICATION_PLAN.md)
# =============================================================================


def log_config_apply_audit(
    pending_id: Optional[str] = None,
    config_key: Optional[str] = None,
    old_value: Optional[Any] = None,
    new_value: Optional[Any] = None,
    status: str = "applied",
    error_message: Optional[str] = None,
    task_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """
    설정 적용(config_apply) Celery task 실행을 Audit 로그에 기록.
    
    Celery task는 HTTP 요청 컨텍스트가 없으므로 직접 WAL에 기록합니다.
    
    Args:
        pending_id: PendingConfig ID
        config_key: 설정 키 (e.g., "error_budget", "dlq")
        old_value: 변경 전 값
        new_value: 변경 후 값
        status: 적용 상태 (applied, blocked, failed)
        error_message: 에러 메시지 (실패 시)
        task_id: Celery task ID
        details: 추가 상세 정보
        
    Returns:
        WAL 시퀀스 번호 (WAL 기록 성공 시), None (실패 시)
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
    # None 값 제거
    audit_details = {k: v for k, v in audit_details.items() if v is not None}
    
    success = status in ("applied", "success")
    
    # === WAL에 직접 기록 (Celery 컨텍스트) ===
    wal_seq = _write_to_wal(
        event_type="CONFIG_CHANGE",
        source="ConfigApplyTask",
        details=audit_details,
        success=success,
        error_message=error_message,
        target_id=pending_id or config_key,
    )
    
    # === Adapter 통해 기록 시도 ===
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
            logger.debug(f"[ConfigApplyAudit] Adapter record failed: {e}")
    
    log_func = logger.info if success else logger.warning
    log_func(
        f"[ConfigApplyAudit] {status.upper()} | key={config_key} | "
        f"pending_id={pending_id} | task_id={task_id}"
    )
    return wal_seq


def log_chaos_scheduler_audit(
    experiment_id: Optional[str] = None,
    experiment_name: Optional[str] = None,
    action: str = "scheduled",
    status: str = "started",
    target_service: Optional[str] = None,
    error_message: Optional[str] = None,
    task_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """
    Chaos 스케줄러(chaos_scheduler) Celery task 실행을 Audit 로그에 기록.
    
    Celery task는 HTTP 요청 컨텍스트가 없으므로 직접 WAL에 기록합니다.
    
    Args:
        experiment_id: 실험 ID
        experiment_name: 실험 이름
        action: 동작 유형 (scheduled, executed, cleanup)
        status: 상태 (started, completed, failed, blocked)
        target_service: 대상 서비스
        error_message: 에러 메시지 (실패 시)
        task_id: Celery task ID
        details: 추가 상세 정보
        
    Returns:
        WAL 시퀀스 번호 (WAL 기록 성공 시), None (실패 시)
    """
    # 이벤트 타입 결정
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
    # None 값 제거
    audit_details = {k: v for k, v in audit_details.items() if v is not None}
    
    success = status not in ("failed", "blocked", "error")
    
    # === WAL에 직접 기록 (Celery 컨텍스트) ===
    wal_seq = _write_to_wal(
        event_type=event_type_str,
        source="ChaosSchedulerTask",
        details=audit_details,
        success=success,
        error_message=error_message,
        domain="chaos",
        target_id=experiment_id,
    )
    
    # === Adapter 통해 기록 시도 ===
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
                event_type=event_type_map.get(event_type_str, AuditEventType.CHAOS_EXPERIMENT_STARTED),
                source="ChaosSchedulerTask",
                details=audit_details,
                success=success,
                error_message=error_message,
                domain="chaos",
                target_id=experiment_id,
            )
        except Exception as e:
            logger.debug(f"[ChaosSchedulerAudit] Adapter record failed: {e}")
    
    log_func = logger.info if success else logger.warning
    log_func(
        f"[ChaosSchedulerAudit] {action.upper()} | status={status} | "
        f"experiment={experiment_name or experiment_id} | task_id={task_id}"
    )
    return wal_seq


def log_governance_task_audit(
    action: str = "expiry_check",
    emergency_level: Optional[int] = None,
    previous_level: Optional[int] = None,
    status: str = "completed",
    notification_sent: bool = False,
    auto_recovered: bool = False,
    hours_elapsed: Optional[float] = None,
    error_message: Optional[str] = None,
    task_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """
    Governance(emergency mode expiry) Celery task 실행을 Audit 로그에 기록.
    
    Celery task는 HTTP 요청 컨텍스트가 없으므로 직접 WAL에 기록합니다.
    
    Args:
        action: 동작 유형 (expiry_check, warning_sent, auto_recovered)
        emergency_level: 현재 Emergency 레벨
        previous_level: 이전 Emergency 레벨 (변경 시)
        status: 상태 (completed, no_action, warning, auto_recovered)
        notification_sent: 알림 발송 여부
        auto_recovered: 자동 복구 여부
        hours_elapsed: 경과 시간 (시간 단위)
        error_message: 에러 메시지 (실패 시)
        task_id: Celery task ID
        details: 추가 상세 정보
        
    Returns:
        WAL 시퀀스 번호 (WAL 기록 성공 시), None (실패 시)
    """
    # 이벤트 타입 결정
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
    # None 값 제거
    audit_details = {k: v for k, v in audit_details.items() if v is not None}
    
    success = status not in ("failed", "error")
    
    # === WAL에 직접 기록 (Celery 컨텍스트) ===
    wal_seq = _write_to_wal(
        event_type=event_type_str,
        source="GovernanceTask",
        details=audit_details,
        success=success,
        error_message=error_message,
        domain="governance",
    )
    
    # === Adapter 통해 기록 시도 ===
    adapter = _get_audit_adapter()
    if adapter is not None:
        try:
            from selfhealing.audit.event_buffer import AuditEventType
            
            event_type_map = {
                "EMERGENCY_MODE_ACTIVATED": AuditEventType.EMERGENCY_MODE_ACTIVATED,
                "EMERGENCY_MODE_DEACTIVATED": AuditEventType.EMERGENCY_MODE_DEACTIVATED,
            }
            adapter.record(
                event_type=event_type_map.get(event_type_str, AuditEventType.EMERGENCY_MODE_DEACTIVATED),
                source="GovernanceTask",
                details=audit_details,
                success=success,
                error_message=error_message,
                domain="governance",
            )
        except Exception as e:
            logger.debug(f"[GovernanceTaskAudit] Adapter record failed: {e}")
    
    log_func = logger.info if success else logger.warning
    level_str = f"L{emergency_level}" if emergency_level is not None else "N/A"
    log_func(
        f"[GovernanceTaskAudit] {action.upper()} | level={level_str} | "
        f"status={status} | auto_recovered={auto_recovered} | task_id={task_id}"
    )
    return wal_seq


def log_traffic_aware_replay_audit(
    domain: Optional[str] = None,
    status: str = "completed",
    total: int = 0,
    success_count: int = 0,
    failed_count: int = 0,
    skipped_reason: Optional[str] = None,
    health_checks: Optional[Dict[str, bool]] = None,
    error_message: Optional[str] = None,
    task_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """
    Traffic-Aware Replay Celery task 실행을 Audit 로그에 기록.
    
    Celery task는 HTTP 요청 컨텍스트가 없으므로 직접 WAL에 기록합니다.
    
    Args:
        domain: 대상 도메인
        status: 상태 (completed, skipped, disabled, error)
        total: 전체 처리 건수
        success_count: 성공 건수
        failed_count: 실패 건수
        skipped_reason: 스킵 사유
        health_checks: 헬스 체크 결과 (circuit_breaker, error_budget, governance)
        error_message: 에러 메시지 (실패 시)
        task_id: Celery task ID
        details: 추가 상세 정보
        
    Returns:
        WAL 시퀀스 번호 (WAL 기록 성공 시), None (실패 시)
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
    # None 값 제거
    audit_details = {k: v for k, v in audit_details.items() if v is not None}
    
    success = status in ("completed", "success") and failed_count == 0
    
    # === WAL에 직접 기록 (Celery 컨텍스트) ===
    wal_seq = _write_to_wal(
        event_type="DLQ_REPLAY",
        source="TrafficAwareReplayTask",
        details=audit_details,
        success=success,
        error_message=error_message or skipped_reason,
        domain=domain or "dlq",
    )
    
    # === Adapter 통해 기록 시도 ===
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
            logger.debug(f"[TrafficAwareReplayAudit] Adapter record failed: {e}")
    
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
    drift_details: Optional[Dict[str, Any]] = None,
    operations_analyzed: int = 0,
    error_message: Optional[str] = None,
    task_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """
    Drift Detection Celery task 실행을 Audit 로그에 기록.
    
    Celery task는 HTTP 요청 컨텍스트가 없으므로 직접 WAL에 기록합니다.
    
    Args:
        check_type: 체크 유형 (sla_drift, analyze_pending, chaos_cleanup)
        status: 상태 (completed, warning, error)
        drift_detected: 드리프트 감지 여부
        drift_details: 드리프트 상세 정보
        operations_analyzed: 분석된 작업 수
        error_message: 에러 메시지 (실패 시)
        task_id: Celery task ID
        details: 추가 상세 정보
        
    Returns:
        WAL 시퀀스 번호 (WAL 기록 성공 시), None (실패 시)
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
    # None 값 제거
    audit_details = {k: v for k, v in audit_details.items() if v is not None}
    
    success = status not in ("error", "failed")
    
    # === WAL에 직접 기록 (Celery 컨텍스트) ===
    wal_seq = _write_to_wal(
        event_type="CONFIG_CHANGE",  # drift detection은 설정 변경 감지
        source="DriftDetectionTask",
        details=audit_details,
        success=success,
        error_message=error_message,
        domain="drift_detection",
    )
    
    # === Adapter 통해 기록 시도 ===
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
            logger.debug(f"[DriftDetectionAudit] Adapter record failed: {e}")
    
    log_func = logger.info if success else logger.warning
    drift_str = "DRIFT_DETECTED" if drift_detected else "NO_DRIFT"
    log_func(
        f"[DriftDetectionAudit] {check_type.upper()} | {drift_str} | "
        f"analyzed={operations_analyzed} | status={status} | task_id={task_id}"
    )
    return wal_seq
