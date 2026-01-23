"""
DLQ Replay Tasks - Django Adapter (DEPRECATED)

.. deprecated:: 2.0.0
    이 모듈은 하위 호환성을 위해서만 유지됩니다.
    직접 selfhealing.celery_tasks에서 import하세요.
    이 모듈은 v3.0.0에서 제거될 예정입니다.

Migration Guide:
    Before (deprecated):
        from shopping.tasks.dlq_replay_tasks import replay_single_dlq_entry
    
    After (recommended):
        from selfhealing.celery_tasks import replay_single_dlq_entry
    
    Note: replay_on_circuit_breaker_close 별칭은 
          conditional_replay_on_circuit_close로 직접 import하세요.
"""

import warnings

warnings.warn(
    "Importing from 'shopping.tasks.dlq_replay_tasks' is deprecated. "
    "Import directly from 'selfhealing.celery_tasks' instead. "
    "This module will be removed in v3.0.0.",
    DeprecationWarning,
    stacklevel=2,
)

from selfhealing.celery_tasks import (
    cleanup_resolved_dlq_entries,
    conditional_replay_on_circuit_close,
    replay_batch_by_domain,
    replay_batch_by_failure_type,
    replay_single_dlq_entry,
)

# Alias for backward compatibility
replay_on_circuit_breaker_close = conditional_replay_on_circuit_close

__all__ = [
    "cleanup_resolved_dlq_entries",
    "conditional_replay_on_circuit_close",
    "replay_batch_by_domain",
    "replay_batch_by_failure_type",
    "replay_single_dlq_entry",
    "replay_on_circuit_breaker_close",  # Alias
]
