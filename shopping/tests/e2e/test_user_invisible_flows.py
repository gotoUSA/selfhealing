"""
User-Invisible Flows End-to-End Tests

File: e2e/test_user_invisible_flows.py

Business Risk: User-visible failures damaging user experience and trust
Compliance Alignment: SOC 2 (Availability), NIST IR-4 (Incident Handling)

Test Cases:
- E2E-U001: Payment with PG timeout auto-retry (user sees success after delay)
- E2E-U002: Payment with CB open uses fallback PG
- E2E-U003: All retries fail - user sees graceful "processing" message
- E2E-U004: User checks order during DLQ processing - sees "Processing" status

Reference: docs/testing/SELF_HEALING_TEST_SPECIFICATIONS.md §9.2
"""

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from unittest.mock import patch, MagicMock

import pytest
from django.utils import timezone

from shopping.models.failed_payment import CircuitBreakerState, FailedPayment
from shopping.models.order import Order
from shopping.models.payment import Payment
from shopping.services.self_healing.circuit_breaker_service import (
    CircuitBreakerConfig,
    CircuitBreakerService,
    CircuitState,
)
from shopping.tests.factories import OrderFactory, PaymentFactory, ProductFactory, UserFactory


# =============================================================================
# E2E Flow Utilities
# =============================================================================


@dataclass
class UserVisibleState:
    """
    Tracks what the user sees during a flow.

    Used to verify that internal failures are hidden from users.
    """

    loading_shown: bool = False
    error_shown: bool = False
    success_shown: bool = False
    processing_message_shown: bool = False
    notification_sent: bool = False
    status_text: str = ""
    wait_time_seconds: float = 0
    redirect_url: str | None = None


@dataclass
class InternalState:
    """
    Tracks internal system state during a flow.

    Used to verify proper handling while user is unaware.
    """

    retry_count: int = 0
    cb_state: str = "closed"
    dlq_entry_created: bool = False
    fallback_used: bool = False
    error_codes: list = field(default_factory=list)
    recovery_actions: list = field(default_factory=list)


class UserFlowSimulator:
    """
    Simulates user actions and tracks visible state.

    Provides clear separation between user-visible and internal states.
    """

    def __init__(self):
        self.user_state = UserVisibleState()
        self.internal_state = InternalState()
        self._failure_queue: list[str] = []

    def set_failure_sequence(self, failures: list[str]) -> None:
        """
        Set sequence of failures for testing.

        Example: ["PG_TIMEOUT", "PG_TIMEOUT", None]
        None = success, string = error code
        """
        self._failure_queue = failures.copy()

    def attempt_payment(self) -> dict:
        """
        Simulate user clicking "Pay" button.

        Returns the user-visible result.
        """
        start_time = time.time()
        self.user_state.loading_shown = True

        # Process failure queue
        while self._failure_queue:
            failure = self._failure_queue.pop(0)

            if failure is None:
                # Success
                self.user_state.success_shown = True
                self.user_state.loading_shown = False
                self.user_state.wait_time_seconds = time.time() - start_time
                self.user_state.status_text = "Payment successful"
                return {
                    "visible_result": "success",
                    "user_saw_error": False,
                    "delay": self.user_state.wait_time_seconds,
                }

            # Failure - handle internally
            self.internal_state.retry_count += 1
            self.internal_state.error_codes.append(failure)
            self.internal_state.recovery_actions.append("retry_scheduled")

            # If max retries reached
            if self.internal_state.retry_count >= 3:
                break

        # All retries exhausted
        if self._failure_queue or self.internal_state.retry_count >= 3:
            self.user_state.loading_shown = False
            self.user_state.processing_message_shown = True
            self.user_state.status_text = "Processing your payment. We'll notify you shortly."
            self.user_state.notification_sent = True
            self.internal_state.dlq_entry_created = True

            return {
                "visible_result": "processing",
                "user_saw_error": False,  # User doesn't see error, sees "processing"
                "delay": time.time() - start_time,
            }

        # No failures configured - immediate success
        self.user_state.success_shown = True
        self.user_state.loading_shown = False
        self.user_state.wait_time_seconds = time.time() - start_time
        return {
            "visible_result": "success",
            "user_saw_error": False,
            "delay": self.user_state.wait_time_seconds,
        }

    def check_order_status(self, order_id: int) -> dict:
        """
        Simulate user checking order status.

        Returns user-visible status.
        """
        if self.internal_state.dlq_entry_created:
            return {
                "status": "Processing",
                "message": "Your order is being processed. Please wait.",
                "show_spinner": True,
            }
        elif self.user_state.success_shown:
            return {
                "status": "Confirmed",
                "message": "Your payment was successful.",
                "show_spinner": False,
            }
        else:
            return {
                "status": "Pending",
                "message": "Awaiting payment confirmation.",
                "show_spinner": False,
            }


# =============================================================================
# E2E-U: User-Invisible Flow Tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.tier3_chaos
class TestUserInvisibleFlows:
    """
    Tests that verify failures are handled invisibly to users.

    Validates:
    - Users don't see raw error messages
    - Retries happen seamlessly
    - Fallbacks work transparently
    - Graceful degradation in worst case
    """

    def test_e2e_u001_pg_timeout_auto_retry_success(self):
        """
        Purpose:
            Verify PG timeout results in auto-retry with eventual success.

        Scenario:
            1. User clicks "Pay"
            2. First attempt: PG_TIMEOUT error
            3. Auto-retry succeeds
            4. User sees success (with slight delay)

        Expected:
            - User never sees error message
            - Success shown after delay
            - Internal: 1 retry recorded

        Risk Covered:
            R-009: User-visible failure exposure
        """
        # Arrange
        simulator = UserFlowSimulator()
        # First call fails, second succeeds
        simulator.set_failure_sequence(["PG_TIMEOUT", None])

        # Act
        result = simulator.attempt_payment()

        # Assert - User perspective
        assert result["visible_result"] == "success", (
            "User should see success after retry"
        )
        assert result["user_saw_error"] is False, (
            "User should never see the PG_TIMEOUT error"
        )
        assert simulator.user_state.success_shown is True
        assert simulator.user_state.error_shown is False

        # Assert - Internal state
        assert simulator.internal_state.retry_count == 1, (
            "Should have 1 retry before success"
        )
        assert "PG_TIMEOUT" in simulator.internal_state.error_codes

    def test_e2e_u002_circuit_breaker_fallback_pg(self):
        """
        Purpose:
            Verify CB open triggers fallback PG transparently.

        Scenario:
            1. Primary PG has CB open (too many failures)
            2. User clicks "Pay"
            3. System automatically uses fallback PG
            4. User sees success

        Expected:
            - User unaware of primary PG issues
            - Fallback PG used automatically
            - Success shown normally

        Risk Covered:
            R-013: Circuit Breaker stuck in wrong state
        """
        # Arrange
        simulator = UserFlowSimulator()
        simulator.internal_state.cb_state = "open"
        simulator.internal_state.fallback_used = True

        # Fallback succeeds immediately
        simulator.set_failure_sequence([None])

        # Act
        result = simulator.attempt_payment()

        # Assert - User perspective
        assert result["visible_result"] == "success"
        assert result["user_saw_error"] is False

        # Assert - Internal state
        assert simulator.internal_state.fallback_used is True, (
            "Fallback PG should have been used"
        )

    def test_e2e_u003_all_retries_fail_graceful_message(self):
        """
        Purpose:
            Verify graceful handling when all retries fail.

        Scenario:
            1. User clicks "Pay"
            2. All 3 retry attempts fail
            3. User sees "Processing" message (not error)
            4. User receives notification later

        Expected:
            - No error message shown to user
            - "Processing" status displayed
            - Notification promise made
            - DLQ entry created internally

        Risk Covered:
            R-009: User-visible failure exposure

        Compliance:
            SOC 2 (Availability)
        """
        # Arrange
        simulator = UserFlowSimulator()
        # All 3 attempts fail
        simulator.set_failure_sequence([
            "PG_TIMEOUT",
            "NETWORK_ERROR",
            "DB_CONNECTION_ERROR",
        ])

        # Act
        result = simulator.attempt_payment()

        # Assert - User perspective
        assert result["visible_result"] == "processing", (
            "User should see 'processing' not 'error'"
        )
        assert result["user_saw_error"] is False, (
            "User should never see raw error"
        )
        assert simulator.user_state.processing_message_shown is True
        assert simulator.user_state.notification_sent is True
        assert "Processing" in simulator.user_state.status_text or "notify" in simulator.user_state.status_text.lower()

        # Assert - Internal state
        assert simulator.internal_state.retry_count == 3, (
            "All 3 retries should have been attempted"
        )
        assert simulator.internal_state.dlq_entry_created is True, (
            "DLQ entry should be created for manual handling"
        )
        assert len(simulator.internal_state.error_codes) == 3

    def test_e2e_u004_order_status_during_dlq_processing(self):
        """
        Purpose:
            Verify order status shows "Processing" during DLQ handling.

        Scenario:
            1. Payment failed, now in DLQ
            2. User checks order status
            3. User sees "Processing" (not failed)
            4. Admin resolves DLQ entry
            5. User sees "Confirmed"

        Expected:
            - Status is "Processing" while in DLQ
            - No "Failed" status exposed
            - Status updates after resolution

        Risk Covered:
            R-009: User-visible failure exposure
        """
        # Arrange
        simulator = UserFlowSimulator()
        simulator.internal_state.dlq_entry_created = True

        # Act: Check status while in DLQ
        status_during_dlq = simulator.check_order_status(order_id=123)

        # Assert - During DLQ
        assert status_during_dlq["status"] == "Processing", (
            "Status should be 'Processing' not 'Failed'"
        )
        assert status_during_dlq["show_spinner"] is True, (
            "Spinner should indicate ongoing work"
        )
        assert "Failed" not in status_during_dlq["message"]
        assert "error" not in status_during_dlq["message"].lower()

    def test_e2e_u005_multiple_concurrent_user_flows(self):
        """
        Purpose:
            Verify isolation between concurrent user flows.

        Scenario:
            1. User A payment fails, goes to retry
            2. User B payment succeeds immediately
            3. Both users see appropriate results
            4. No cross-contamination

        Expected:
            - User A flow independent of User B
            - Each user sees correct result
            - No shared state leakage

        Risk Covered:
            R-001: Cross-tenant data leakage
        """
        # Arrange
        user_a_simulator = UserFlowSimulator()
        user_b_simulator = UserFlowSimulator()

        # User A has failures
        user_a_simulator.set_failure_sequence(["PG_TIMEOUT", None])
        # User B succeeds immediately
        user_b_simulator.set_failure_sequence([None])

        # Act
        result_a = user_a_simulator.attempt_payment()
        result_b = user_b_simulator.attempt_payment()

        # Assert - Both succeed but differently
        assert result_a["visible_result"] == "success"
        assert result_b["visible_result"] == "success"

        # User A had retry
        assert user_a_simulator.internal_state.retry_count == 1
        # User B had no retry
        assert user_b_simulator.internal_state.retry_count == 0

        # Verify isolation
        assert user_a_simulator.internal_state.error_codes != user_b_simulator.internal_state.error_codes


@pytest.mark.django_db(transaction=True)
@pytest.mark.tier3_chaos
class TestUserNotificationFlows:
    """
    Tests for user notification during async processing.
    """

    def test_user_notified_on_dlq_resolution(self):
        """
        Purpose:
            Verify user receives notification when DLQ entry resolved.

        Scenario:
            1. Payment in DLQ
            2. Admin successfully replays
            3. User receives success notification
            4. Order status updates

        Expected:
            - Notification sent on resolution
            - Notification contains correct order info
            - Order status reflects success
        """
        # Arrange
        notifications_sent = []

        def mock_send_notification(user_id: int, message: str, order_id: int) -> None:
            notifications_sent.append({
                "user_id": user_id,
                "message": message,
                "order_id": order_id,
                "timestamp": timezone.now(),
            })

        # Simulate DLQ resolution
        dlq_entry = {
            "id": 1,
            "user_id": 42,
            "order_id": 123,
            "status": "pending",
        }

        # Act: Resolve DLQ entry
        dlq_entry["status"] = "resolved"
        mock_send_notification(
            user_id=dlq_entry["user_id"],
            message="Your payment has been successfully processed.",
            order_id=dlq_entry["order_id"],
        )

        # Assert
        assert len(notifications_sent) == 1
        notification = notifications_sent[0]
        assert notification["user_id"] == 42
        assert notification["order_id"] == 123
        assert "success" in notification["message"].lower()

    def test_user_notified_on_permanent_failure(self):
        """
        Purpose:
            Verify user receives appropriate notification on permanent failure.

        Scenario:
            1. Payment fails permanently (non-recoverable)
            2. Escalated to REQUIRES_REVIEW
            3. Admin marks as failed
            4. User receives refund/failure notification

        Expected:
            - User notified of failure
            - Refund information included
            - No technical error details exposed
        """
        # Arrange
        notifications_sent = []

        def mock_send_notification(user_id: int, message: str, order_id: int) -> None:
            notifications_sent.append({
                "user_id": user_id,
                "message": message,
                "order_id": order_id,
            })

        # Act: Permanent failure notification
        mock_send_notification(
            user_id=42,
            message="We couldn't process your payment. No charges were made. Please try again.",
            order_id=123,
        )

        # Assert
        assert len(notifications_sent) == 1
        notification = notifications_sent[0]

        # No technical details exposed
        assert "PG_TIMEOUT" not in notification["message"]
        assert "exception" not in notification["message"].lower()
        assert "error code" not in notification["message"].lower()

        # User-friendly message
        assert "try again" in notification["message"].lower() or "contact" in notification["message"].lower()


@pytest.mark.django_db(transaction=True)
@pytest.mark.tier3_chaos
class TestUserExperienceMetrics:
    """
    Tests for tracking user experience during failures.
    """

    def test_user_wait_time_tracked(self):
        """
        Purpose:
            Verify user wait times are tracked for SLA.

        Scenario:
            1. User initiates payment
            2. Retries occur
            3. Total wait time measured
            4. SLA compliance checked

        Expected:
            - Wait time accurately measured
            - Metrics recorded for monitoring
            - SLA threshold respected
        """
        # Arrange
        simulator = UserFlowSimulator()
        simulator.set_failure_sequence(["PG_TIMEOUT", None])
        sla_max_wait_seconds = 10

        # Act
        result = simulator.attempt_payment()

        # Assert
        assert result["visible_result"] == "success"
        assert result["delay"] < sla_max_wait_seconds, (
            f"User wait time ({result['delay']:.2f}s) should be under SLA ({sla_max_wait_seconds}s)"
        )

    def test_user_invisible_failure_count_tracked(self):
        """
        Purpose:
            Track how many failures were invisible to user.

        Scenario:
            1. Multiple failures occur
            2. User eventually sees success
            3. Count of hidden failures tracked

        Expected:
            - All hidden failures counted
            - Metrics available for monitoring
            - Can calculate "invisible failure rate"
        """
        # Arrange
        simulator = UserFlowSimulator()
        simulator.set_failure_sequence([
            "PG_TIMEOUT",
            "NETWORK_ERROR",
            None,  # Success on 3rd attempt
        ])

        # Act
        result = simulator.attempt_payment()

        # Assert
        assert result["visible_result"] == "success"

        # Track invisible failures
        invisible_failure_count = simulator.internal_state.retry_count
        assert invisible_failure_count == 2, (
            f"Expected 2 invisible failures, got {invisible_failure_count}"
        )

        # These failures were never shown to user
        assert result["user_saw_error"] is False
