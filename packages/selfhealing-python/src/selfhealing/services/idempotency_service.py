"""
Idempotency Service - Backward Compatibility Shim.

.. deprecated:: 2.1.0
    Import from ``selfhealing.services.idempotency`` instead.
    This shim will be removed in v3.0.0.

기존 ``idempotency_service.py`` 플랫 파일은 ``idempotency/`` 패키지로 이전되었습니다.
이 shim은 하위 호환성을 위해 유지되며, v3.0.0에서 삭제됩니다.
"""

import sys
import warnings

warnings.warn(
    "Importing from 'selfhealing.services.idempotency_service' is deprecated. "
    "Use 'selfhealing.services.idempotency' instead. "
    "This module will be removed in v3.0.0.",
    DeprecationWarning,
    stacklevel=2,
)

import selfhealing.services.idempotency as _module  # noqa: E402

sys.modules[__name__] = _module
