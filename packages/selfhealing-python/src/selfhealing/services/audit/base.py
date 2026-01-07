"""
Audit Base - WAL 공통 로직 및 헬퍼 함수

WAL 기반 누락 0 보장 (20_AUDIT_UNIFICATION_PLAN.md ADR-005):
- 모든 audit 이벤트는 WAL에 먼저 기록 (로컬 파일, 거의 실패 안함)
- 이후 중앙 저장소에 기록 시도 (Best Effort)
- Background Sync Worker가 WAL → 중앙 저장소 동기화
- Reconciler가 주기적으로 누락 감지 및 재전송
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from selfhealing.audit.event_buffer import AuditEventType

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
    actor_roles: Optional[list[str]] = None,  # Phase 25: RBAC 역할 정보
) -> Optional[int]:
    """
    WAL에 audit 이벤트 기록.
    
    Phase 25: actor_roles를 자동으로 ActorContext에서 가져옴.
    
    Args:
        event_type: 이벤트 유형 (e.g., "CB_STATE_CHANGE")
        source: 이벤트 소스 (e.g., "CircuitBreaker")
        details: 이벤트 상세 정보
        success: 성공 여부
        error_message: 에러 메시지 (실패 시)
        domain: 비즈니스 도메인 (e.g., "payment")
        target_id: 대상 ID
        actor_roles: RBAC 역할 목록 (None이면 ActorContext에서 자동 추출)
    
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
    
    # Phase 25: ActorContext에서 actor 정보 자동 추출
    actor_id = None
    actor_type = "system"
    if actor_roles is None:
        actor_roles = []
    
    try:
        from selfhealing.context.actor_context import ActorContext
        if ActorContext.is_set():
            actor = ActorContext.get_current()
            actor_id = actor.actor_id
            actor_type = actor.actor_type
            if not actor_roles:  # 명시적으로 전달되지 않은 경우에만
                actor_roles = actor.roles
    except ImportError:
        pass
    except Exception:
        pass
    
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
            "actor_id": actor_id,  # Phase 25: actor 정보 추가
            "actor_type": actor_type,  # Phase 25: actor 정보 추가
            "actor_roles": actor_roles,  # Phase 25: RBAC 역할 추가
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
    actor_roles: Optional[list[str]] = None,  # Phase 25: RBAC 역할 정보
) -> bool:
    """
    request의 버퍼에 이벤트 추가 시도.
    
    Phase 25: actor_roles를 자동으로 ActorContext에서 가져옴.
    
    Args:
        request: Django HttpRequest 객체
        event_type: 이벤트 유형
        source: 이벤트 소스
        details: 이벤트 상세 정보
        success: 성공 여부
        error_message: 에러 메시지
        domain: 비즈니스 도메인
        target_id: 대상 ID
        actor_roles: RBAC 역할 목록 (None이면 ActorContext에서 자동 추출)
    
    Returns:
        True: 버퍼에 추가 성공 (AuditMiddleware에서 기록됨)
        False: request 없거나 버퍼 추가 실패 (직접 기록 필요)
    """
    if request is None:
        return False
    
    # Phase 25: ActorContext에서 actor_roles 자동 추출
    if actor_roles is None:
        try:
            from selfhealing.context.actor_context import ActorContext
            if ActorContext.is_set():
                actor = ActorContext.get_current()
                actor_roles = actor.roles
            else:
                actor_roles = []
        except (ImportError, Exception):
            actor_roles = []
    
    try:
        from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEventType
        
        # details에 actor_roles 추가 (버퍼에서 사용)
        enriched_details = {**details}
        if actor_roles:
            enriched_details["_actor_roles"] = actor_roles
        
        buffer = RequestAuditBuffer.get_or_create(request)
        buffer.add(
            event_type=event_type,
            source=source,
            details=enriched_details,
            success=success,
            error_message=error_message,
            domain=domain,
            target_id=target_id,
        )
        return True
    except Exception as e:
        logger.debug(f"[AuditHelpers] Buffer add failed: {e}")
        return False
