"""
Singleton Audit Adapter Management.

싱글톤 패턴으로 AuditLogAdapter 인스턴스를 관리.

Usage:
    >>> from selfhealing.adapters.audit.singleton import get_audit_adapter
    >>> adapter = get_audit_adapter()
    >>> adapter.log(entry)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selfhealing.core.protocols import AuditLogAdapter

logger = logging.getLogger(__name__)

# =============================================================================
# Singleton Audit Adapter Management
# =============================================================================

# 기본 어댑터 인스턴스 (싱글톤)
_default_adapter: AuditLogAdapter | None = None


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
        >>> from selfhealing.adapters.audit.singleton import get_audit_adapter
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

        from .file_adapter import FileAuditLogAdapter

        log_path = os.getenv("AUDIT_LOG_PATH", "logs/audit.jsonl")
        _default_adapter = FileAuditLogAdapter(log_path)
        logger.debug(f"[AuditAdapter] Using FileAuditLogAdapter: {log_path}")
        return _default_adapter
    except Exception as e:
        logger.warning(f"[AuditAdapter] FileAuditLogAdapter failed: {e}")

    # 3. Fallback: NullAuditLogAdapter
    from .null_adapter import NullAuditLogAdapter

    logger.warning("[AuditAdapter] Using NullAuditLogAdapter (fallback)")
    _default_adapter = NullAuditLogAdapter()
    return _default_adapter


def set_audit_adapter(adapter: "AuditLogAdapter") -> None:
    """
    기본 AuditLogAdapter 설정.

    Args:
        adapter: 사용할 AuditLogAdapter 인스턴스

    Example:
        >>> from selfhealing.adapters.audit.singleton import set_audit_adapter
        >>> from selfhealing.adapters.audit.file_adapter import FileAuditLogAdapter
        >>> set_audit_adapter(FileAuditLogAdapter("/var/log/audit.jsonl"))
    """
    global _default_adapter
    _default_adapter = adapter
    logger.debug(f"[AuditAdapter] Set adapter: {type(adapter).__name__}")


def reset_audit_adapter() -> None:
    """
    기본 AuditLogAdapter 초기화 (테스트용).

    Example:
        >>> from selfhealing.adapters.audit.singleton import reset_audit_adapter
        >>> reset_audit_adapter()  # 다음 get_audit_adapter()에서 새로 생성
    """
    global _default_adapter
    _default_adapter = None
    logger.debug("[AuditAdapter] Reset adapter")
