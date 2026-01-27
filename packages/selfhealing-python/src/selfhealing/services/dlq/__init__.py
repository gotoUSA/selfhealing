"""
Dead Letter Queue (DLQ) Service Package.

Provides centralized DLQ operations for the self-healing layer.
Handles storage, retrieval, and management of failed operations.

Features:
- Store failed operations with full forensic context
- Query and filter DLQ entries (via Repository pattern)
- Batch replay operations

Note: Admin/Dashboard operations (cleanup, archive, purge, list, entry management)
      should be implemented in the host application using Django ORM directly.
      This package follows domain-free principles.
"""

from __future__ import annotations

from typing import Any, Optional

# Import models from separate module
from selfhealing.services.dlq_models import (
    DLQBatchReplayStats,
    DLQConfig,
    DLQEntryResult,
)

# Import base and mixins
from .base import DLQServiceBase
from .entry_operations import EntryOperationsMixin
from .list_operations import ListOperationsMixin
from .query_operations import QueryOperationsMixin
from .replay_operations import ReplayOperationsMixin
from .store_operations import StoreOperationsMixin


class DLQService(
    StoreOperationsMixin,
    QueryOperationsMixin,
    ReplayOperationsMixin,
    EntryOperationsMixin,
    ListOperationsMixin,
    DLQServiceBase,
):
    """
    Dead Letter Queue Service.

    Provides centralized operations for managing failed operations.

    Usage:
        service = DLQService()
        result = service.store_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            order=order,
            error_message="Connection timed out",
        )
        if result.success:
            print(f"Stored as DLQ entry {result.dlq_id}")

    For testing with mock repository:
        mock_repo = Mock(spec=FailedOperationRepository)
        service = DLQService(repository=mock_repo)
    """

    pass


# =============================================================================
# Module-level convenience functions
# =============================================================================


_dlq_service: DLQService | None = None


def get_dlq_service() -> DLQService:
    """Get the singleton DLQ service instance."""
    global _dlq_service
    if _dlq_service is None:
        _dlq_service = DLQService()
    return _dlq_service


def store_to_dlq(
    domain: str,
    failure_type: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    user_id: int | None = None,
    error_code: str = "",
    error_message: str = "",
    snapshot_data: dict[str, Any] | None = None,
    request_data: dict[str, Any] | None = None,
    response_data: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    next_action_hint: str = "",
    recommended_action: str = "",
) -> DLQEntryResult:
    """
    Convenience function to store a failure in the DLQ.

    This is a shortcut for get_dlq_service().store_failure(...).
    """
    return get_dlq_service().store_failure(
        domain=domain,
        failure_type=failure_type,
        entity_type=entity_type,
        entity_id=entity_id,
        user_id=user_id,
        error_code=error_code,
        error_message=error_message,
        snapshot_data=snapshot_data,
        request_data=request_data,
        response_data=response_data,
        metadata=metadata,
        next_action_hint=next_action_hint,
        recommended_action=recommended_action,
    )


__all__ = [
    # Models
    "DLQConfig",
    "DLQEntryResult",
    "DLQBatchReplayStats",
    # Service
    "DLQService",
    "DLQServiceBase",
    # Mixins
    "StoreOperationsMixin",
    "QueryOperationsMixin",
    "ReplayOperationsMixin",
    "EntryOperationsMixin",
    "ListOperationsMixin",
    # Convenience functions
    "get_dlq_service",
    "store_to_dlq",
]
