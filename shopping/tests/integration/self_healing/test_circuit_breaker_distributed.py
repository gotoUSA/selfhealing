"""
Distributed Circuit Breaker Tests

Tests for circuit breaker state synchronization across distributed workers.
Validates that CB state changes are immediately visible to all instances.

Reference:
- Gap Report: G-09 (Distributed Worker CB State Synchronization)
- docs/L3_SELF_HEALING_OPERATIONS.md §9 (Circuit Breaker)

Note:
    This test simulates distributed behavior with separate service instances,
    not actual separate processes. The key is verifying database is the
    source of truth without caching issues.
"""

from datetime import timedelta
from unittest.mock import patch, MagicMock

import pytest
from django.utils import timezone

from shopping.models.failed_payment import CircuitBreakerState
from shopping.services.self_healing.circuit_breaker_service import (
    CircuitBreakerConfig,
    CircuitBreakerService,
    CircuitState,
)
from shopping.tests.factories import UserFactory


@pytest.mark.django_db(transaction=True)
class TestCircuitBreakerDistributed:
    """
    Tests for circuit breaker in distributed environment.

    Validates:
    - State visible immediately across instances
    - No stale cache issues
    - Database consistency maintained
    """

    @pytest.fixture
    def admin_user(self):
        """Create an admin user."""
        return UserFactory(is_staff=True, is_superuser=True)

    @pytest.fixture(autouse=True)
    def clean_circuit_breakers(self):
        """Clean up circuit breaker states before each test."""
        CircuitBreakerState.objects.all().delete()
        yield

    def test_circuit_state_visible_across_workers(self, admin_user):
        """
        Purpose:
            Verify CB state changes are immediately visible to all workers.

        Scenario:
            1. Worker A opens circuit
            2. Worker B checks should_allow()
            3. Worker B should see circuit as OPEN

        Expected:
            - No caching delays
            - Database is source of truth
            - Consistent view across instances

        Note:
            This test simulates distributed behavior with separate
            service instances, not actual separate processes.
        """
        service_name = "distributed_test_payment"

        # Simulate Worker A - creates and opens circuit
        service_a = CircuitBreakerService(config=CircuitBreakerConfig(enabled=True))
        result = service_a.force_open(
            service_name=service_name,
            reason="Worker A detected PG failure",
            controlled_by=admin_user,
        )
        assert result.success is True

        # Simulate Worker B - new instance, no shared memory
        # This is key: creating a NEW service instance simulates a different worker
        service_b = CircuitBreakerService(config=CircuitBreakerConfig(enabled=True))

        # Worker B should see the open circuit (no caching)
        assert service_b.should_allow(service_name) is False
        assert service_b.get_state(service_name) == CircuitState.OPEN

    def test_circuit_close_propagates_immediately(self, admin_user):
        """
        Purpose:
            Verify circuit close propagates to all workers immediately.

        Scenario:
            1. Worker A opens circuit
            2. Worker B sees OPEN
            3. Worker A closes circuit
            4. Worker B sees CLOSED immediately (no stale cache)

        Expected:
            - State change visible immediately
            - No stale cached state in Worker B
        """
        service_name = "distributed_close_test"

        # Worker A opens circuit
        service_a = CircuitBreakerService(config=CircuitBreakerConfig(enabled=True))
        service_a.force_open(
            service_name=service_name,
            reason="Initial open",
            controlled_by=admin_user,
        )

        # Worker B sees OPEN
        service_b = CircuitBreakerService(config=CircuitBreakerConfig(enabled=True))
        assert service_b.get_state(service_name) == CircuitState.OPEN

        # Worker A closes circuit
        service_a.force_close(
            service_name=service_name,
            reason="PG recovered",
            controlled_by=admin_user,
        )

        # Worker B should see CLOSED immediately (not cached OPEN)
        # Re-query the state to simulate "later" check
        assert service_b.get_state(service_name) == CircuitState.CLOSED
        assert service_b.should_allow(service_name) is True

    def test_multiple_workers_see_consistent_state(self, admin_user):
        """
        Purpose:
            Verify multiple workers see consistent state at all times.

        Scenario:
            1. Create 5 service instances (simulating 5 workers)
            2. One worker opens circuit
            3. All workers should report same state

        Expected:
            - All workers return identical state
            - No divergence due to caching
        """
        service_name = "multi_worker_test"

        # Create multiple service instances
        workers = [CircuitBreakerService(config=CircuitBreakerConfig(enabled=True)) for _ in range(5)]

        # Worker 0 opens circuit
        workers[0].force_open(
            service_name=service_name,
            reason="Test open",
            controlled_by=admin_user,
        )

        # All workers should see OPEN
        for i, worker in enumerate(workers):
            state = worker.get_state(service_name)
            assert state == CircuitState.OPEN, f"Worker {i} has inconsistent state: {state}"

        # All workers should block requests
        for i, worker in enumerate(workers):
            allowed = worker.should_allow(service_name)
            assert allowed is False, f"Worker {i} incorrectly allowing requests"

    def test_new_worker_instance_sees_existing_state(self, admin_user):
        """
        Purpose:
            Verify a newly started worker sees existing circuit state.

        Scenario:
            1. Worker A opens circuit
            2. Worker A goes away (instance destroyed)
            3. New Worker C starts
            4. Worker C should see circuit is still OPEN

        Expected:
            - State persisted in database
            - New workers inherit state without explicit sync
        """
        service_name = "new_worker_test"

        # Worker A opens circuit
        service_a = CircuitBreakerService(config=CircuitBreakerConfig(enabled=True))
        service_a.force_open(
            service_name=service_name,
            reason="Test open before restart",
            controlled_by=admin_user,
        )

        # Destroy Worker A reference (simulate worker shutdown)
        del service_a

        # New Worker C starts fresh
        service_c = CircuitBreakerService(config=CircuitBreakerConfig(enabled=True))

        # Worker C should see the open circuit from database
        assert service_c.get_state(service_name) == CircuitState.OPEN
        assert service_c.should_allow(service_name) is False

    def test_concurrent_state_queries_consistent(self, admin_user):
        """
        Purpose:
            Verify concurrent queries return consistent state.

        Scenario:
            1. Open circuit
            2. Multiple workers query state rapidly
            3. All should return consistent OPEN

        Expected:
            - No race conditions in state reading
            - Consistent results across rapid queries
        """
        service_name = "concurrent_query_test"

        # Setup: Open circuit
        setup_service = CircuitBreakerService(config=CircuitBreakerConfig(enabled=True))
        setup_service.force_open(
            service_name=service_name,
            reason="Setup for concurrent test",
            controlled_by=admin_user,
        )

        # Create workers and query rapidly
        results = []
        for _ in range(10):
            worker = CircuitBreakerService(config=CircuitBreakerConfig(enabled=True))
            state = worker.get_state(service_name)
            allowed = worker.should_allow(service_name)
            results.append((state, allowed))

        # All results should be consistent
        expected_state = CircuitState.OPEN
        expected_allowed = False

        for i, (state, allowed) in enumerate(results):
            assert state == expected_state, f"Query {i} returned inconsistent state: {state}"
            assert allowed == expected_allowed, f"Query {i} returned inconsistent allowed: {allowed}"

    def test_database_is_single_source_of_truth(self, admin_user):
        """
        Purpose:
            Verify database is the definitive source of circuit state.

        Scenario:
            1. Open circuit via service
            2. Directly query database
            3. Modify database directly
            4. Service should reflect database state

        Expected:
            - Service always reads from database
            - Direct DB changes immediately visible to service
        """
        service_name = "db_source_of_truth_test"

        # Open via service
        service = CircuitBreakerService(config=CircuitBreakerConfig(enabled=True))
        service.force_open(
            service_name=service_name,
            reason="Initial open",
            controlled_by=admin_user,
        )

        # Verify via direct DB query
        db_state = CircuitBreakerState.objects.get(service_name=service_name)
        assert db_state.state == CircuitState.OPEN

        # Modify directly in database (simulate external admin tool)
        db_state.state = CircuitState.CLOSED
        db_state.manually_controlled = False
        db_state.save()

        # Service should see the database change immediately
        # Create new service instance to ensure no instance caching
        fresh_service = CircuitBreakerService(config=CircuitBreakerConfig(enabled=True))
        assert fresh_service.get_state(service_name) == CircuitState.CLOSED
        assert fresh_service.should_allow(service_name) is True

    def test_half_open_state_visible_across_workers(self, admin_user):
        """
        Purpose:
            Verify HALF_OPEN state is also synchronized.

        Scenario:
            1. Worker A opens circuit
            2. Worker A transitions to half-open
            3. Worker B should see HALF_OPEN

        Expected:
            - All states (CLOSED, OPEN, HALF_OPEN) sync correctly
        """
        service_name = "half_open_sync_test"

        # Worker A opens circuit
        service_a = CircuitBreakerService(config=CircuitBreakerConfig(enabled=True, recovery_timeout=0))
        service_a.force_open(
            service_name=service_name,
            reason="Test open",
            controlled_by=admin_user,
        )

        # Directly set to half-open to simulate recovery timeout
        db_state = CircuitBreakerState.objects.get(service_name=service_name)
        db_state.state = CircuitState.HALF_OPEN
        db_state.manually_controlled = False
        db_state.save()

        # Worker B should see HALF_OPEN
        service_b = CircuitBreakerService(config=CircuitBreakerConfig(enabled=True))
        assert service_b.get_state(service_name) == CircuitState.HALF_OPEN

        # HALF_OPEN should allow requests (for testing recovery)
        assert service_b.should_allow(service_name) is True
