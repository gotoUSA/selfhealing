"""
Default Audit Log Adapter Implementations.

Provides non-invasive audit logging implementations:
- FileAuditLogAdapter: Log to files (default for production)
- StdoutAuditLogAdapter: Log to stdout (good for containers)
- NullAuditLogAdapter: No-op (for testing or opt-out)

Users can implement their own adapters for:
- Database logging
- Loki/Grafana
- Elasticsearch
- Custom solutions
"""

from .file_adapter import FileAuditLogAdapter
from .null_adapter import NullAuditLogAdapter
from .stdout_adapter import StdoutAuditLogAdapter

__all__ = [
    "FileAuditLogAdapter",
    "StdoutAuditLogAdapter",
    "NullAuditLogAdapter",
]
