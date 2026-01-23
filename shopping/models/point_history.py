"""
Backward Compatibility Module for PointHistory (DEPRECATED)

.. deprecated:: 2.0.0
    이 모듈은 하위 호환성을 위해서만 유지됩니다.
    직접 shopping.models.point에서 import하세요.
    이 모듈은 v3.0.0에서 제거될 예정입니다.

Migration Guide:
    Before (deprecated):
        from shopping.models.point_history import PointHistory
    
    After (recommended):
        from shopping.models.point import PointHistory
"""

import warnings

warnings.warn(
    "Importing from 'shopping.models.point_history' is deprecated. "
    "Import from 'shopping.models.point' instead. "
    "This module will be removed in v3.0.0.",
    DeprecationWarning,
    stacklevel=2,
)

from shopping.models.point import PointHistory

__all__ = ["PointHistory"]
