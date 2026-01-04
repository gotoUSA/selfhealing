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
from .singleton import (
    get_audit_adapter,
    set_audit_adapter,
    reset_audit_adapter,
)


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
