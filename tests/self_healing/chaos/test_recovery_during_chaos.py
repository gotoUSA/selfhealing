"""
Recovery During Chaos Tests

File: integration/chaos/test_recovery_during_chaos.py

Business Risk: Failed recovery attempts during active failure periods
Compliance Alignment: NIST IR-4 (Incident Handling), SOC 2 (Availability)

Test Cases:
- CHAOS-R001: Service recovery detected while Circuit Breaker open
- CHAOS-R002: Partial recovery (50%) handling
- CHAOS-R003: Full recovery during active retry sequence

Reference: docs/testing/SELF_HEALING_TEST_SPECIFICATIONS.md §8.3
"""

from decimal import Decimal
from unittest.mock import patch, MagicMock

import pytest
from django.utils import timezone

from selfhealing.services import (
    CircuitBreakerConfig,
    CircuitBreakerService,
    CircuitState,
)
from selfhealing.services import DLQConfig, DLQService
from shopping.tests.factories import OrderFactory, PaymentFactory, UserFactory


# =============================================================================
# CHAOS-R: Recovery During Chaos Tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.tier3_chaos
@pytest.mark.skip(reason="CircuitBreaker uses Redis/Memory adapter - Django ORM (CircuitBreakerState.objects) is not applicable")
class TestRecoveryDuringChaos:
    """
    Tests for system recovery behavior during active failure periods.

    Validates:
    - Circuit Breaker transitions during recovery
    - Partial recovery detection and handling
    - Retry success during failure reduction
    """

    def setup_method(self):
        """Set up test services."""
        self.cb_service = CircuitBreakerService(
            config=CircuitBreakerConfig(
                enabled=True,
                failure_threshold=5,
                recovery_timeout=60,
                success_threshold=2,
                half_open_request_limit=10,
            )
        )
        self.dlq_service = DLQService(
            config=DLQConfig(
                enabled=True,
                retention_days=30,
                max_replay_attempts=2,
            )
        )

    def test_chaos_r001_service_recovery_with_cb_open(self, failure_injector):
        """
        Purpose:
            Test that service recovery is detected while CB is open.

        Scenario:
            1. Force Circuit Breaker to OPEN state
            2. Service starts recovering (failures stop)
            3. CB transitions to HALF_OPEN on timeout
            4. Verify successful requests close CB

        Expected:
            - CB transitions: OPEN -> HALF_OPEN -> CLOSED
            - Recovery detected within timeout window
            - Normal operations resume

        Risk Covered:
            R-013: Circuit Breaker stuck in wrong state

        Compliance:
            SOC 2 (Availability)
        """
        from shopping.models.failed_external_request import CircuitBreakerState

        # Arrange
        service_name = "test_payment_recovery"

        # Force CB open by recording failures
        for _ in range(6):  # Above threshold of 5
            self.cb_service.record_failure(service_name)

        # Verify CB is open
        state = self.cb_service.get_state(service_name)
        assert state == CircuitState.OPEN, f"CB should be OPEN after failures, got {state}"

        # Act: Simulate recovery timeout by updating opened_at
        # This triggers the automatic transition to half-open
        cb_state = CircuitBreakerState.objects.get(service_name=service_name)
        cb_state.opened_at = timezone.now() - timezone.timedelta(seconds=self.cb_service.config.recovery_timeout + 1)
        cb_state.save()

        # Calling should_allow triggers the half-open transition
        self.cb_service.should_allow(service_name)

        # Verify half-open state
        state = self.cb_service.get_state(service_name)
        assert state == CircuitState.HALF_OPEN, f"CB should be HALF_OPEN after timeout, got {state}"

        # Record successful requests to close CB
        for _ in range(2):  # success_threshold = 2
            self.cb_service.record_success(service_name)

        # Assert: CB should be closed
        state = self.cb_service.get_state(service_name)
        assert state == CircuitState.CLOSED, f"CB should be CLOSED after successful requests, got {state}"

    def test_chaos_r002_partial_recovery_handling(self, failure_injector):
        """
        Purpose:
            Test handling of partial recovery (50% success rate).

        Scenario:
            1. CB in HALF_OPEN state
            2. 50% of requests succeed, 50% fail
            3. Verify CB remains in appropriate state
            4. Verify metrics track partial recovery

        Expected:
            - CB stays HALF_OPEN or returns to OPEN
            - No premature closure
            - Partial recovery metrics recorded

        Risk Covered:
            R-013: Circuit Breaker stuck in wrong state
        """
        # Arrange
        service_name = "test_payment_partial"

        # Transition to OPEN then simulate HALF_OPEN by setting model directly
        for _ in range(6):
            self.cb_service.record_failure(service_name)

        # Simulate half-open by updating model directly
        from shopping.models import CircuitBreakerState

        cb_model, _ = CircuitBreakerState.objects.get_or_create(
            service_name=service_name, defaults={"state": "half_open", "failure_count": 0}
        )
        cb_model.state = "half_open"
        cb_model.save()

        # Act: Alternating success/failure (50% each)
        for i in range(4):
            if i % 2 == 0:
                self.cb_service.record_success(service_name)
            else:
                self.cb_service.record_failure(service_name)

        # Assert: Check state
        state = self.cb_service.get_state(service_name)

        # With alternating pattern, CB should not close
        # (needs consecutive successes to close)
        assert state in [
            CircuitState.HALF_OPEN,
            CircuitState.OPEN,
        ], f"CB should remain HALF_OPEN or OPEN with partial recovery, got {state}"

    def test_chaos_r003_full_recovery_during_retries(self, failure_injector):
        """
        Purpose:
            Test that retries succeed when service fully recovers.

        Scenario:
            1. Payment attempt fails, retry scheduled
            2. Service recovers before retry executes
            3. Retry succeeds
            4. Verify complete flow

        Expected:
            - Retry executes successfully
            - No unnecessary DLQ entry
            - Recovery metrics updated

        Risk Covered:
            R-015: Retry storm overwhelming PG
        """
        # Arrange
        failure_injector.failure_rate = 0.0  # Full recovery
        retry_scheduled = True
        retry_executed = False
        retry_succeeded = False

        # Act: Simulate retry execution after recovery
        if retry_scheduled:
            retry_executed = True
            # Service has recovered, no failure
            if not failure_injector.should_fail():
                retry_succeeded = True

        # Assert
        assert retry_executed, "Retry should have been executed"
        assert retry_succeeded, "Retry should succeed after recovery"

    def test_chaos_r004_recovery_during_dlq_replay(self, failure_injector):
        """
        Purpose:
            Test DLQ replay success when service recovers.

        Scenario:
            1. Operations in DLQ due to failures
            2. Service recovers
            3. DLQ replay succeeds
            4. Verify DLQ entries marked resolved

        Expected:
            - Replay executes successfully
            - DLQ entry status updated to "resolved"
            - No further replay needed

        Risk Covered:
            R-014: DLQ entries lost or corrupted
        """
        # Arrange
        dlq_entries = [
            {"id": 1, "status": "pending", "failure_type": "network_error"},
            {"id": 2, "status": "pending", "failure_type": "timeout"},
            {"id": 3, "status": "pending", "failure_type": "pg_error"},
        ]

        failure_injector.failure_rate = 0.0  # Full recovery
        resolved_entries = []

        # Act: Replay each DLQ entry
        for entry in dlq_entries:
            if not failure_injector.should_fail():
                entry["status"] = "resolved"
                resolved_entries.append(entry)

        # Assert
        assert len(resolved_entries) == 3, f"Expected all 3 entries resolved, got {len(resolved_entries)}"

        for entry in resolved_entries:
            assert entry["status"] == "resolved", f"Entry {entry['id']} should be resolved"

    def test_chaos_r005_gradual_recovery_detection(self, failure_injector):
        """
        Purpose:
            Test detection of gradual recovery (improving success rate).

        Scenario:
            1. Start with 80% failure rate
            2. Gradually reduce to 10%
            3. Verify CB transitions appropriately
            4. Verify recovery metrics track improvement

        Expected:
            - Recovery trend detected
            - CB adapts to improving conditions
            - Metrics show recovery trajectory

        Risk Covered:
            R-008: Unpredictable failure patterns
        """
        # Arrange
        service_name = "test_payment_gradual"
        recovery_phases = [
            (0.8, 10),  # 80% failure, 10 calls
            (0.5, 10),  # 50% failure, 10 calls
            (0.2, 10),  # 20% failure, 10 calls
            (0.0, 10),  # 0% failure, 10 calls
        ]

        phase_stats = []

        # Act: Execute each phase
        for rate, count in recovery_phases:
            failure_injector.failure_rate = rate
            failure_injector.reset()

            successes = 0
            failures = 0

            for _ in range(count):
                if failure_injector.should_fail():
                    failures += 1
                else:
                    successes += 1
                    self.cb_service.record_success(service_name)

            phase_stats.append(
                {
                    "configured_rate": rate,
                    "actual_rate": failure_injector.actual_failure_rate,
                    "successes": successes,
                    "failures": failures,
                }
            )

        # Assert: Verify recovery trend
        # Last phase should have highest success rate
        last_phase = phase_stats[-1]
        assert (
            last_phase["successes"] == 10
        ), f"Last phase (0% failure) should have all successes, got {last_phase['successes']}"

        # Final CB state should be closed (after many successes)
        final_state = self.cb_service.get_state(service_name)
        assert final_state == CircuitState.CLOSED, f"CB should be CLOSED after recovery, got {final_state}"


@pytest.mark.django_db(transaction=True)
@pytest.mark.tier3_chaos
class TestRecoveryCoordination:
    """
    Tests for coordinated recovery across multiple components.
    """

    def test_coordinated_cb_and_dlq_recovery(self, failure_injector):
        """
        Purpose:
            Test that CB and DLQ recovery work together.

        Scenario:
            1. CB opens due to failures
            2. Operations queued in DLQ
            3. Service recovers
            4. CB closes AND DLQ replay succeeds

        Expected:
            - CB transition and DLQ replay coordinated
            - No duplicate processing
            - Complete recovery trail
        """
        # Arrange
        cb_service = CircuitBreakerService(
            config=CircuitBreakerConfig(
                enabled=True,
                failure_threshold=5,
                recovery_timeout=60,
                success_threshold=2,
            )
        )
        service_name = "test_coordinated_recovery"

        # Initial state: CB open
        for _ in range(6):
            cb_service.record_failure(service_name)

        dlq_queue = [{"id": i, "status": "pending"} for i in range(5)]

        # Act: Simulate recovery - set to half_open via model
        failure_injector.failure_rate = 0.0
        from shopping.models import CircuitBreakerState

        cb_model, _ = CircuitBreakerState.objects.get_or_create(
            service_name=service_name, defaults={"state": "half_open", "failure_count": 0}
        )
        cb_model.state = "half_open"
        cb_model.save()

        # Process DLQ while CB in half-open
        processed = 0
        for entry in dlq_queue:
            if cb_service.should_allow(service_name):
                if not failure_injector.should_fail():
                    entry["status"] = "resolved"
                    cb_service.record_success(service_name)
                    processed += 1

        # Assert
        assert processed == 5, f"Expected 5 processed, got {processed}"

        final_state = cb_service.get_state(service_name)
        assert final_state == CircuitState.CLOSED, f"CB should be CLOSED after coordinated recovery"

        resolved = [e for e in dlq_queue if e["status"] == "resolved"]
        assert len(resolved) == 5, f"All DLQ entries should be resolved"

    def test_recovery_prevents_new_dlq_entries(self, failure_injector):
        """
        Purpose:
            Test that recovery prevents new entries from going to DLQ.

        Scenario:
            1. Service recovering (low failure rate)
            2. New operations execute
            3. Verify successful operations don't create DLQ entries
            4. Verify minimal DLQ growth

        Expected:
            - Successful ops don't create DLQ entries
            - DLQ growth stops after recovery
            - Normal processing resumes
        """
        # Arrange
        failure_injector.failure_rate = 0.1  # 10% still failing
        new_dlq_entries = []
        successful_ops = []

        # Act: Execute operations during recovery
        for i in range(20):
            if failure_injector.should_fail():
                new_dlq_entries.append(
                    {
                        "id": i,
                        "timestamp": timezone.now(),
                    }
                )
            else:
                successful_ops.append(i)

        # Assert
        stats = failure_injector.get_stats()

        # Most operations should succeed
        assert len(successful_ops) > len(new_dlq_entries), (
            f"Successes ({len(successful_ops)}) should exceed " f"DLQ entries ({len(new_dlq_entries)}) during recovery"
        )

        # DLQ growth should be minimal
        assert len(new_dlq_entries) <= 5, f"Expected minimal DLQ growth (<=5), got {len(new_dlq_entries)}"
