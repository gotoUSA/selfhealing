"""
DLQ Entry Operations Mixin.

Provides methods for retry, resolve, and entry management operations.
Uses Repository pattern for domain-free architecture.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from selfhealing.core.timezone import now

if TYPE_CHECKING:
    pass

logger = structlog.get_logger()


class EntryOperationsMixin:
    """Mixin providing DLQ entry operations using Repository pattern."""

    def retry_entry(self, pk: int) -> dict[str, Any]:
        """
        Retry a single DLQ entry.

        Increments retry_count via Repository, not Django ORM directly.

        Args:
            pk: Entry primary key

        Returns:
            Dict with operation details:
                - success: bool
                - id: int
                - retry_count: int
                - previous_retry_count: int
                - message: str

        Raises:
            ValueError: If entry not found or already resolved/archived
        """
        entry = self.repository.get_by_id(pk)

        if entry is None:
            raise ValueError(f"DLQ entry {pk} not found")

        if entry.status == "resolved":
            raise ValueError("Cannot retry an already resolved entry")

        if entry.status == "archived":
            raise ValueError("Cannot retry an archived entry")

        old_count = entry.retry_count

        # Use repository method
        success = self.repository.increment_retry_count(pk)

        if not success:
            raise ValueError(f"Failed to increment retry count for entry {pk}")

        logger.info(
            "dlq_service.retry_triggered_entry",
            pk=pk,
            entry=entry.domain,
            entry_2=entry.failure_type,
        )

        return {
            "success": True,
            "id": pk,
            "retry_count": old_count + 1,
            "previous_retry_count": old_count,
            "message": f"Retry triggered for entry {pk}",
        }

    def resolve_entry(self, pk: int, notes: str = "") -> dict[str, Any]:
        """
        Manually resolve a DLQ entry.

        Args:
            pk: Entry primary key
            notes: Resolution notes (optional)

        Returns:
            Dict with operation details:
                - success: bool
                - id: int
                - previous_status: str
                - current_status: str
                - resolved_at: str (ISO format)
                - notes: str

        Raises:
            ValueError: If entry not found or already resolved/archived
        """
        entry = self.repository.get_by_id(pk)

        if entry is None:
            raise ValueError(f"DLQ entry {pk} not found")

        if entry.status == "resolved":
            raise ValueError("Entry is already resolved")

        if entry.status == "archived":
            raise ValueError("Cannot resolve an archived entry")

        old_status = entry.status

        # Use repository method
        success = self.repository.mark_as_resolved(
            id=pk,
            resolution_type="manual",
            resolution_note=notes,
        )

        if not success:
            raise ValueError(f"Failed to resolve entry {pk}")

        resolved_at = now()

        logger.info(
            "dlq_service.entry_manually_resolved",
            pk=pk,
            notes=notes,
        )

        # Metrics update (Fail-Open)
        try:
            from selfhealing.metrics.event_handlers import DLQMetricEventHandler

            DLQMetricEventHandler.on_item_resolved(
                domain=entry.domain,
                resolution_type="manual",
                duration_seconds=None,
            )
        except ImportError:
            pass

        return {
            "success": True,
            "id": pk,
            "previous_status": old_status,
            "current_status": "resolved",
            "resolved_at": resolved_at.isoformat(),
            "notes": notes,
        }

    def get_entry(self, pk: int) -> dict[str, Any] | None:
        """
        Get detailed info for a single DLQ entry.

        Args:
            pk: Entry primary key

        Returns:
            Dictionary with entry details or None if not found
        """
        entry = self.repository.get_by_id(pk)

        if entry is None:
            return None

        return {
            "id": entry.id,
            "domain": entry.domain,
            "failure_type": entry.failure_type,
            "status": entry.status,
            "retry_count": entry.retry_count,
            "max_retries": entry.max_retries,
            "entity_type": entry.entity_type,
            "entity_id": entry.entity_id,
            "error_code": entry.error_code,
            "error_message": entry.error_message,
            "snapshot_data": entry.snapshot_data,
            "request_data": entry.request_data,
            "response_data": entry.response_data,
            "metadata": entry.metadata,
            "resolution_note": entry.resolution_note,
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
            "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
            "resolved_at": entry.resolved_at.isoformat() if entry.resolved_at else None,
        }
