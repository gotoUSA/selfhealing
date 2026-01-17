"""
DLQ Query Operations Mixin.

Provides methods for querying DLQ entries.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, List, Optional

from selfhealing.core.timezone import now

if TYPE_CHECKING:
    from selfhealing.interfaces.repositories import FailedOperationData

logger = logging.getLogger(__name__)


class QueryOperationsMixin:
    """Mixin providing DLQ query operations."""

    def get_pending_entries(
        self,
        domain: str | None = None,
        failure_type: str | None = None,
        limit: int = 100,
    ) -> List["FailedOperationData"]:
        """
        Get pending DLQ entries.

        Args:
            domain: Filter by domain (optional)
            failure_type: Filter by failure type (optional)
            limit: Maximum number of entries to return

        Returns:
            List of pending FailedOperationData entries
        """
        return self.repository.find_by_status(
            status="pending",
            domain=domain,
            failure_type=failure_type,
            limit=limit,
        )

    def get_replayable_entries(
        self,
        domain: str | None = None,
        failure_type: str | None = None,
        limit: int = 100,
    ) -> List["FailedOperationData"]:
        """
        Get entries that can be replayed.

        Entries are replayable if:
        - Status is PENDING
        - retry_count < max_retries

        Args:
            domain: Filter by domain (optional)
            failure_type: Filter by failure type (optional)
            limit: Maximum number of entries to return

        Returns:
            List of replayable FailedOperationData entries
        """
        return self.repository.find_replayable(
            max_retries=self.config.max_replay_attempts,
            domain=domain,
            failure_type=failure_type,
            limit=limit,
        )

    def get_sla_breached_entries(self) -> List["FailedOperationData"]:
        """
        Get entries that have breached their SLA.

        SLA thresholds are loaded from configuration (domain-free).
        Uses SLAConfig.get_all_thresholds() to support any configured domain.

        Returns:
            List of SLA-breached FailedOperationData entries
        """
        from selfhealing.settings import get_config

        current_time = now()
        sla_config = get_config().sla

        # Domain-Free: 설정된 모든 도메인의 임계값을 동적으로 로드
        return self.repository.find_sla_breached(
            current_time=current_time,
            sla_thresholds=sla_config.get_all_thresholds(),
        )

    def get_expired_entries(self) -> List["FailedOperationData"]:
        """
        Get entries that have passed their retention period.

        Returns:
            List of expired FailedOperationData entries
        """
        current_time = now()
        return self.repository.find_expired(current_time=current_time)

    def get_entry_by_id(self, dlq_id: int) -> Optional["FailedOperationData"]:
        """
        Get a single DLQ entry by ID.

        Args:
            dlq_id: The DLQ entry ID

        Returns:
            FailedOperationData or None
        """
        return self.repository.get_by_id(dlq_id)

    def get_stats(self) -> dict[str, Any]:
        """
        Get DLQ statistics.

        Returns:
            Dictionary with DLQ statistics
        """
        return self.repository.get_statistics()
