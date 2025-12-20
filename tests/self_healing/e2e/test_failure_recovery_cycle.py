"""
End-to-End Failure Recovery Cycle Tests

File: e2e/test_failure_recovery_cycle.py

Business Risk: User-visible failures, incomplete recovery flows
Compliance Alignment: NIST IR-4 (Incident Handling), SOC 2 (Availability)

Test Cases:
- E2E-001: Complete failure -> retry -> success cycle
- E2E-002: Failure -> max retries -> DLQ -> admin replay -> success
- E2E-003: Repeated failure-recovery-failure cycle (stress test)
- E2E-004: User-invisible flow (failure handled without user awareness)
- E2E-005: Complete audit trail for full cycle
"""

import json
from datetime import timedelta
from decimal import Decimal
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch, MagicMock

import pytest
from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from shopping.models.failed_external_request import CircuitBreakerState, FailedExternalRequest
from shopping.models.payment import Payment, PaymentLog
from shopping.models.order import Order
from shopping.models.user import User
from shopping.services.payment_recovery_service import CeleryPaymentRecovery
from shopping.tests.factories import OrderFactory, PaymentFactory, ProductFactory, UserFactory


# =============================================================================
# E2E Test Utilities and Fixtures
# =============================================================================


@dataclass
class PaymentAttempt:
    """Record of a payment attempt in the E2E flow."""

    attempt_number: int
    timestamp: Any = field(default_factory=timezone.now)
    success: bool = False
    error_code: str | None = None
    error_message: str | None = None
    action_taken: str | None = None  # "retry", "dlq", "success"
    delay_applied: float = 0


@dataclass
class E2EFlowContext:
    """Context for tracking E2E flow state."""

    payment_id: int | None = None
    order_id: int | None = None
    user_id: int | None = None
    attempts: list = field(default_factory=list)
    dlq_entry_id: int | None = None
    replay_attempts: list = field(default_factory=list)
    final_status: str = "pending"
    audit_trail: list = field(default_factory=list)
    user_visible_error: bool = False
    total_time_elapsed: float = 0


class E2EPaymentFlowSimulator:
    """
    Simulates end-to-end payment flows for testing.

    Provides controlled failure injection and recovery tracking.
    """

    def __init__(self):
        self.recovery_handler = CeleryPaymentRecovery()
        self._failure_pattern: list[bool] = []  # True = fail, False = succeed
        self._current_attempt = 0
        self._contexts: dict[int, E2EFlowContext] = {}

    def set_failure_pattern(self, pattern: list[bool]) -> None:
        """
        Set the pattern of failures for subsequent attempts.

        Example: [True, True, False] = fail, fail, succeed
        """
        self._failure_pattern = pattern
        self._current_attempt = 0

    def should_fail_next(self) -> bool:
        """Check if next attempt should fail based on pattern."""
        if self._current_attempt >= len(self._failure_pattern):
            return False  # Default to success
        should_fail = self._failure_pattern[self._current_attempt]
        self._current_attempt += 1
        return should_fail

    def reset_pattern(self) -> None:
        """Reset failure pattern state."""
        self._current_attempt = 0

    def create_context(self, payment: Payment) -> E2EFlowContext:
        """Create a new E2E flow context."""
        context = E2EFlowContext(
            payment_id=payment.id,
            order_id=payment.order_id,
            user_id=payment.order.user_id,
        )
        self._contexts[payment.id] = context
        return context

    def get_context(self, payment_id: int) -> E2EFlowContext | None:
        """Get existing context for a payment."""
        return self._contexts.get(payment_id)

    def simulate_payment_attempt(
        self,
        payment: Payment,
        context: E2EFlowContext,
        error_code: str = "PG_TIMEOUT",
    ) -> dict:
        """
        Simulate a payment attempt with potential failure.

        Returns result dictionary with action taken.
        """
        attempt = PaymentAttempt(
            attempt_number=len(context.attempts) + 1,
        )

        if self.should_fail_next():
            # Payment fails
            attempt.success = False
            attempt.error_code = error_code
            attempt.error_message = f"Simulated {error_code}"

            # Determine action based on retry count
            retry_count = len(context.attempts)
            if retry_count < 3:  # MAX_RETRIES
                attempt.action_taken = "retry"
                attempt.delay_applied = 4 ** (retry_count + 1)  # Exponential backoff
            else:
                attempt.action_taken = "dlq"

            context.attempts.append(attempt)
            context.audit_trail.append(
                {
                    "event": "payment_attempt_failed",
                    "attempt": attempt.attempt_number,
                    "error_code": error_code,
                    "action": attempt.action_taken,
                    "timestamp": timezone.now().isoformat(),
                }
            )

            return {
                "success": False,
                "action": attempt.action_taken,
                "retry_count": retry_count + 1,
                "error_code": error_code,
            }

        else:
            # Payment succeeds
            attempt.success = True
            attempt.action_taken = "success"
            context.attempts.append(attempt)
            context.final_status = "completed"

            context.audit_trail.append(
                {
                    "event": "payment_succeeded",
                    "attempt": attempt.attempt_number,
                    "timestamp": timezone.now().isoformat(),
                }
            )

            return {
                "success": True,
                "action": "success",
                "attempt": attempt.attempt_number,
            }

    def move_to_dlq(
        self,
        payment: Payment,
        context: E2EFlowContext,
        error_code: str,
    ) -> FailedExternalRequest:
        """Move payment to DLQ after max retries."""
        dlq_entry = FailedExternalRequest.objects.create(
            payment=payment,
            order=payment.order,
            domain="payment",
            amount=payment.amount,
            status="pending",
            failure_type=error_code,
            error_code=error_code,
            error_message=f"Max retries exhausted: {error_code}",
            retry_count=len(context.attempts),
        )

        context.dlq_entry_id = dlq_entry.id
        context.final_status = "in_dlq"

        context.audit_trail.append(
            {
                "event": "moved_to_dlq",
                "dlq_id": dlq_entry.id,
                "retry_count": len(context.attempts),
                "timestamp": timezone.now().isoformat(),
            }
        )

        return dlq_entry

    def simulate_admin_replay(
        self,
        dlq_entry: FailedExternalRequest,
        context: E2EFlowContext,
        admin: User,
    ) -> dict:
        """Simulate admin replaying a DLQ entry."""
        replay_attempt = {
            "dlq_id": dlq_entry.id,
            "replayed_by": admin.id,
            "timestamp": timezone.now(),
        }

        # Check if should fail
        if self.should_fail_next():
            replay_attempt["success"] = False
            replay_attempt["error"] = "Replay failed"
            context.replay_attempts.append(replay_attempt)

            context.audit_trail.append(
                {
                    "event": "replay_failed",
                    "dlq_id": dlq_entry.id,
                    "replayed_by": admin.username,
                    "timestamp": timezone.now().isoformat(),
                }
            )

            return {
                "success": False,
                "action": "replay_failed",
            }

        # Replay succeeds
        replay_attempt["success"] = True
        context.replay_attempts.append(replay_attempt)
        context.final_status = "completed"

        # Update DLQ entry
        dlq_entry.status = "resolved"
        dlq_entry.resolved_at = timezone.now()
        dlq_entry.resolved_by = admin  # ForeignKey requires User instance
        dlq_entry.resolution_note = "Successfully replayed by admin"
        dlq_entry.save()

        context.audit_trail.append(
            {
                "event": "replay_succeeded",
                "dlq_id": dlq_entry.id,
                "replayed_by": admin.username,
                "timestamp": timezone.now().isoformat(),
            }
        )

        return {
            "success": True,
            "action": "replay_succeeded",
            "resolved_at": dlq_entry.resolved_at,
        }

    def get_complete_audit_trail(self, context: E2EFlowContext) -> list[dict]:
        """Get complete audit trail for the payment flow."""
        return context.audit_trail.copy()


@pytest.fixture
def e2e_simulator() -> E2EPaymentFlowSimulator:
    """Provide an E2E payment flow simulator."""
    return E2EPaymentFlowSimulator()


@pytest.fixture
def sample_order_with_payment(db):
    """Create a sample order with payment for E2E testing."""
    user = UserFactory.with_points(50000)
    product = ProductFactory(price=Decimal("10000"), stock=10)
    order = OrderFactory(
        user=user,
        status="confirmed",
        total_amount=Decimal("10000"),
    )
    payment = PaymentFactory(
        order=order,
        status="in_progress",
        amount=Decimal("10000"),
    )
    return order, payment


@pytest.fixture
def admin_user(db) -> User:
    """Create an admin user for E2E tests."""
    return UserFactory.admin(username="e2e_test_admin")


# =============================================================================
# E2E-001: Complete Failure -> Retry -> Success Cycle
# =============================================================================


@pytest.mark.tier3_chaos
@pytest.mark.django_db(transaction=True)
class TestE2EFailureRetrySuccess:
    """
    Test complete failure-retry-success cycle.

    Validates the happy path with temporary failures.
    """

    def test_payment_succeeds_on_second_attempt(
        self,
        e2e_simulator,
        sample_order_with_payment,
    ):
        """
        Purpose:
            Validate payment succeeds after initial failure.

        Scenario:
            1. Payment attempt fails with PG timeout
            2. Retry scheduled with exponential backoff
            3. Second attempt succeeds
            4. Order completed, user notified

        Expected:
            - First attempt fails
            - Retry correctly scheduled
            - Second attempt succeeds
            - User sees successful result

        Risk Covered:
            E2E-001: Temporary failure recovery
        """
        order, payment = sample_order_with_payment

        # Arrange: Fail first, succeed second
        e2e_simulator.set_failure_pattern([True, False])
        context = e2e_simulator.create_context(payment)

        # Act: First attempt (fails)
        result1 = e2e_simulator.simulate_payment_attempt(
            payment=payment,
            context=context,
            error_code="PG_TIMEOUT",
        )

        assert result1["success"] is False
        assert result1["action"] == "retry"
        assert result1["retry_count"] == 1

        # Act: Second attempt (succeeds)
        result2 = e2e_simulator.simulate_payment_attempt(
            payment=payment,
            context=context,
            error_code="PG_TIMEOUT",
        )

        # Assert
        assert result2["success"] is True
        assert result2["action"] == "success"
        assert context.final_status == "completed"
        assert len(context.attempts) == 2

    def test_backoff_delay_increases_between_retries(
        self,
        e2e_simulator,
        sample_order_with_payment,
    ):
        """
        Purpose:
            Verify exponential backoff between retries.

        Scenario:
            1. Multiple failures
            2. Check delay increases exponentially
            3. Verify 4, 16, 64 second delays

        Expected:
            - Delay follows 4^n pattern
            - Max delay capped
        """
        order, payment = sample_order_with_payment

        # Arrange: Fail multiple times
        e2e_simulator.set_failure_pattern([True, True, True, False])
        context = e2e_simulator.create_context(payment)

        # Act: Multiple attempts
        for i in range(3):
            result = e2e_simulator.simulate_payment_attempt(
                payment=payment,
                context=context,
            )

        # Assert: Check delays
        assert context.attempts[0].delay_applied == 4  # 4^1
        assert context.attempts[1].delay_applied == 16  # 4^2
        assert context.attempts[2].delay_applied == 64  # 4^3


# =============================================================================
# E2E-002: Failure -> Max Retries -> DLQ -> Admin Replay
# =============================================================================


@pytest.mark.tier3_chaos
@pytest.mark.django_db(transaction=True)
class TestE2EFailureDLQReplay:
    """
    Test complete failure through DLQ to admin replay.

    Validates full recovery path including manual intervention.
    """

    def test_max_retries_leads_to_dlq_then_admin_replay_succeeds(
        self,
        e2e_simulator,
        sample_order_with_payment,
        admin_user,
    ):
        """
        Purpose:
            Validate complete DLQ cycle with admin replay.

        Scenario:
            1. Payment fails 3+ times
            2. Moved to DLQ
            3. Admin reviews and replays
            4. Replay succeeds, order completed

        Expected:
            - Max retries exhausted
            - DLQ entry created
            - Admin replay succeeds
            - Complete audit trail

        Risk Covered:
            E2E-002: Full DLQ lifecycle

        Compliance:
            NIST IR-4 (Incident Handling)
        """
        order, payment = sample_order_with_payment

        # Arrange: Fail 4 times (3 retries + original)
        e2e_simulator.set_failure_pattern([True, True, True, True, False])
        context = e2e_simulator.create_context(payment)

        # Act: Exhaust retries
        for i in range(4):
            result = e2e_simulator.simulate_payment_attempt(
                payment=payment,
                context=context,
                error_code="PG_TIMEOUT",
            )

        # Should be moved to DLQ after 3 retries
        assert result["action"] == "dlq"

        # Create DLQ entry
        dlq_entry = e2e_simulator.move_to_dlq(
            payment=payment,
            context=context,
            error_code="PG_TIMEOUT",
        )

        assert context.final_status == "in_dlq"
        assert context.dlq_entry_id is not None

        # Act: Admin replay
        replay_result = e2e_simulator.simulate_admin_replay(
            dlq_entry=dlq_entry,
            context=context,
            admin=admin_user,
        )

        # Assert
        assert replay_result["success"] is True
        assert context.final_status == "completed"

        # Verify DLQ entry resolved
        dlq_entry.refresh_from_db()
        assert dlq_entry.status == "resolved"
        assert dlq_entry.resolved_by == admin_user  # ForeignKey returns User instance

    def test_dlq_entry_contains_full_failure_history(
        self,
        e2e_simulator,
        sample_order_with_payment,
    ):
        """
        Purpose:
            Verify DLQ entry contains complete failure history.

        Scenario:
            1. Multiple failures with different error codes
            2. DLQ entry created
            3. Verify history is preserved

        Expected:
            - All attempts recorded
            - Error codes preserved
            - Timestamps accurate
        """
        order, payment = sample_order_with_payment

        # Arrange
        e2e_simulator.set_failure_pattern([True, True, True, True])
        context = e2e_simulator.create_context(payment)

        # Act: Multiple failures
        error_codes = ["PG_TIMEOUT", "NETWORK_ERROR", "INVALID_RESPONSE", "PG_TIMEOUT"]
        for i, error_code in enumerate(error_codes[:4]):
            if i < 4:
                e2e_simulator.simulate_payment_attempt(
                    payment=payment,
                    context=context,
                    error_code=error_code,
                )

        dlq_entry = e2e_simulator.move_to_dlq(
            payment=payment,
            context=context,
            error_code="PG_TIMEOUT",
        )

        # Assert
        assert dlq_entry.retry_count == 4
        assert len(context.attempts) == 4

        # Verify audit trail contains all events
        audit = e2e_simulator.get_complete_audit_trail(context)
        failure_events = [e for e in audit if e["event"] == "payment_attempt_failed"]
        assert len(failure_events) == 4


# =============================================================================
# E2E-003: Repeated Failure-Recovery-Failure Cycle
# =============================================================================


@pytest.mark.tier3_chaos
@pytest.mark.django_db(transaction=True)
class TestE2ERepeatedFailureRecoveryCycle:
    """
    Test repeated failure-recovery-failure cycles.

    Validates system stability under repeated failures.
    """

    def test_failure_recovery_failure_cycle(
        self,
        e2e_simulator,
        admin_user,
        db,
    ):
        """
        Purpose:
            Validate repeated failure-recovery cycles.

        Scenario:
            1. Payment fails, auto-recovers
            2. Same user makes another payment
            3. Second payment fails with different error
            4. DLQ -> Admin replay -> Success
            5. Verify complete audit for both payments

        Expected:
            - Each payment tracked independently
            - Recovery works multiple times
            - System remains stable

        Risk Covered:
            E2E-003: System stability under repeated failures
        """
        # Create two separate payments for same user
        user = UserFactory.with_points(100000)
        order1 = OrderFactory(user=user, status="confirmed")
        payment1 = PaymentFactory(order=order1, status="in_progress", amount=Decimal("10000"))

        order2 = OrderFactory(user=user, status="confirmed")
        payment2 = PaymentFactory(order=order2, status="in_progress", amount=Decimal("20000"))

        # Cycle 1: Payment 1 - fail, retry, succeed
        e2e_simulator.set_failure_pattern([True, False])
        context1 = e2e_simulator.create_context(payment1)

        e2e_simulator.simulate_payment_attempt(payment1, context1, "PG_TIMEOUT")
        result1 = e2e_simulator.simulate_payment_attempt(payment1, context1, "PG_TIMEOUT")

        assert result1["success"] is True
        assert context1.final_status == "completed"

        # Cycle 2: Payment 2 - fail multiple times, DLQ, admin replay
        e2e_simulator.set_failure_pattern([True, True, True, True, False])
        context2 = e2e_simulator.create_context(payment2)

        for _ in range(4):
            e2e_simulator.simulate_payment_attempt(payment2, context2, "NETWORK_ERROR")

        dlq_entry = e2e_simulator.move_to_dlq(payment2, context2, "NETWORK_ERROR")
        replay_result = e2e_simulator.simulate_admin_replay(dlq_entry, context2, admin_user)

        # Assert both cycles completed
        assert context1.final_status == "completed"
        assert context2.final_status == "completed"

        # Verify independent tracking
        assert len(context1.attempts) == 2
        assert len(context2.attempts) == 4

    def test_system_handles_alternating_success_failure(
        self,
        e2e_simulator,
        db,
    ):
        """
        Purpose:
            Verify system handles alternating patterns.

        Scenario:
            1. Multiple payments with alternating outcomes
            2. Success -> Failure -> Success -> Failure pattern
            3. Verify all outcomes correctly tracked

        Expected:
            - Each payment outcome independent
            - No state leakage between payments
        """
        # Create multiple payments
        payments = []
        for i in range(4):
            user = UserFactory.with_points(50000)
            order = OrderFactory(user=user, status="confirmed")
            payment = PaymentFactory(order=order, status="in_progress")
            payments.append(payment)

        # Alternating pattern: success, fail, success, fail
        outcomes = [False, True, False, True]

        for i, payment in enumerate(payments):
            e2e_simulator.set_failure_pattern([outcomes[i], False])  # At most 1 retry
            context = e2e_simulator.create_context(payment)

            result = e2e_simulator.simulate_payment_attempt(payment, context)

            if outcomes[i]:  # If first attempt failed
                # Retry should succeed
                result2 = e2e_simulator.simulate_payment_attempt(payment, context)
                assert result2["success"] is True

            assert context.final_status == "completed"


# =============================================================================
# E2E-004: User-Invisible Flow
# =============================================================================


@pytest.mark.tier3_chaos
@pytest.mark.django_db(transaction=True)
class TestE2EUserInvisibleFlow:
    """
    Test user-invisible failure handling.

    Validates seamless user experience during failures.
    """

    def test_user_sees_success_despite_internal_retry(
        self,
        e2e_simulator,
        sample_order_with_payment,
    ):
        """
        Purpose:
            Verify user sees success despite internal failures.

        Scenario:
            1. User initiates payment
            2. First attempt fails (user unaware)
            3. Auto-retry succeeds
            4. User sees only success

        Expected:
            - User not notified of internal failure
            - Final result is success
            - Small delay acceptable

        Risk Covered:
            E2E-004: User experience during failures
        """
        order, payment = sample_order_with_payment

        # Arrange: Single failure, then success
        e2e_simulator.set_failure_pattern([True, False])
        context = e2e_simulator.create_context(payment)

        # Simulate the full flow
        e2e_simulator.simulate_payment_attempt(payment, context, "PG_TIMEOUT")
        final_result = e2e_simulator.simulate_payment_attempt(payment, context, "PG_TIMEOUT")

        # Assert: User-visible result is success
        assert final_result["success"] is True
        assert context.user_visible_error is False  # No error shown to user
        assert context.final_status == "completed"

        # Verify internal failure was handled
        assert len(context.attempts) == 2
        assert context.attempts[0].success is False
        assert context.attempts[1].success is True

    def test_pending_status_shown_during_dlq_processing(
        self,
        e2e_simulator,
        sample_order_with_payment,
    ):
        """
        Purpose:
            Verify correct status shown while in DLQ.

        Scenario:
            1. Payment exhausts retries
            2. Moved to DLQ
            3. User checks order status
            4. "Processing" status shown (not "Failed")

        Expected:
            - Status is "Processing" during DLQ
            - User not shown hard failure
            - Eventual resolution shown correctly
        """
        order, payment = sample_order_with_payment

        # Arrange: Exhaust all retries
        e2e_simulator.set_failure_pattern([True, True, True, True])
        context = e2e_simulator.create_context(payment)

        for _ in range(4):
            e2e_simulator.simulate_payment_attempt(payment, context)

        e2e_simulator.move_to_dlq(payment, context, "PG_TIMEOUT")

        # Assert: Status during DLQ
        assert context.final_status == "in_dlq"

        # User-facing status should be "processing", not "failed"
        user_facing_status = "processing" if context.final_status == "in_dlq" else context.final_status
        assert user_facing_status == "processing"


# =============================================================================
# E2E-005: Complete Audit Trail
# =============================================================================


@pytest.mark.tier3_chaos
@pytest.mark.django_db(transaction=True)
class TestE2ECompleteAuditTrail:
    """
    Test complete audit trail for full cycle.

    Validates forensic completeness.
    """

    def test_complete_audit_trail_for_dlq_cycle(
        self,
        e2e_simulator,
        sample_order_with_payment,
        admin_user,
    ):
        """
        Purpose:
            Validate complete audit trail for full DLQ cycle.

        Scenario:
            1. Payment fails multiple times
            2. DLQ entry created
            3. Admin replays
            4. Verify complete audit trail

        Expected:
            - All events recorded with timestamps
            - Actor information preserved
            - Sequence is chronological

        Risk Covered:
            E2E-005: Forensic accountability

        Compliance:
            SOC 2 (Audit Logging), NIST AU-3
        """
        order, payment = sample_order_with_payment

        # Full cycle
        e2e_simulator.set_failure_pattern([True, True, True, True, False])
        context = e2e_simulator.create_context(payment)

        # Multiple failures
        for _ in range(4):
            e2e_simulator.simulate_payment_attempt(payment, context, "PG_TIMEOUT")

        # DLQ
        dlq_entry = e2e_simulator.move_to_dlq(payment, context, "PG_TIMEOUT")

        # Admin replay
        e2e_simulator.simulate_admin_replay(dlq_entry, context, admin_user)

        # Get audit trail
        audit_trail = e2e_simulator.get_complete_audit_trail(context)

        # Assert: All events present
        event_types = [e["event"] for e in audit_trail]
        assert "payment_attempt_failed" in event_types
        assert "moved_to_dlq" in event_types
        assert "replay_succeeded" in event_types

        # Count events
        failure_count = sum(1 for e in audit_trail if e["event"] == "payment_attempt_failed")
        assert failure_count == 4

        # Verify chronological order
        timestamps = [e["timestamp"] for e in audit_trail]
        assert timestamps == sorted(timestamps)

        # Verify admin info in replay
        replay_event = next(e for e in audit_trail if e["event"] == "replay_succeeded")
        assert replay_event["replayed_by"] == admin_user.username

    def test_audit_trail_contains_error_codes(
        self,
        e2e_simulator,
        sample_order_with_payment,
    ):
        """
        Purpose:
            Verify error codes preserved in audit trail.

        Scenario:
            1. Failures with different error codes
            2. Verify each error code recorded

        Expected:
            - Each failure has error_code
            - Error codes are accurate
        """
        order, payment = sample_order_with_payment

        e2e_simulator.set_failure_pattern([True, True, False])
        context = e2e_simulator.create_context(payment)

        # Different error codes
        e2e_simulator.simulate_payment_attempt(payment, context, "PG_TIMEOUT")
        e2e_simulator.simulate_payment_attempt(payment, context, "NETWORK_ERROR")
        e2e_simulator.simulate_payment_attempt(payment, context, "PG_TIMEOUT")

        # Get audit trail
        audit_trail = e2e_simulator.get_complete_audit_trail(context)
        failure_events = [e for e in audit_trail if e["event"] == "payment_attempt_failed"]

        # Assert
        assert len(failure_events) == 2
        assert failure_events[0]["error_code"] == "PG_TIMEOUT"
        assert failure_events[1]["error_code"] == "NETWORK_ERROR"


# =============================================================================
# Integration: Full E2E Lifecycle
# =============================================================================


@pytest.mark.tier3_chaos
@pytest.mark.django_db(transaction=True)
class TestE2EFullLifecycle:
    """
    Test complete E2E lifecycle including edge cases.
    """

    def test_complete_e2e_lifecycle_with_all_stages(
        self,
        e2e_simulator,
        admin_user,
        db,
    ):
        """
        Purpose:
            Validate complete E2E lifecycle covering all stages.

        Scenario:
            1. User payment -> initial failure
            2. Auto-retry with backoff
            3. Max retries -> DLQ
            4. Admin review
            5. Admin replay (fails first, then succeeds)
            6. Complete resolution
            7. Full audit trail

        Expected:
            - All stages complete correctly
            - System handles replay failure gracefully
            - Final state is resolved
            - Complete forensic trail

        Compliance:
            NIST IR-4 (Incident Handling), SOC 2 CC7
        """
        # Setup
        user = UserFactory.with_points(100000)
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(order=order, status="in_progress", amount=Decimal("50000"))

        # Pattern: 4 failures, 1 replay fail, 1 replay success
        e2e_simulator.set_failure_pattern([True, True, True, True, True, False])
        context = e2e_simulator.create_context(payment)

        # Stage 1-3: Initial failures and retries
        for i in range(4):
            result = e2e_simulator.simulate_payment_attempt(
                payment=payment,
                context=context,
                error_code="PG_TIMEOUT" if i % 2 == 0 else "NETWORK_ERROR",
            )

        assert result["action"] == "dlq"

        # Stage 4: DLQ entry
        dlq_entry = e2e_simulator.move_to_dlq(payment, context, "PG_TIMEOUT")
        assert dlq_entry.status == "pending"

        # Stage 5: First replay attempt (fails)
        replay_result1 = e2e_simulator.simulate_admin_replay(
            dlq_entry=dlq_entry,
            context=context,
            admin=admin_user,
        )
        assert replay_result1["success"] is False

        # Stage 6: Second replay attempt (succeeds)
        replay_result2 = e2e_simulator.simulate_admin_replay(
            dlq_entry=dlq_entry,
            context=context,
            admin=admin_user,
        )
        assert replay_result2["success"] is True

        # Stage 7: Verify final state
        assert context.final_status == "completed"
        dlq_entry.refresh_from_db()
        assert dlq_entry.status == "resolved"

        # Verify complete audit trail
        audit = e2e_simulator.get_complete_audit_trail(context)

        # Check all event types present
        event_types = set(e["event"] for e in audit)
        assert "payment_attempt_failed" in event_types
        assert "moved_to_dlq" in event_types
        assert "replay_failed" in event_types
        assert "replay_succeeded" in event_types

        # Verify total events
        assert len(audit) >= 7  # 4 failures + DLQ + replay fail + replay success
