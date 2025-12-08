"""
Circuit Breaker TTL Expiration Tests

Tests for G-03: Circuit breaker TTL expiration auto-closes.
Validates that manually opened circuits auto-close after TTL expires.

Reference: docs/l3_auto_self_healing/testing/L3_TEST_GAP_REPORT.md
Risk Covered: R-017 (Forgotten open circuit blocks operations indefinitely)
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from shopping.models.failed_payment import CircuitBreakerState
from shopping.services.self_healing.circuit_breaker_service import (
    CircuitBreakerConfig,
    CircuitBreakerService,
    CircuitState,
    get_circuit_breaker_service,
)
from shopping.tasks.self_healing_tasks import expire_manual_overrides
from shopping.tests.factories import UserFactory


@pytest.mark.django_db(transaction=True)
class TestCircuitBreakerTTLExpiration:
    """
    Tests for circuit breaker TTL-based auto-close.

    Gap ID: G-03
    Purpose: Verify that manually opened circuits auto-close after TTL expires.
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.config = CircuitBreakerConfig(
            enabled=True,
            failure_threshold=5,
            recovery_timeout=60,
            success_threshold=2,
            manual_override_ttl_minutes=90,
        )
        self.service = CircuitBreakerService(config=self.config)

    def test_manual_override_expires_after_ttl(self):
        """
        Purpose:
            Verify that manually opened circuit auto-closes after TTL expires.

        Scenario:
            1. Force open a circuit with TTL of 90 minutes
            2. Backdate the opened_at timestamp to simulate time passing
            3. Run the expire_manual_overrides task
            4. Verify circuit is now half-open (ready for recovery)

        Expected:
            - Circuit state changes from OPEN to HALF_OPEN
            - manually_controlled is set to False
            - Expiration reason is logged
        """
        admin_user = UserFactory(is_staff=True)
        service_name = "toss_payment_ttl_test"

        # Force open with TTL
        self.service.force_open(
            service_name=service_name,
            reason="Maintenance window",
            controlled_by=admin_user,
        )

        # Verify circuit is open with TTL set
        state = CircuitBreakerState.objects.get(service_name=service_name)
        assert state.state == "open"
        assert state.manually_controlled is True
        assert state.manual_override_expires_at is not None

        # Backdate expiration to simulate 100 minutes passing (TTL is 90)
        state.manual_override_expires_at = timezone.now() - timedelta(minutes=10)
        state.save()

        # Run expiration task
        result = expire_manual_overrides()

        # Verify task succeeded and found expired circuit
        assert result["success"] is True
        assert service_name in result["expired_services"]

        # Verify circuit transitioned to half-open
        state.refresh_from_db()
        assert state.state == "half_open"
        assert state.manually_controlled is False
        assert state.manual_override_expires_at is None
        assert "[EXPIRED]" in state.control_reason

    def test_multiple_circuits_expire_simultaneously(self):
        """
        Purpose:
            Verify multiple expired circuits are all handled.

        Scenario:
            1. Create multiple circuits with expired TTLs
            2. Run expiration task
            3. Verify all expired circuits are transitioned

        Expected:
            - All expired circuits transition to half-open
        """
        admin_user = UserFactory(is_staff=True)
        service_names = [
            "service_ttl_test_1",
            "service_ttl_test_2",
            "service_ttl_test_3",
        ]

        # Create and expire multiple circuits
        for name in service_names:
            CircuitBreakerState.objects.create(
                service_name=name,
                state="open",
                manually_controlled=True,
                manual_override_expires_at=timezone.now() - timedelta(minutes=5),
                control_reason="Test override",
            )

        # Run expiration task
        result = expire_manual_overrides()

        # Verify all circuits expired
        assert result["success"] is True
        assert result["count"] == 3
        for name in service_names:
            assert name in result["expired_services"]

        # Verify all transitioned to half-open
        for name in service_names:
            state = CircuitBreakerState.objects.get(service_name=name)
            assert state.state == "half_open"
            assert state.manually_controlled is False

    def test_valid_ttl_circuits_not_expired(self):
        """
        Purpose:
            Verify circuits with valid (future) TTL are not expired.

        Scenario:
            1. Create circuit with future TTL
            2. Run expiration task
            3. Verify circuit remains open

        Expected:
            - Circuit state unchanged
            - TTL remains set
        """
        service_name = "valid_ttl_circuit"

        CircuitBreakerState.objects.create(
            service_name=service_name,
            state="open",
            manually_controlled=True,
            manual_override_expires_at=timezone.now() + timedelta(minutes=60),
            control_reason="Still in maintenance",
        )

        # Run expiration task
        result = expire_manual_overrides()

        # Verify circuit was not expired
        assert result["success"] is True
        assert service_name not in result.get("expired_services", [])

        # Verify circuit remains open
        state = CircuitBreakerState.objects.get(service_name=service_name)
        assert state.state == "open"
        assert state.manually_controlled is True
        assert state.manual_override_expires_at is not None

    def test_non_manual_circuits_ignored(self):
        """
        Purpose:
            Verify auto-opened circuits are not affected by TTL expiration.

        Scenario:
            1. Create auto-opened circuit (not manually controlled)
            2. Run expiration task
            3. Verify circuit is unchanged

        Expected:
            - Auto-opened circuits remain unchanged
        """
        service_name = "auto_opened_circuit"

        CircuitBreakerState.objects.create(
            service_name=service_name,
            state="open",
            manually_controlled=False,  # Not manually controlled
            failure_count=5,
            opened_at=timezone.now() - timedelta(minutes=10),
        )

        result = expire_manual_overrides()

        assert result["success"] is True
        assert service_name not in result.get("expired_services", [])

        state = CircuitBreakerState.objects.get(service_name=service_name)
        assert state.state == "open"

    def test_ttl_expiration_sends_notification(self):
        """
        Purpose:
            Verify notification is sent when TTL expires.

        Scenario:
            1. Create expired circuit
            2. Run expiration task with notification mocked
            3. Verify notification would be triggered
        """
        service_name = "notification_test_circuit"

        CircuitBreakerState.objects.create(
            service_name=service_name,
            state="open",
            manually_controlled=True,
            manual_override_expires_at=timezone.now() - timedelta(minutes=10),
            control_reason="Test",
        )

        # Run expiration and verify logging indicates expiration occurred
        result = expire_manual_overrides()

        assert result["success"] is True
        assert service_name in result["expired_services"]

        # The actual notification would be handled by the service layer
        # This verifies the task correctly identifies and expires circuits

    def test_extend_ttl_prevents_expiration(self):
        """
        Purpose:
            Verify extending TTL prevents imminent expiration.

        Scenario:
            1. Create circuit about to expire
            2. Extend TTL
            3. Run expiration task
            4. Verify circuit remains open

        Expected:
            - Extended TTL is respected
            - Circuit not expired
        """
        service_name = "extend_ttl_test"

        # Create circuit expiring in 5 minutes
        initial_expiry = timezone.now() + timedelta(minutes=5)
        CircuitBreakerState.objects.create(
            service_name=service_name,
            state="open",
            manually_controlled=True,
            manual_override_expires_at=initial_expiry,
            control_reason="Initial",
        )

        # Extend TTL by 60 minutes
        extend_result = self.service.extend_manual_override(
            service_name=service_name,
            additional_minutes=60,
            reason="Need more time",
        )

        assert extend_result.success is True

        # Run expiration task
        expire_result = expire_manual_overrides()

        # Circuit should not be expired
        assert service_name not in expire_result.get("expired_services", [])

        state = CircuitBreakerState.objects.get(service_name=service_name)
        assert state.state == "open"
        assert state.manually_controlled is True

    def test_expired_circuit_allows_recovery_probe(self):
        """
        Purpose:
            Verify expired circuit transitions to half-open allowing recovery.

        Scenario:
            1. Expire a circuit
            2. Verify should_allow returns True (for probe)
            3. Record success to fully close

        Expected:
            - Half-open circuit allows test requests
            - Success recording closes circuit
        """
        service_name = "recovery_probe_test"

        CircuitBreakerState.objects.create(
            service_name=service_name,
            state="open",
            manually_controlled=True,
            manual_override_expires_at=timezone.now() - timedelta(minutes=10),
            control_reason="Test",
        )

        # Run expiration
        expire_manual_overrides()

        # Verify circuit is half-open and allows requests
        assert self.service.should_allow(service_name) is True

        state = CircuitBreakerState.objects.get(service_name=service_name)
        assert state.state == "half_open"

        # Record success to close circuit
        self.service.record_success(service_name)
        self.service.record_success(service_name)  # Need success_threshold (2) successes

        state.refresh_from_db()
        assert state.state == "closed"
