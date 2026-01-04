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

