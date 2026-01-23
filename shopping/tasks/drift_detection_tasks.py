"""
Drift Detection Tasks - Django Adapter (DEPRECATED)

.. deprecated:: 2.0.0
    이 모듈은 하위 호환성을 위해서만 유지됩니다.
    직접 selfhealing.celery_tasks에서 import하세요.
    이 모듈은 v3.0.0에서 제거될 예정입니다.

Migration Guide:
    Before (deprecated):
        from shopping.tasks.drift_detection_tasks import check_sla_drift
    
    After (recommended):
        from selfhealing.celery_tasks import check_sla_drift
"""

import warnings

warnings.warn(
    "Importing from 'shopping.tasks.drift_detection_tasks' is deprecated. "
    "Import directly from 'selfhealing.celery_tasks' instead. "
    "This module will be removed in v3.0.0.",
    DeprecationWarning,
    stacklevel=2,
)

from selfhealing.celery_tasks import (
    check_sla_drift,
    cleanup_expired_chaos_experiments,
)

__all__ = [
    "check_sla_drift",
    "cleanup_expired_chaos_experiments",
]
