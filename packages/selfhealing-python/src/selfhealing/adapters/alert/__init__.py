"""
Default Alert Adapter Implementations.

Provides non-invasive alerting implementations:
- StdoutAlertAdapter: Output to stdout (default for containers)
- FileAlertAdapter: Write to files (for local development)
- NullAlertAdapter: No-op (for testing or opt-out)

Users can implement their own adapters for:
- Slack/Teams webhooks
- PagerDuty/OpsGenie
- Email
- Custom solutions
"""

from .file_adapter import FileAlertAdapter
from .null_adapter import NullAlertAdapter
from .stdout_adapter import StdoutAlertAdapter

__all__ = [
    "StdoutAlertAdapter",
    "FileAlertAdapter",
    "NullAlertAdapter",
]
