"""
Dead Letter Queue (DLQ) Service

Provides centralized DLQ operations for the self-healing layer.
Handles storage, retrieval, and management of failed operations.

Features:
- Store failed operations with full forensic context
- Query and filter DLQ entries
- Manage DLQ lifecycle (pending → reviewing → resolved/rejected)

Reference: docs/L3_SELF_HEALING_OPERATIONS.md §1
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, List, Optional

from selfhealing.core.timezone import now
from selfhealing.core.config import get_config

if TYPE_CHECKING:
    from selfhealing.interfaces.repositories import (
        FailedOperationRepository,
        FailedOperationData,
    )

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class DLQConfig:
    """Configuration for DLQ operations."""

    enabled: bool = True
    retention_days: int = 30
    max_replay_attempts: int = 2

    @classmethod
    def from_settings(cls) -> "DLQConfig":
        """Load configuration from core config."""
        dlq_settings = get_config().dlq
        return cls(
            enabled=dlq_settings.enabled,
            retention_days=dlq_settings.retention_days,
            max_replay_attempts=dlq_settings.max_replay_attempts,
        )


# =============================================================================
# DLQ Entry Result
# =============================================================================


@dataclass
class DLQEntryResult:
    """Result of a DLQ operation."""

    success: bool
    dlq_id: int | None = None
    error: str | None = None

    @classmethod
    def created(cls, dlq_id: int) -> "DLQEntryResult":
        """Factory for successful creation."""
        return cls(success=True, dlq_id=dlq_id)

    @classmethod
    def failed(cls, error: str) -> "DLQEntryResult":
        """Factory for failed operation."""
        return cls(success=False, error=error)


# =============================================================================
# DLQ Service
# =============================================================================


class DLQService:
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

    def __init__(
        self,
        config: DLQConfig | None = None,
        repository: "FailedOperationRepository | None" = None,
    ):
        """
        Initialize the DLQ service.

        Args:
            config: Optional configuration, loads from settings if None
            repository: Optional repository for DI, uses Django adapter if None
        """
        self.config = config or DLQConfig.from_settings()
        self._repository = repository

    @property
    def repository(self) -> "FailedOperationRepository":
        """Get the repository, creating Django adapter if needed."""
        if self._repository is None:
            # Try to use ProviderRegistry from selfhealing package first
            try:
                from selfhealing.factory import ProviderRegistry

                self._repository = ProviderRegistry.get_failed_operation_repo()
            except (ImportError, ValueError):
                # Fallback to local Django adapter
                from .adapters.django_repositories import DjangoFailedOperationRepository

                self._repository = DjangoFailedOperationRepository()
        return self._repository

    @property
    def is_enabled(self) -> bool:
        """Check if DLQ is enabled."""
        return self.config.enabled

    # =========================================================================
    # Store Operations
    # =========================================================================

    def store_failure(
        self,
        domain: str,
        failure_type: str,
        order_id: Optional[int] = None,
        payment_id: Optional[int] = None,
        user_id: Optional[int] = None,
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
        Store a failed operation in the DLQ.

        Args:
            domain: Business domain (payment, point, inventory, webhook, notification)
            failure_type: Specific failure type (e.g., PG_TIMEOUT, AMOUNT_MISMATCH)
            order_id: Related Order ID
            payment_id: Related Payment ID
            user_id: Related User ID
            error_code: Error code from external system
            error_message: Human-readable error message
            snapshot_data: State snapshot for recovery
            request_data: Original request payload
            response_data: External system response
            metadata: Additional debug context
            next_action_hint: Guidance for operators
            recommended_action: Suggested action (replay, manual_check, etc.)

        Returns:
            DLQEntryResult with creation status
        """
        if not self.is_enabled:
            logger.debug("[DLQService] DLQ is disabled, skipping storage")
            return DLQEntryResult.failed("DLQ is disabled")

        try:
            failed_op = self.repository.create(
                domain=domain,
                failure_type=failure_type,
                order_id=order_id,
                payment_id=payment_id,
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

            logger.info(f"[DLQService] Created DLQ entry: id={failed_op.id}, " f"domain={domain}, failure_type={failure_type}")

            return DLQEntryResult.created(failed_op.id)

        except Exception as e:
            logger.error(f"[DLQService] Failed to store in DLQ: {e}")
            return DLQEntryResult.failed(str(e))

    def store_with_forensic_context(
        self,
        domain: str,
        failure_type: str,
        forensic_context: Any,
        order_id: Optional[int] = None,
        payment_id: Optional[int] = None,
        user_id: Optional[int] = None,
        error_code: str = "",
        error_message: str = "",
        next_action_hint: str = "",
        recommended_action: str = "",
    ) -> DLQEntryResult:
        """
        Store a failed operation with full forensic context.

        This is the preferred method when forensic context is available.

        Args:
            domain: Business domain
            failure_type: Specific failure type
            forensic_context: ForensicContext instance with full debug info
            order_id: Related Order ID
            payment_id: Related Payment ID
            user_id: Related User ID
            error_code: Error code
            error_message: Human-readable error message
            next_action_hint: Guidance for operators
            recommended_action: Suggested action

        Returns:
            DLQEntryResult with creation status
        """
        from .forensic_context import ForensicContext

        if not isinstance(forensic_context, ForensicContext):
            raise TypeError("forensic_context must be a ForensicContext instance")

        # Build snapshot data from forensic context
        snapshot_data = {
            "order_id": order_id,
            "payment_id": payment_id,
            "user_id": user_id,
        }

        return self.store_failure(
            domain=domain,
            failure_type=failure_type,
            order_id=order_id,
            payment_id=payment_id,
            user_id=user_id,
            error_code=error_code,
            error_message=error_message,
            snapshot_data=snapshot_data,
            request_data=forensic_context.extra.get("request_data", {}),
            response_data={
                "external_response_code": forensic_context.external_response_code,
                "external_response_body": forensic_context.external_response_body,
            },
            metadata=forensic_context.to_metadata(),
            next_action_hint=next_action_hint,
            recommended_action=recommended_action,
        )

    # =========================================================================
    # Query Operations
    # =========================================================================

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

        SLA thresholds are loaded from configuration.
        See config.SLAThresholds for default values.

        Returns:
            List of SLA-breached FailedOperationData entries
        """
        from selfhealing.core.config import get_config
        
        current_time = now()
        sla_config = get_config().sla
        
        return self.repository.find_sla_breached(
            current_time=current_time,
            sla_thresholds={
                "payment": sla_config.get_threshold("payment"),
                "point": sla_config.get_threshold("point"),
                "inventory": sla_config.get_threshold("inventory"),
                "webhook": sla_config.get_threshold("webhook"),
                "notification": sla_config.get_threshold("notification"),
            }
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

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_stats(self) -> dict[str, Any]:
        """
        Get DLQ statistics.

        Returns:
            Dictionary with DLQ statistics
        """
        return self.repository.get_statistics()


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
    order_id: Optional[int] = None,
    payment_id: Optional[int] = None,
    user_id: Optional[int] = None,
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
        order_id=order_id,
        payment_id=payment_id,
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
