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
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

if TYPE_CHECKING:
    from shopping.models.failed_operation import FailedOperation
    from shopping.models.order import Order
    from shopping.models.payment import Payment
    from shopping.models.user import User

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
        """Load configuration from Django settings."""
        self_healing = getattr(settings, "SELF_HEALING", {})
        dlq_config = self_healing.get("DLQ", {})

        return cls(
            enabled=dlq_config.get("ENABLED", True),
            retention_days=dlq_config.get("RETENTION_DAYS", 30),
            max_replay_attempts=dlq_config.get("MAX_REPLAY_ATTEMPTS", 2),
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
    """

    def __init__(self, config: DLQConfig | None = None):
        """
        Initialize the DLQ service.

        Args:
            config: Optional configuration, loads from settings if None
        """
        self.config = config or DLQConfig.from_settings()

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
        order: "Order | None" = None,
        payment: "Payment | None" = None,
        user: "User | None" = None,
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
            order: Related Order instance
            payment: Related Payment instance
            user: Related User instance
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
            from shopping.models.failed_operation import FailedOperation

            failed_op = FailedOperation.create_from_failure(
                domain=domain,
                failure_type=failure_type,
                order=order,
                payment=payment,
                user=user,
                error_code=error_code,
                error_message=error_message,
                snapshot_data=snapshot_data,
                request_data=request_data,
                response_data=response_data,
                metadata=metadata,
                next_action_hint=next_action_hint,
                recommended_action=recommended_action,
                retention_days=self.config.retention_days,
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
        forensic_context: "ForensicContext",
        order: "Order | None" = None,
        payment: "Payment | None" = None,
        user: "User | None" = None,
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
            order: Related Order instance
            payment: Related Payment instance
            user: Related User instance
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
        snapshot_data = self._build_snapshot_data(order, payment, user)

        return self.store_failure(
            domain=domain,
            failure_type=failure_type,
            order=order,
            payment=payment,
            user=user,
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

    def _build_snapshot_data(
        self,
        order: "Order | None",
        payment: "Payment | None",
        user: "User | None",
    ) -> dict[str, Any]:
        """Build snapshot data from related entities."""
        snapshot = {}

        if order:
            snapshot["order_id"] = order.id
            snapshot["order_number"] = getattr(order, "order_number", None)
            snapshot["order_status"] = order.status

        if payment:
            snapshot["payment_id"] = payment.id
            snapshot["payment_key"] = getattr(payment, "payment_key", None)
            snapshot["amount"] = str(payment.amount) if payment.amount else None
            snapshot["payment_status"] = payment.status

        if user:
            snapshot["user_id"] = user.id
            snapshot["user_email"] = user.email
            snapshot["user_points"] = getattr(user, "point_balance", None)

        return snapshot

    # =========================================================================
    # Query Operations
    # =========================================================================

    def get_pending_entries(
        self,
        domain: str | None = None,
        failure_type: str | None = None,
        limit: int = 100,
    ) -> QuerySet["FailedOperation"]:
        """
        Get pending DLQ entries.

        Args:
            domain: Filter by domain (optional)
            failure_type: Filter by failure type (optional)
            limit: Maximum number of entries to return

        Returns:
            QuerySet of pending FailedOperation entries
        """
        from shopping.models.failed_operation import FailedOperation

        qs = FailedOperation.objects.filter(status=FailedOperation.Status.PENDING)

        if domain:
            qs = qs.filter(domain=domain)
        if failure_type:
            qs = qs.filter(failure_type=failure_type)

        return qs.order_by("created_at")[:limit]

    def get_replayable_entries(
        self,
        domain: str | None = None,
        failure_type: str | None = None,
        limit: int = 100,
    ) -> QuerySet["FailedOperation"]:
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
            QuerySet of replayable FailedOperation entries
        """
        from shopping.models.failed_operation import FailedOperation

        qs = FailedOperation.objects.filter(
            status=FailedOperation.Status.PENDING,
            retry_count__lt=self.config.max_replay_attempts,
        )

        if domain:
            qs = qs.filter(domain=domain)
        if failure_type:
            qs = qs.filter(failure_type=failure_type)

        return qs.order_by("created_at")[:limit]

    def get_sla_breached_entries(self) -> QuerySet["FailedOperation"]:
        """
        Get entries that have breached their SLA.

        SLA thresholds by domain:
        - payment: 1 hour
        - point: 4 hours
        - inventory: 2 hours
        - webhook: 8 hours
        - notification: 24 hours

        Returns:
            QuerySet of SLA-breached FailedOperation entries
        """
        from shopping.models.failed_operation import FailedOperation

        now = timezone.now()
        sla_thresholds = {
            FailedOperation.Domain.PAYMENT: timedelta(hours=1),
            FailedOperation.Domain.POINT: timedelta(hours=4),
            FailedOperation.Domain.INVENTORY: timedelta(hours=2),
            FailedOperation.Domain.WEBHOOK: timedelta(hours=8),
            FailedOperation.Domain.NOTIFICATION: timedelta(hours=24),
        }

        # Build OR conditions for each domain
        from django.db.models import Q

        conditions = Q()
        for domain, threshold in sla_thresholds.items():
            conditions |= Q(domain=domain, created_at__lt=now - threshold)

        return FailedOperation.objects.filter(
            status=FailedOperation.Status.PENDING,
        ).filter(conditions)

    def get_expired_entries(self) -> QuerySet["FailedOperation"]:
        """
        Get entries that have passed their retention period.

        Returns:
            QuerySet of expired FailedOperation entries
        """
        from shopping.models.failed_operation import FailedOperation

        now = timezone.now()
        return FailedOperation.objects.filter(
            expires_at__lt=now,
            status__in=[
                FailedOperation.Status.PENDING,
                FailedOperation.Status.REJECTED,
            ],
        )

    def get_entry_by_id(self, dlq_id: int) -> "FailedOperation | None":
        """
        Get a single DLQ entry by ID.

        Args:
            dlq_id: The DLQ entry ID

        Returns:
            FailedOperation instance or None
        """
        from shopping.models.failed_operation import FailedOperation

        try:
            return FailedOperation.objects.get(id=dlq_id)
        except FailedOperation.DoesNotExist:
            return None

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_stats(self) -> dict[str, Any]:
        """
        Get DLQ statistics.

        Returns:
            Dictionary with DLQ statistics
        """
        from django.db.models import Count

        from shopping.models.failed_operation import FailedOperation

        by_status = dict(FailedOperation.objects.values("status").annotate(count=Count("id")).values_list("status", "count"))

        by_domain = dict(
            FailedOperation.objects.filter(status=FailedOperation.Status.PENDING)
            .values("domain")
            .annotate(count=Count("id"))
            .values_list("domain", "count")
        )

        return {
            "pending_count": by_status.get(FailedOperation.Status.PENDING, 0),
            "reviewing_count": by_status.get(FailedOperation.Status.REVIEWING, 0),
            "resolved_count": by_status.get(FailedOperation.Status.RESOLVED, 0),
            "rejected_count": by_status.get(FailedOperation.Status.REJECTED, 0),
            "pending_by_domain": by_domain,
        }


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
    order=None,
    payment=None,
    user=None,
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
        order=order,
        payment=payment,
        user=user,
        error_code=error_code,
        error_message=error_message,
        snapshot_data=snapshot_data,
        request_data=request_data,
        response_data=response_data,
        metadata=metadata,
        next_action_hint=next_action_hint,
        recommended_action=recommended_action,
    )
