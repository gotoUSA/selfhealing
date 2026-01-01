"""
Default Audit Log Adapter Implementations.

Provides non-invasive audit logging implementations:
- FileAuditLogAdapter: Log to files (default for production)
- StdoutAuditLogAdapter: Log to stdout (good for containers)
- NullAuditLogAdapter: No-op (for testing or opt-out)

WORM (Write Once Read Many) Storage Adapters:
- S3ObjectLockAdapter: AWS S3 with Object Lock (Compliance Mode)
- LokiAdapter: Grafana Loki (append-only log aggregation)
- HTTPWebhookAdapter: Generic HTTP POST to external systems
- SidecarFileWatcher: File watcher for sidecar pattern

비침투 원칙:
- 고객사 DB에 직접 접근하지 않음
- 기본값: FileAuditLogAdapter (로컬 JSONL)
- 외부 전송은 사이드카 패턴 또는 Export CLI로 수행

Users can implement their own adapters for:
- Database logging (사용자 책임)
- Custom solutions
"""

from typing import Optional
import logging

from .file_adapter import FileAuditLogAdapter
from .null_adapter import NullAuditLogAdapter
from .stdout_adapter import StdoutAuditLogAdapter
from .worm_adapters import (
    WORMAdapter,
    S3Config,
    S3ObjectLockAdapter,
    LokiConfig,
    LokiAdapter,
    HTTPWebhookAdapter,
    SidecarConfig,
    SidecarFileWatcher,
    create_worm_adapter,
)

logger = logging.getLogger(__name__)

# =============================================================================
# Singleton Audit Adapter Management
# =============================================================================

# 기본 어댑터 인스턴스 (싱글톤)
_default_adapter: Optional["AuditLogAdapter"] = None


def get_audit_adapter() -> "AuditLogAdapter":
    """
    기본 AuditLogAdapter 인스턴스 반환.
    
    우선순위:
    1. 명시적으로 설정된 어댑터 (set_audit_adapter)
    2. ProviderRegistry에 등록된 어댑터
    3. 기본 FileAuditLogAdapter
    4. NullAuditLogAdapter (fallback)
    
    Returns:
        AuditLogAdapter 인스턴스
        
    Example:
        >>> from selfhealing.adapters.audit import get_audit_adapter
        >>> adapter = get_audit_adapter()
        >>> adapter.log(entry)
    """
    global _default_adapter
    
    if _default_adapter is not None:
        return _default_adapter
    
    # 1. ProviderRegistry 시도
    try:
        from selfhealing.factory import ProviderRegistry
        adapter = ProviderRegistry.get_audit_adapter()
        if adapter is not None:
            _default_adapter = adapter
            logger.debug("[AuditAdapter] Using adapter from ProviderRegistry")
            return adapter
    except (ImportError, ValueError, AttributeError):
        pass
    
    # 2. 기본 FileAuditLogAdapter
    try:
        import os
        log_path = os.getenv("AUDIT_LOG_PATH", "logs/audit.jsonl")
        _default_adapter = FileAuditLogAdapter(log_path)
        logger.debug(f"[AuditAdapter] Using FileAuditLogAdapter: {log_path}")
        return _default_adapter
    except Exception as e:
        logger.warning(f"[AuditAdapter] FileAuditLogAdapter failed: {e}")
    
    # 3. Fallback: NullAuditLogAdapter
    logger.warning("[AuditAdapter] Using NullAuditLogAdapter (fallback)")
    _default_adapter = NullAuditLogAdapter()
    return _default_adapter


def set_audit_adapter(adapter: "AuditLogAdapter") -> None:
    """
    기본 AuditLogAdapter 설정.
    
    Args:
        adapter: 사용할 AuditLogAdapter 인스턴스
        
    Example:
        >>> from selfhealing.adapters.audit import set_audit_adapter
        >>> from selfhealing.adapters.audit import StdoutAuditLogAdapter
        >>> set_audit_adapter(StdoutAuditLogAdapter())
    """
    global _default_adapter
    _default_adapter = adapter
    logger.debug(f"[AuditAdapter] Set adapter: {type(adapter).__name__}")


def reset_audit_adapter() -> None:
    """
    기본 AuditLogAdapter 초기화 (테스트용).
    
    Example:
        >>> from selfhealing.adapters.audit import reset_audit_adapter
        >>> reset_audit_adapter()  # 다음 get_audit_adapter()에서 새로 생성
    """
    global _default_adapter
    _default_adapter = None
    logger.debug("[AuditAdapter] Reset adapter")


__all__ = [
    # Default Adapters (Non-invasive)
    "FileAuditLogAdapter",
    "StdoutAuditLogAdapter",
    "NullAuditLogAdapter",
    # WORM Storage Adapters
    "WORMAdapter",
    "S3Config",
    "S3ObjectLockAdapter",
    "LokiConfig",
    "LokiAdapter",
    "HTTPWebhookAdapter",
    # Sidecar Pattern
    "SidecarConfig",
    "SidecarFileWatcher",
    # Factory
    "create_worm_adapter",
    # Singleton Management
    "get_audit_adapter",
    "set_audit_adapter",
    "reset_audit_adapter",
]
