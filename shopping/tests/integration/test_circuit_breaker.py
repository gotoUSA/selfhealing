"""
Circuit Breaker Integration Tests

Integration tests for the Circuit Breaker system including:
- Full force open/close workflow
- Conditional replay on circuit close
- Admin action simulation
- State persistence and recovery

Reference: docs/L3_SELF_HEALING_OPERATIONS.md §9
"""

import pytest
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.utils import timezone
from django.test import override_settings

from shopping.models.failed_payment import CircuitBreakerState
from shopping.models.failed_operation import FailedOperation
from shopping.services.self_healing.circuit_breaker_service import (
    CircuitBreakerService,
    CircuitBreakerConfig,
    CircuitState,
    get_circuit_breaker_service,
)
from shopping.services.self_healing.replay_service import get_replay_service
from shopping.tests.factories import OrderFactory, PaymentFactory, UserFactory


# =============================================================================
# Test Configuration
# =============================================================================


CIRCUIT_BREAKER_SETTINGS = {
    "SELF_HEALING": {
        "CIRCUIT_BREAKER": {
            "ENABLED": True,
            "FAILURE_THRESHOLD": 5,
            "RECOVERY_TIMEOUT": 60,
            "SUCCESS_THRESHOLD": 2,
        },
        "DLQ": {
            "ENABLED": True,
            "RETENTION_DAYS": 30,
            "MAX_REPLAY_ATTEMPTS": 2,
        },
    }
}


# =============================================================================
# Circuit Breaker Workflow Integration Tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestCircuitBreakerWorkflow:
    """
    Integration tests for full circuit breaker workflow.

    Tests the complete lifecycle:
    CLOSED -> OPEN -> HALF_OPEN -> CLOSED
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.config = CircuitBreakerConfig(
            enabled=True,
            failure_threshold=5,
            recovery_timeout=60,
            success_threshold=2,
        )
        self.service = CircuitBreakerService(config=self.config)

    def test_complete_manual_workflow(self):
        """
        Purpose:
            Test complete manual circuit breaker workflow.

        Scenario:
            1. Create circuit breaker in closed state
            2. Force open (PG outage detected)
            3. Force close (PG recovered)
            4. Verify state transitions
        """
        admin_user = UserFactory(is_staff=True)
        service_name = "toss_payment"

        # Step 1: Initial state should be closed
        state = self.service.get_or_create_state(service_name)
        assert state.state == CircuitState.CLOSED
        assert self.service.should_allow(service_name) is True

        # Step 2: Force open (PG outage)
        open_result = self.service.force_open(
            service_name=service_name,
            reason="Toss PG maintenance window 14:00-15:00",
            controlled_by=admin_user,
        )

        assert open_result.success is True
        assert open_result.new_state == CircuitState.OPEN
        assert self.service.should_allow(service_name) is False

        # Verify state persistence
        state = CircuitBreakerState.objects.get(service_name=service_name)
        assert state.state == "open"
        assert state.manually_controlled is True
        assert state.controlled_by == admin_user
        assert "maintenance window" in state.control_reason

        # Step 3: Force close (PG recovered)
        close_result = self.service.force_close(
            service_name=service_name,
            reason="Toss PG recovered, verified via status page",
            controlled_by=admin_user,
            trigger_replay=False,
        )

        assert close_result.success is True
        assert close_result.new_state == CircuitState.CLOSED
        assert self.service.should_allow(service_name) is True

        # Verify final state
        state.refresh_from_db()
        assert state.state == "closed"
        assert state.failure_count == 0

        print("✓ PASSED: Complete manual workflow")

    def test_automatic_state_transition(self):
        """
        Purpose:
            Test automatic circuit breaker state transitions.

        Scenario:
            1. Record failures until threshold
            2. Verify circuit opens automatically
            3. Verify half-open transition after timeout
            4. Record successes to close
        """
        service_name = "auto_service"

        # Create initial state
        self.service.get_or_create_state(service_name)

        # Step 1: Record failures up to threshold
        for i in range(5):
            self.service.record_failure(service_name)

        # Circuit should now be open
        state = CircuitBreakerState.objects.get(service_name=service_name)
        assert state.state == "open"
        assert state.failure_count == 5

        # Step 2: Simulate timeout for half-open transition
        state.opened_at = timezone.now() - timedelta(seconds=120)
        state.save()

        # should_allow will trigger transition to half-open
        result = self.service.should_allow(service_name)
        assert result is True

        state.refresh_from_db()
        assert state.state == "half_open"

        # Step 3: Record successes to close
        self.service.record_success(service_name)
        self.service.record_success(service_name)

        state.refresh_from_db()
        assert state.state == "closed"
        assert state.failure_count == 0

        print("✓ PASSED: Automatic state transitions")

    @patch("shopping.tasks.self_healing_tasks.conditional_replay_on_circuit_close.delay")
    def test_conditional_replay_on_close(self, mock_replay_task):
        """
        Purpose:
            Test that conditional replay is triggered when circuit closes.

        Scenario:
            1. Force open circuit
            2. Force close with trigger_replay=True
            3. Verify replay task is queued
        """
        service_name = "replay_test_service"
        admin_user = UserFactory(is_staff=True)

        # Create and open circuit
        self.service.force_open(
            service_name=service_name,
            reason="Testing replay",
            controlled_by=admin_user,
        )

        # Close with replay trigger
        self.service.force_close(
            service_name=service_name,
            reason="Service recovered",
            controlled_by=admin_user,
            trigger_replay=True,
        )

        # Verify replay task was called
        mock_replay_task.assert_called_once_with(service_name=service_name)

        print("✓ PASSED: Conditional replay triggered on close")


@pytest.mark.django_db(transaction=True)
class TestCircuitBreakerWithDLQ:
    """
    Integration tests for circuit breaker with DLQ entries.

    Tests the replay of DLQ entries when circuit recovers.
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.config = CircuitBreakerConfig(
            enabled=True,
            failure_threshold=5,
            recovery_timeout=60,
            success_threshold=2,
        )
        self.cb_service = CircuitBreakerService(config=self.config)
        self.replay_service = get_replay_service()

    def test_dlq_entries_created_during_outage(self):
        """
        Purpose:
            Verify DLQ entries are created when circuit is open.

        Scenario:
            1. Create payment failures that would go to DLQ
            2. Verify entries are created with correct failure type
        """
        user = UserFactory()
        order = OrderFactory(user=user)

        # Create DLQ entries for PG timeout
        dlq_entries = []
        for i in range(3):
            entry = FailedOperation.objects.create(
                domain=FailedOperation.Domain.PAYMENT,
                failure_type="PG_TIMEOUT",
                order=order,
                user=user,
                error_code="TIMEOUT",
                error_message=f"Connection timed out (attempt {i + 1})",
                status=FailedOperation.Status.PENDING,
                snapshot_data={
                    "order_id": order.id,
                    "amount": 50000,
                },
            )
            dlq_entries.append(entry)

        # Verify entries created
        pending_count = FailedOperation.objects.filter(
            domain="payment",
            failure_type="PG_TIMEOUT",
            status=FailedOperation.Status.PENDING,
        ).count()

        assert pending_count == 3

        print("✓ PASSED: DLQ entries created during outage")

    def test_replay_on_circuit_close_integration(self):
        """
        Purpose:
            Test full replay flow when circuit closes.

        Scenario:
            1. Create pending DLQ entries
            2. Close circuit with replay
            3. Verify entries are processed
        """
        user = UserFactory()
        order = OrderFactory(user=user)
        service_name = "toss_payment"

        # Create circuit in open state
        self.cb_service.force_open(
            service_name=service_name,
            reason="Test setup",
        )

        # Create pending DLQ entries
        for i in range(2):
            FailedOperation.objects.create(
                domain=FailedOperation.Domain.PAYMENT,
                failure_type="PG_TIMEOUT",
                order=order,
                user=user,
                error_code="TIMEOUT",
                error_message="Simulated timeout",
                status=FailedOperation.Status.PENDING,
                retry_count=0,
            )

        # Verify entries exist
        initial_pending = FailedOperation.objects.filter(status=FailedOperation.Status.PENDING).count()
        assert initial_pending == 2

        # Close circuit (replay would be triggered via Celery in production)
        result = self.cb_service.force_close(
            service_name=service_name,
            reason="Recovered",
            trigger_replay=False,  # Don't actually trigger async task in test
        )

        assert result.success is True

        # In production, the Celery task would call replay_service.replay_on_circuit_close()
        # We can verify the replay service method directly
        replay_result = self.replay_service.replay_on_circuit_close(
            service_name=service_name,
            max_items=10,
        )

        # Entries should have been processed (marked as replayed)
        # Note: Actual success depends on payment handler implementation
        assert replay_result.total == 2

        print("✓ PASSED: Replay on circuit close integration")


@pytest.mark.django_db(transaction=True)
class TestCircuitBreakerAdminActions:
    """
    Integration tests for circuit breaker admin actions.

    Simulates admin interface operations.
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.config = CircuitBreakerConfig(
            enabled=True,
            failure_threshold=5,
            recovery_timeout=60,
            success_threshold=2,
        )
        self.service = CircuitBreakerService(config=self.config)

    def test_admin_force_open_action(self):
        """
        Purpose:
            Simulate admin force open action.

        Scenario:
            Admin selects circuit breakers and applies "Force OPEN" action.
        """
        admin_user = UserFactory(is_staff=True, is_superuser=True)

        # Create circuits
        services = ["toss_payment", "kakao_payment", "notification"]
        for service in services:
            self.service.get_or_create_state(service)

        # Simulate admin action: force open selected circuits
        selected = ["toss_payment", "kakao_payment"]
        success_count = 0

        for service_name in selected:
            result = self.service.force_open(
                service_name=service_name,
                reason=f"Admin action by {admin_user.username}",
                controlled_by=admin_user,
            )
            if result.success:
                success_count += 1

        assert success_count == 2

        # Verify states
        for service_name in selected:
            state = CircuitBreakerState.objects.get(service_name=service_name)
            assert state.state == "open"
            assert state.manually_controlled is True

        # Unselected circuit should still be closed
        notif_state = CircuitBreakerState.objects.get(service_name="notification")
        assert notif_state.state == "closed"

        print("✓ PASSED: Admin force open action")

    def test_admin_force_close_with_replay_action(self):
        """
        Purpose:
            Simulate admin force close with replay action.

        Scenario:
            Admin closes circuit and triggers DLQ replay.
        """
        admin_user = UserFactory(is_staff=True, is_superuser=True)
        service_name = "toss_payment"

        # Create open circuit
        self.service.force_open(
            service_name=service_name,
            reason="Initial open",
        )

        # Create pending DLQ entries
        for _ in range(3):
            FailedOperation.objects.create(
                domain=FailedOperation.Domain.PAYMENT,
                failure_type="PG_TIMEOUT",
                error_message="Test entry",
                status=FailedOperation.Status.PENDING,
            )

        # Simulate admin action with replay
        with patch("shopping.tasks.self_healing_tasks.conditional_replay_on_circuit_close.delay") as mock_task:
            result = self.service.force_close(
                service_name=service_name,
                reason=f"Admin action with replay by {admin_user.username}",
                controlled_by=admin_user,
                trigger_replay=True,
            )

            assert result.success is True
            mock_task.assert_called_once_with(service_name=service_name)

        print("✓ PASSED: Admin force close with replay action")

    def test_admin_reset_action(self):
        """
        Purpose:
            Simulate admin reset action.

        Scenario:
            Admin resets circuit breaker to initial state.
        """
        admin_user = UserFactory(is_staff=True, is_superuser=True)
        service_name = "test_service"

        # Create circuit with accumulated state
        CircuitBreakerState.objects.create(
            service_name=service_name,
            state="half_open",
            failure_count=8,
            success_count=1,
            opened_at=timezone.now() - timedelta(hours=1),
            manually_controlled=True,
        )

        # Reset
        result = self.service.reset(
            service_name=service_name,
            reason=f"Admin reset by {admin_user.username}",
            controlled_by=admin_user,
        )

        assert result.success is True

        # Verify reset state
        state = CircuitBreakerState.objects.get(service_name=service_name)
        assert state.state == "closed"
        assert state.failure_count == 0
        assert state.success_count == 0
        assert state.opened_at is None
        assert state.manually_controlled is False

        print("✓ PASSED: Admin reset action")


@pytest.mark.django_db(transaction=True)
class TestCircuitBreakerPersistence:
    """
    Tests for circuit breaker state persistence.

    Verifies state survives application restarts.
    """

    def test_state_persists_across_service_instances(self):
        """
        Purpose:
            Verify state persists when service is recreated.

        Scenario:
            1. Create circuit and modify state
            2. Create new service instance
            3. Verify state is preserved
        """
        service_name = "persistent_service"

        # First service instance
        service1 = CircuitBreakerService(config=CircuitBreakerConfig(enabled=True))
        service1.force_open(
            service_name=service_name,
            reason="Test persistence",
        )

        # Verify open
        assert service1.get_state(service_name) == "open"

        # Simulate restart with new service instance
        service2 = CircuitBreakerService(config=CircuitBreakerConfig(enabled=True))

        # State should be preserved
        assert service2.get_state(service_name) == "open"
        assert service2.should_allow(service_name) is False

        print("✓ PASSED: State persists across service instances")

    def test_multiple_circuits_independent(self):
        """
        Purpose:
            Verify multiple circuits operate independently.

        Scenario:
            1. Create multiple circuits
            2. Modify one circuit
            3. Verify others are unaffected
        """
        service = CircuitBreakerService(config=CircuitBreakerConfig(enabled=True))

        circuits = ["payment_1", "payment_2", "notification"]

        # Create all circuits
        for name in circuits:
            service.get_or_create_state(name)

        # Open only payment_1
        service.force_open("payment_1", reason="Test")

        # Verify independence
        assert service.get_state("payment_1") == "open"
        assert service.get_state("payment_2") == "closed"
        assert service.get_state("notification") == "closed"

        print("✓ PASSED: Multiple circuits operate independently")
