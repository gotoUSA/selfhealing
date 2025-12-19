"""
DLQ Replay Service

Provides replay functionality for failed operations in the DLQ.
Supports manual replay, batch replay, and conditional replay on circuit breaker recovery.

Replay Types:
- Manual Replay: Operator selects individual items
- Batch Replay: Operator selects multiple items by filter
- Conditional Replay: Auto-replay when external system recovers

Reference: docs/L3_SELF_HEALING_OPERATIONS.md §2
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

if TYPE_CHECKING:
    from shopping.models.failed_operation import FailedOperation
    from shopping.services.self_healing.interfaces.repositories import (
        FailedOperationRepository,
    )

logger = logging.getLogger(__name__)


# =============================================================================
# Replay Result
# =============================================================================


@dataclass
class ReplayResult:
    """Result of a replay operation."""

    success: bool
    dlq_id: int
    message: str = ""
    error: str | None = None
    data: dict[str, Any] | None = None

    @classmethod
    def succeeded(cls, dlq_id: int, message: str = "", data: dict | None = None) -> "ReplayResult":
        """Factory for successful replay."""
        return cls(success=True, dlq_id=dlq_id, message=message, data=data)

    @classmethod
    def failed(cls, dlq_id: int, error: str) -> "ReplayResult":
        """Factory for failed replay."""
        return cls(success=False, dlq_id=dlq_id, error=error)


@dataclass
class BatchReplayResult:
    """Result of a batch replay operation."""

    total: int = 0
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    results: list[ReplayResult] | None = None


# =============================================================================
# Replay Handlers (Domain-specific)
# =============================================================================


class ReplayHandler(ABC):
    """
    Abstract base class for domain-specific replay handlers.

    Each domain (payment, point, inventory, etc.) should implement
    its own replay logic by subclassing this.
    """

    @property
    @abstractmethod
    def domain(self) -> str:
        """Return the domain this handler handles."""
        pass

    @abstractmethod
    def replay(self, failed_op: "FailedOperation") -> ReplayResult:
        """
        Execute replay for a single failed operation.

        Args:
            failed_op: The FailedOperation to replay

        Returns:
            ReplayResult indicating success or failure
        """
        pass

    @abstractmethod
    def can_replay(self, failed_op: "FailedOperation") -> tuple[bool, str]:
        """
        Check if the operation can be replayed.

        Args:
            failed_op: The FailedOperation to check

        Returns:
            Tuple of (can_replay: bool, reason: str)
        """
        pass


class PaymentReplayHandler(ReplayHandler):
    """Replay handler for payment domain failures."""

    @property
    def domain(self) -> str:
        return "payment"

    def can_replay(self, failed_op: "FailedOperation") -> tuple[bool, str]:
        """Check if payment operation can be replayed."""
        # Cannot replay if original payment is already completed
        if failed_op.payment and failed_op.payment.is_paid:
            return False, "Payment is already completed"

        # Cannot replay if order is cancelled
        if failed_op.order and failed_op.order.status == "cancelled":
            return False, "Order is cancelled"

        # Cannot replay certain failure types
        non_replayable_types = [
            "AMOUNT_MISMATCH_PG_RESPONSE",
            "SECURITY_SIGNATURE_INVALID",
            "DUPLICATE_PAYMENT",
        ]
        if failed_op.failure_type in non_replayable_types:
            return False, f"Failure type {failed_op.failure_type} cannot be replayed"

        return True, ""

    def replay(self, failed_op: "FailedOperation") -> ReplayResult:
        """Replay a payment operation."""
        from shopping.services.payment_recovery_service import get_payment_recovery_handler

        can_replay, reason = self.can_replay(failed_op)
        if not can_replay:
            return ReplayResult.failed(failed_op.id, reason)

        try:
            recovery_handler = get_payment_recovery_handler()

            # Use snapshot data if original records are missing
            payment_id = failed_op.payment_id or failed_op.snapshot_data.get("payment_id")
            order_id = failed_op.order_id or failed_op.snapshot_data.get("order_id")

            if not payment_id or not order_id:
                return ReplayResult.failed(failed_op.id, "Missing payment_id or order_id for replay")

            # Schedule retry through recovery handler
            task_id = recovery_handler.schedule_retry(
                payment_id=payment_id,
                order_id=order_id,
                attempt=0,  # Fresh attempt
            )

            return ReplayResult.succeeded(failed_op.id, f"Replay scheduled with task_id={task_id}", data={"task_id": task_id})

        except Exception as e:
            logger.error(f"[PaymentReplayHandler] Replay failed: {e}")
            return ReplayResult.failed(failed_op.id, str(e))


class PointReplayHandler(ReplayHandler):
    """Replay handler for point domain failures."""

    @property
    def domain(self) -> str:
        return "point"

    def can_replay(self, failed_op: "FailedOperation") -> tuple[bool, str]:
        """Check if point operation can be replayed."""
        # Cannot replay if user doesn't exist
        if not failed_op.user:
            return False, "User not found"

        return True, ""

    def replay(self, failed_op: "FailedOperation") -> ReplayResult:
        """Replay a point operation."""
        can_replay, reason = self.can_replay(failed_op)
        if not can_replay:
            return ReplayResult.failed(failed_op.id, reason)

        try:
            from shopping.services.point_service import add_point

            snapshot = failed_op.snapshot_data
            user = failed_op.user

            # Extract point operation details from snapshot
            amount = snapshot.get("amount", 0)
            reason_text = snapshot.get("reason", "Replay from DLQ")

            if amount > 0:
                add_point(
                    user=user,
                    amount=amount,
                    reason=f"[DLQ Replay] {reason_text}",
                )
                return ReplayResult.succeeded(failed_op.id, f"Added {amount} points to user {user.id}")
            else:
                return ReplayResult.failed(failed_op.id, "Invalid point amount in snapshot")

        except Exception as e:
            logger.error(f"[PointReplayHandler] Replay failed: {e}")
            return ReplayResult.failed(failed_op.id, str(e))


class WebhookReplayHandler(ReplayHandler):
    """Replay handler for webhook domain failures."""

    @property
    def domain(self) -> str:
        return "webhook"

    def can_replay(self, failed_op: "FailedOperation") -> tuple[bool, str]:
        """Check if webhook operation can be replayed."""
        # Check if request data is available
        if not failed_op.request_data:
            return False, "No request data available for replay"

        return True, ""

    def replay(self, failed_op: "FailedOperation") -> ReplayResult:
        """Replay a webhook operation."""
        can_replay, reason = self.can_replay(failed_op)
        if not can_replay:
            return ReplayResult.failed(failed_op.id, reason)

        try:
            from shopping.tasks.payment_tasks import call_toss_confirm_api

            request_data = failed_op.request_data
            payment_key = request_data.get("payment_key")
            order_id = request_data.get("order_id")
            amount = request_data.get("amount")

            if not all([payment_key, order_id, amount]):
                return ReplayResult.failed(failed_op.id, "Missing required fields in request_data")

            # Queue the webhook processing task
            task = call_toss_confirm_api.delay(
                payment_key=payment_key,
                order_id=order_id,
                amount=amount,
            )

            return ReplayResult.succeeded(
                failed_op.id, f"Webhook replay scheduled with task_id={task.id}", data={"task_id": task.id}
            )

        except Exception as e:
            logger.error(f"[WebhookReplayHandler] Replay failed: {e}")
            return ReplayResult.failed(failed_op.id, str(e))


class DefaultReplayHandler(ReplayHandler):
    """Default replay handler for domains without specific handlers."""

    def __init__(self, domain_name: str = "default"):
        self._domain = domain_name

    @property
    def domain(self) -> str:
        return self._domain

    def can_replay(self, failed_op: "FailedOperation") -> tuple[bool, str]:
        """Default: cannot replay without specific handler."""
        return False, f"No specific replay handler for domain '{self.domain}'"

    def replay(self, failed_op: "FailedOperation") -> ReplayResult:
        """Default replay is not supported."""
        return ReplayResult.failed(failed_op.id, f"No replay handler implemented for domain '{self.domain}'")


# =============================================================================
# Replay Handler Registry
# =============================================================================


_replay_handlers: dict[str, ReplayHandler] = {}


def register_replay_handler(handler: ReplayHandler) -> None:
    """Register a replay handler for a domain."""
    _replay_handlers[handler.domain] = handler


def get_replay_handler(domain: str) -> ReplayHandler:
    """
    Get the replay handler for a domain.

    Args:
        domain: The domain name

    Returns:
        ReplayHandler instance for the domain
    """
    if domain in _replay_handlers:
        return _replay_handlers[domain]

    # Return default handler if no specific handler exists
    return DefaultReplayHandler(domain)


# Register default handlers
register_replay_handler(PaymentReplayHandler())
register_replay_handler(PointReplayHandler())
register_replay_handler(WebhookReplayHandler())


# =============================================================================
# Replay Service
# =============================================================================


class ReplayService:
    """
    DLQ Replay Service.

    Orchestrates replay operations for failed operations.

    Usage:
        service = ReplayService()

        # Single replay
        result = service.replay_single(dlq_id=123)

        # Batch replay
        batch_result = service.replay_batch(
            failure_type="PG_TIMEOUT",
            max_items=50
        )

    For testing with mock repository:
        mock_repo = Mock(spec=FailedOperationRepository)
        service = ReplayService(repository=mock_repo)
    """

    def __init__(self, repository: "FailedOperationRepository | None" = None):
        """
        Initialize the replay service.

        Args:
            repository: Optional repository for DI, uses Django adapter if None
        """
        self.config = self._load_config()
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

    def _load_config(self) -> dict[str, Any]:
        """Load replay configuration from settings."""
        self_healing = getattr(settings, "SELF_HEALING", {})
        return {
            "max_replay_attempts": self_healing.get("DLQ", {}).get("MAX_REPLAY_ATTEMPTS", 2),
        }

    # =========================================================================
    # Single Replay
    # =========================================================================

    @transaction.atomic
    def replay_single(self, dlq_id: int) -> ReplayResult:
        """
        Replay a single DLQ entry.

        Args:
            dlq_id: ID of the FailedOperation to replay

        Returns:
            ReplayResult indicating success or failure
        """
        from shopping.models.failed_operation import FailedOperation

        try:
            failed_op = FailedOperation.objects.select_for_update().get(id=dlq_id)
        except FailedOperation.DoesNotExist:
            return ReplayResult.failed(dlq_id, "DLQ entry not found")

        # 행동 직전 로깅 - Replay 실행 전에 기록
        logger.info(
            f"[ReplayService] REPLAYING dlq_id={dlq_id}, domain={failed_op.domain}, "
            f"failure_type={failed_op.failure_type}, retry_count={failed_op.retry_count}"
        )

        # Check replay eligibility - use the minimum of config and model's max_retries
        config_max = self.config["max_replay_attempts"]
        model_max = failed_op.max_retries
        max_replays = min(config_max, model_max)

        if failed_op.retry_count >= max_replays:
            failed_op.mark_as_rejected(note=f"Maximum replay attempts ({max_replays}) exceeded")
            return ReplayResult.failed(dlq_id, "max_replays_exceeded")

        # Mark as replaying
        try:
            failed_op.queue_for_replay()
        except ValueError as e:
            # queue_for_replay also checks max_retries - mark as rejected
            failed_op.mark_as_rejected(note=str(e))
            return ReplayResult.failed(dlq_id, "max_replays_exceeded")

        # Get appropriate handler and execute replay
        handler = get_replay_handler(failed_op.domain)

        try:
            result = handler.replay(failed_op)
        except Exception as e:
            # Handler raised an unexpected exception - escalate to REQUIRES_REVIEW
            logger.error(f"[ReplayService] Handler exception for DLQ {dlq_id}: {e}", exc_info=True)
            failed_op.mark_as_requires_review(note=f"Handler crash: {type(e).__name__}: {str(e)[:200]}")
            # Store exception details in metadata for forensics
            failed_op.metadata["handler_exception"] = {
                "type": type(e).__name__,
                "message": str(e)[:500],
                "occurred_at": timezone.now().isoformat(),
            }
            failed_op.save(update_fields=["metadata", "updated_at"])
            return ReplayResult.failed(dlq_id, f"internal_error: {type(e).__name__}")

        # Update DLQ entry based on result
        if result.success:
            failed_op.mark_as_resolved(
                resolved_by=None,  # System
                resolution_type=FailedOperation.ResolutionType.AUTO_REPLAY,
                note=result.message,
            )
            logger.info(f"[ReplayService] DLQ entry {dlq_id} replayed successfully")
        else:
            failed_op.revert_to_pending(note=result.error or "Replay failed")
            logger.warning(f"[ReplayService] DLQ entry {dlq_id} replay failed: {result.error}")

        return result

    # =========================================================================
    # Batch Replay
    # =========================================================================

    def replay_batch(
        self,
        domain: str | None = None,
        failure_type: str | None = None,
        max_items: int = 100,
    ) -> BatchReplayResult:
        """
        Replay multiple DLQ entries matching criteria.

        Args:
            domain: Filter by domain (optional)
            failure_type: Filter by failure type (optional)
            max_items: Maximum number of items to replay

        Returns:
            BatchReplayResult with summary and individual results
        """
        from shopping.models.failed_operation import FailedOperation

        max_replays = self.config["max_replay_attempts"]

        # Get eligible entries
        qs = FailedOperation.objects.filter(
            status=FailedOperation.Status.PENDING,
            retry_count__lt=max_replays,
        )

        if domain:
            qs = qs.filter(domain=domain)
        if failure_type:
            qs = qs.filter(failure_type=failure_type)

        entries = list(qs.order_by("created_at")[:max_items])

        batch_result = BatchReplayResult(
            total=len(entries),
            results=[],
        )

        for entry in entries:
            result = self.replay_single(entry.id)
            batch_result.results.append(result)

            if result.success:
                batch_result.success_count += 1
            else:
                batch_result.failed_count += 1

        logger.info(
            f"[ReplayService] Batch replay completed: "
            f"total={batch_result.total}, success={batch_result.success_count}, "
            f"failed={batch_result.failed_count}"
        )

        return batch_result

    # =========================================================================
    # Conditional Replay (Circuit Breaker Recovery)
    # =========================================================================

    def replay_on_circuit_close(
        self,
        service_name: str,
        max_items: int = 50,
        escalate_failures: bool = True,
    ) -> BatchReplayResult:
        """
        Replay entries when circuit breaker closes.

        This is triggered when an external service recovers.
        Only replays entries related to the recovered service.

        IMPORTANT: When triggered by force_close with trigger_replay=True,
        any replay failures are escalated to REQUIRES_REVIEW status.
        This is because operator-initiated recovery implies the operator
        intended to resolve these items, so failures need explicit attention.

        Args:
            service_name: Name of the service that recovered
            max_items: Maximum number of items to replay
            escalate_failures: If True, mark failed replays as REQUIRES_REVIEW

        Returns:
            BatchReplayResult with summary
        """
        # Map service names to failure types
        service_failure_types = {
            "toss_payment": ["PG_TIMEOUT", "PG_CONNECTION_ERROR", "PG_500_ERROR"],
            "notification": ["SMTP_TIMEOUT", "FCM_ERROR"],
        }

        failure_types = service_failure_types.get(service_name, [])
        if not failure_types:
            logger.info(f"[ReplayService] No failure types mapped for service '{service_name}'")
            return BatchReplayResult()

        # Replay entries with matching failure types
        from shopping.models.failed_operation import FailedOperation

        max_replays = self.config["max_replay_attempts"]

        qs = FailedOperation.objects.filter(
            status=FailedOperation.Status.PENDING,
            failure_type__in=failure_types,
            retry_count__lt=max_replays,
        ).order_by("created_at")[:max_items]

        entries = list(qs)
        batch_result = BatchReplayResult(
            total=len(entries),
            results=[],
        )

        for entry in entries:
            result = self.replay_single(entry.id)
            batch_result.results.append(result)

            if result.success:
                batch_result.success_count += 1
            else:
                batch_result.failed_count += 1

                # Escalate failures to REQUIRES_REVIEW when triggered by forced close
                # This ensures operator attention for operator-initiated recoveries
                if escalate_failures:
                    try:
                        failed_entry = FailedOperation.objects.get(id=entry.id)
                        if failed_entry.status == FailedOperation.Status.PENDING:
                            failed_entry.mark_as_requires_review(
                                note=f"Conditional replay failed after circuit close for {service_name}: {result.error}"
                            )
                            logger.warning(
                                f"[ReplayService] Escalated DLQ {entry.id} to REQUIRES_REVIEW "
                                f"after conditional replay failure"
                            )
                    except FailedOperation.DoesNotExist:
                        pass

        logger.info(
            f"[ReplayService] Circuit close replay for {service_name}: "
            f"total={batch_result.total}, success={batch_result.success_count}, "
            f"failed={batch_result.failed_count} (escalated={batch_result.failed_count if escalate_failures else 0})"
        )

        return batch_result


# =============================================================================
# Module-level convenience functions
# =============================================================================


_replay_service: ReplayService | None = None


def get_replay_service() -> ReplayService:
    """Get the singleton replay service instance."""
    global _replay_service
    if _replay_service is None:
        _replay_service = ReplayService()
    return _replay_service


def replay_failed_operation(dlq_id: int) -> ReplayResult:
    """
    Convenience function to replay a single DLQ entry.

    This is a shortcut for get_replay_service().replay_single(dlq_id).
    """
    return get_replay_service().replay_single(dlq_id)


def batch_replay_by_failure_type(
    failure_type: str,
    max_items: int = 100,
) -> BatchReplayResult:
    """
    Convenience function to replay entries by failure type.

    This is a shortcut for get_replay_service().replay_batch(...).
    """
    return get_replay_service().replay_batch(
        failure_type=failure_type,
        max_items=max_items,
    )
