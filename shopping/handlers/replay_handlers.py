"""
Domain-Specific Replay Handlers for Shopping Application

These handlers contain the business logic for replaying failed operations
in the shopping domain. They are registered with the core selfhealing package
at application startup.

Usage:
    from shopping.handlers.replay_handlers import register_shopping_handlers
    register_shopping_handlers()  # Call during Django app ready()
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from selfhealing.services.replay_service import (
    ReplayHandler,
    ReplayResult,
    register_replay_handler,
)

if TYPE_CHECKING:
    from selfhealing.interfaces.repositories import FailedOperationData

logger = logging.getLogger(__name__)


# =============================================================================
# Service-to-Failure-Type Mapping (for circuit breaker replay)
# =============================================================================

SHOPPING_SERVICE_FAILURE_TYPES: dict[str, list[str]] = {
    "toss_payment": ["PG_TIMEOUT", "PG_CONNECTION_ERROR", "PG_500_ERROR"],
    "notification": ["SMTP_TIMEOUT", "FCM_ERROR"],
}


# =============================================================================
# Payment Replay Handler
# =============================================================================


class PaymentReplayHandler(ReplayHandler):
    """
    Replay handler for payment domain failures.

    This handler contains shopping-specific payment replay logic including:
    - Checking payment/order status from snapshot
    - Calling payment recovery service
    """

    def __init__(self, recovery_callback: Callable[[int, int, int], str] | None = None):
        """
        Initialize payment replay handler.

        Args:
            recovery_callback: Optional callback(payment_id, order_id, attempt) -> task_id
                               If not provided, will use shopping.services.payment_recovery_service
        """
        self._recovery_callback = recovery_callback

    @property
    def domain(self) -> str:
        return "payment"

    def can_replay(self, failed_op: "FailedOperationData") -> tuple[bool, str]:
        """Check if payment operation can be replayed."""
        snapshot = failed_op.snapshot_data or {}

        # Cannot replay if original payment is already completed
        if snapshot.get("payment_is_paid"):
            return False, "Payment is already completed"

        # Cannot replay if order is cancelled
        if snapshot.get("order_status") == "cancelled":
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

    def replay(self, failed_op: "FailedOperationData") -> ReplayResult:
        """Replay a payment operation."""
        can_replay, reason = self.can_replay(failed_op)
        if not can_replay:
            return ReplayResult.failed(failed_op.id, reason)

        try:
            callback = self._recovery_callback
            if callback is None:
                from shopping.services.payment_recovery_service import get_payment_recovery_handler

                recovery_handler = get_payment_recovery_handler()
                callback = lambda pid, oid, att: recovery_handler.schedule_retry(payment_id=pid, order_id=oid, attempt=att)

            snapshot = failed_op.snapshot_data or {}
            payment_id = getattr(failed_op, "payment_id", None) or snapshot.get("payment_id")
            order_id = getattr(failed_op, "order_id", None) or snapshot.get("order_id")

            if not payment_id or not order_id:
                return ReplayResult.failed(failed_op.id, "Missing payment_id or order_id for replay")

            task_id = callback(payment_id, order_id, 0)

            return ReplayResult.succeeded(
                failed_op.id,
                f"Replay scheduled with task_id={task_id}",
                data={"task_id": task_id},
            )

        except Exception as e:
            logger.error(f"[PaymentReplayHandler] Replay failed: {e}")
            return ReplayResult.failed(failed_op.id, str(e))


# =============================================================================
# Point Replay Handler
# =============================================================================


class PointReplayHandler(ReplayHandler):
    """
    Replay handler for point domain failures.

    This handler contains shopping-specific point replay logic.
    """

    def __init__(self, add_point_callback: Callable[[int, int, str], None] | None = None):
        """
        Initialize point replay handler.

        Args:
            add_point_callback: Optional callback(user_id, amount, reason) -> None
        """
        self._add_point_callback = add_point_callback

    @property
    def domain(self) -> str:
        return "point"

    def can_replay(self, failed_op: "FailedOperationData") -> tuple[bool, str]:
        """Check if point operation can be replayed."""
        snapshot = failed_op.snapshot_data or {}
        if not failed_op.user_id and not snapshot.get("user_id"):
            return False, "User not found"
        return True, ""

    def replay(self, failed_op: "FailedOperationData") -> ReplayResult:
        """Replay a point operation."""
        can_replay, reason = self.can_replay(failed_op)
        if not can_replay:
            return ReplayResult.failed(failed_op.id, reason)

        try:
            snapshot = failed_op.snapshot_data or {}
            user_id = failed_op.user_id or snapshot.get("user_id")
            amount = snapshot.get("amount", 0)
            reason_text = snapshot.get("reason", "Replay from DLQ")

            if amount <= 0:
                return ReplayResult.failed(failed_op.id, "Invalid point amount in snapshot")

            callback = self._add_point_callback
            if callback is None:
                from shopping.services.point_service import add_point
                from django.contrib.auth import get_user_model

                User = get_user_model()
                user = User.objects.get(id=user_id)
                add_point(
                    user=user,
                    amount=amount,
                    reason=f"[DLQ Replay] {reason_text}",
                )
            else:
                callback(user_id, amount, f"[DLQ Replay] {reason_text}")

            return ReplayResult.succeeded(failed_op.id, f"Added {amount} points to user {user_id}")

        except Exception as e:
            logger.error(f"[PointReplayHandler] Replay failed: {e}")
            return ReplayResult.failed(failed_op.id, str(e))


# =============================================================================
# Webhook Replay Handler
# =============================================================================


class WebhookReplayHandler(ReplayHandler):
    """
    Replay handler for webhook domain failures.

    This handler contains shopping-specific webhook replay logic including
    calling Toss payment confirmation API.
    """

    def __init__(self, webhook_callback: Callable[[str, str, int], str] | None = None):
        """
        Initialize webhook replay handler.

        Args:
            webhook_callback: Optional callback(payment_key, order_id, amount) -> task_id
        """
        self._webhook_callback = webhook_callback

    @property
    def domain(self) -> str:
        return "webhook"

    def can_replay(self, failed_op: "FailedOperationData") -> tuple[bool, str]:
        """Check if webhook operation can be replayed."""
        if not failed_op.request_data:
            return False, "No request data available for replay"
        return True, ""

    def replay(self, failed_op: "FailedOperationData") -> ReplayResult:
        """Replay a webhook operation."""
        can_replay, reason = self.can_replay(failed_op)
        if not can_replay:
            return ReplayResult.failed(failed_op.id, reason)

        try:
            request_data = failed_op.request_data or {}
            payment_key = request_data.get("payment_key")
            order_id = request_data.get("order_id")
            amount = request_data.get("amount")

            if not all([payment_key, order_id, amount]):
                return ReplayResult.failed(failed_op.id, "Missing required fields in request_data")

            callback = self._webhook_callback
            if callback is None:
                from shopping.tasks.payment_tasks import call_toss_confirm_api

                task = call_toss_confirm_api.delay(
                    payment_key=payment_key,
                    order_id=order_id,
                    amount=amount,
                )
                task_id = task.id
            else:
                task_id = callback(payment_key, order_id, amount)

            return ReplayResult.succeeded(
                failed_op.id,
                f"Webhook replay scheduled with task_id={task_id}",
                data={"task_id": task_id},
            )

        except Exception as e:
            logger.error(f"[WebhookReplayHandler] Replay failed: {e}")
            return ReplayResult.failed(failed_op.id, str(e))


# =============================================================================
# Registration Function
# =============================================================================


def register_shopping_handlers() -> None:
    """
    Register all shopping domain replay handlers with the core package.

    Call this function during Django application startup (e.g., in AppConfig.ready()).

    Example in shopping/apps.py:
        class ShoppingConfig(AppConfig):
            name = "shopping"

            def ready(self):
                from shopping.handlers.replay_handlers import register_shopping_handlers
                register_shopping_handlers()
    """
    register_replay_handler(PaymentReplayHandler())
    register_replay_handler(PointReplayHandler())
    register_replay_handler(WebhookReplayHandler())
    logger.info("[SelfHealing] Shopping domain replay handlers registered")


def get_service_failure_type_map() -> dict[str, list[str]]:
    """
    Get the service-to-failure-type mapping for circuit breaker replay.

    Pass this to ReplayService.replay_on_circuit_close() for shopping-specific mappings.

    Example:
        service = ReplayService()
        result = service.replay_on_circuit_close(
            service_name="toss_payment",
            service_failure_type_map=get_service_failure_type_map(),
        )
    """
    return SHOPPING_SERVICE_FAILURE_TYPES.copy()
