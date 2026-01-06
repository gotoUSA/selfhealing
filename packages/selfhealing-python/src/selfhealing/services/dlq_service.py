"""
Dead Letter Queue (DLQ) Service - Backward Compatibility Wrapper

이 모듈은 하위 호환성을 위한 re-export wrapper입니다.
실제 구현은 selfhealing.services.dlq 패키지에 있습니다.

Usage (기존 코드 그대로 동작):
    from selfhealing.services.dlq_service import DLQService, DLQConfig
    from selfhealing.services.dlq_service import store_to_dlq, get_dlq_service

새 코드는 직접 패키지에서 import 가능:
    from selfhealing.services.dlq import DLQService, DLQConfig

Note: Admin/Dashboard operations (cleanup, archive, purge, list, entry management)
      should be implemented in the host application using Django ORM directly.
      This package follows domain-free principles (Phase 5+).
"""

from __future__ import annotations

# =============================================================================
# Re-export everything from dlq package for backward compatibility
# =============================================================================

from selfhealing.services.dlq import (
    # Main service class
    DLQService,
    # Models
    DLQConfig,
    DLQEntryResult,
    ReplayResult,
    # Module-level convenience functions
    get_dlq_service,
    store_to_dlq,
    # Base and mixins (for extension)
    DLQServiceBase,
    StoreOperationsMixin,
    QueryOperationsMixin,
    ReplayOperationsMixin,
)

# Alias for backward compatibility
enqueue_failed_operation = store_to_dlq

__all__ = [
    # Main service class
    "DLQService",
    # Models
    "DLQConfig",
    "DLQEntryResult",
    "ReplayResult",
    # Module-level convenience functions
    "get_dlq_service",
    "store_to_dlq",
    "enqueue_failed_operation",  # alias for store_to_dlq
    # Base and mixins
    "DLQServiceBase",
    "StoreOperationsMixin",
    "QueryOperationsMixin",
    "ReplayOperationsMixin",
]
