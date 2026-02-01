"""
Distributed Circuit Breaker Tests with Redis.

Tests for circuit breaker state synchronization across distributed workers
using Redis as the single source of truth.

This replaces the old Django ORM-based test_circuit_breaker_distributed.py
with proper Repository pattern using Redis.

Reference:
- Gap Report: G-09 (Distributed Worker CB State Synchronization)
- docs/L3_SELF_HEALING_OPERATIONS.md §9 (Circuit Breaker)

Key Scenarios:
- Worker A opens circuit -> Worker B immediately sees OPEN state
- Concurrent state queries return consistent results
- New service instances see existing state from Redis
- State changes propagate immediately (no stale cache)
"""

import pytest
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from selfhealing.services import CircuitBreakerService
from selfhealing.services.circuit_breaker_service import CircuitBreakerConfig


@dataclass
class MockUser:
    """Mock user for admin operations."""

    id: int
    username: str
    is_staff: bool = False
    is_superuser: bool = False


@pytest.mark.requires_redis
@pytest.mark.tier2
class TestCircuitBreakerDistributedRedis:
    """
    Tests for circuit breaker in distributed environment using Redis.

    Validates:
    - State visible immediately across worker instances
    - No stale cache issues
    - Redis is single source of truth
    """

    @pytest.fixture
    def admin_user(self):
        """Create an admin user."""
        return MockUser(id=1, username="admin", is_staff=True, is_superuser=True)

    @pytest.fixture
    def service_name(self):
        """Test service name."""
        return "distributed_test_service"

    def test_state_visible_across_worker_instances(
        self,
        redis_circuit_breaker_repository,
        admin_user,
        service_name,
    ):
        """
        Purpose:
            Verify CB state changes are immediately visible to all workers.

        Scenario:
            1. Worker A opens circuit (using Service A)
            2. Worker B queries state (using Service B with same repo)
            3. Worker B should see circuit as OPEN immediately

        Expected:
            - No caching delays
            - Redis is source of truth
            - Both service instances see consistent state
        """
        # Arrange: Create two separate service instances (simulating workers)
        config = CircuitBreakerConfig(enabled=True)
        worker_a_service = CircuitBreakerService(
            config=config,
            repository=redis_circuit_breaker_repository,
        )
        worker_b_service = CircuitBreakerService(
            config=config,
            repository=redis_circuit_breaker_repository,
        )

        # Act: Worker A opens the circuit
        worker_a_service.force_open(
            service_name=service_name,
            reason="Simulated maintenance by Worker A",
            controlled_by=admin_user,
        )

        # Assert: Worker B immediately sees OPEN state
        is_allowed = worker_b_service.should_allow(service_name)
        assert is_allowed is False, "Worker B should see circuit as OPEN immediately"

        # Verify state details from Worker B's perspective
        state = redis_circuit_breaker_repository.get_state(service_name)
        assert state is not None
        assert state.state == "open"
        assert state.manually_controlled is True

    def test_close_propagates_immediately(
        self,
        redis_circuit_breaker_repository,
        admin_user,
        service_name,
    ):
        """
        Purpose:
            Verify close state propagates immediately to all workers.

        Scenario:
            1. Worker A opens circuit
            2. Worker B sees OPEN
            3. Worker A closes circuit
            4. Worker B immediately sees CLOSED

        Expected:
            - State change is atomic
            - No stale reads
        """
        # Arrange
        config = CircuitBreakerConfig(enabled=True)
        worker_a = CircuitBreakerService(config=config, repository=redis_circuit_breaker_repository)
        worker_b = CircuitBreakerService(config=config, repository=redis_circuit_breaker_repository)

        # Step 1: Open
        worker_a.force_open(service_name=service_name, reason="Open", controlled_by=admin_user)
        assert worker_b.should_allow(service_name) is False

        # Step 2: Close
        worker_a.force_close(service_name=service_name, reason="Close", controlled_by=admin_user)

        # Assert: Worker B sees closed immediately
        assert worker_b.should_allow(service_name) is True

        state = redis_circuit_breaker_repository.get_state(service_name)
        assert state.state == "closed"

    def test_multiple_workers_see_consistent_state(
        self,
        redis_circuit_breaker_repository,
        admin_user,
        service_name,
    ):
        """
        Purpose:
            Verify multiple workers always see consistent state.

        Scenario:
            1. Create 5 worker instances
            2. Worker 1 opens circuit
            3. All other workers query state
            4. All should see OPEN

        Expected:
            - 100% consistency across workers
        """
        # Arrange: Create multiple workers
        config = CircuitBreakerConfig(enabled=True)
        workers: List[CircuitBreakerService] = [
            CircuitBreakerService(config=config, repository=redis_circuit_breaker_repository) for _ in range(5)
        ]

        # Act: Worker 0 opens circuit
        workers[0].force_open(
            service_name=service_name,
            reason="Multi-worker test",
            controlled_by=admin_user,
        )

        # Assert: All workers see OPEN
        results = [w.should_allow(service_name) for w in workers]
        assert all(r is False for r in results), f"All workers should see OPEN, got: {results}"

    def test_new_worker_instance_sees_existing_state(
        self,
        redis_circuit_breaker_repository,
        admin_user,
        service_name,
    ):
        """
        Purpose:
            Verify new worker instances can read existing state.

        Scenario:
            1. Worker A opens circuit and "shuts down" (scope ends)
            2. New Worker C starts up and queries state
            3. Worker C should see the existing OPEN state

        Expected:
            - State persists in Redis across worker lifecycles
        """
        config = CircuitBreakerConfig(enabled=True)

        # Worker A opens circuit
        worker_a = CircuitBreakerService(config=config, repository=redis_circuit_breaker_repository)
        worker_a.force_open(
            service_name=service_name,
            reason="Persisted state test",
            controlled_by=admin_user,
        )

        # "Shutdown" Worker A (just discard reference)
        del worker_a

        # New Worker C starts
        worker_c = CircuitBreakerService(config=config, repository=redis_circuit_breaker_repository)

        # Assert: Worker C sees existing state
        assert worker_c.should_allow(service_name) is False, "New worker should see persisted OPEN state"

    def test_concurrent_state_queries_consistent(
        self,
        redis_circuit_breaker_repository,
        admin_user,
        service_name,
    ):
        """
        Purpose:
            Verify concurrent queries return consistent results.

        Scenario:
            1. Open circuit
            2. 10 concurrent threads query state
            3. All should return same result

        Expected:
            - No race conditions
            - Atomic reads
        """
        config = CircuitBreakerConfig(enabled=True)

        # Setup: Open circuit first
        service = CircuitBreakerService(config=config, repository=redis_circuit_breaker_repository)
        service.force_open(
            service_name=service_name,
            reason="Concurrent test",
            controlled_by=admin_user,
        )

        # Concurrent queries
        def query_state():
            svc = CircuitBreakerService(config=config, repository=redis_circuit_breaker_repository)
            return svc.should_allow(service_name)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(query_state) for _ in range(10)]
            results = [f.result() for f in as_completed(futures)]

        # Assert: All results are consistent (all False = OPEN)
        assert len(set(results)) == 1, f"Concurrent queries returned inconsistent results: {results}"
        assert results[0] is False, "All queries should return False (circuit OPEN)"

    def test_redis_is_single_source_of_truth(
        self,
        redis_circuit_breaker_repository,
        admin_user,
        service_name,
    ):
        """
        Purpose:
            Verify Redis is the authoritative source, not local memory.

        Scenario:
            1. Worker A opens circuit
            2. Directly modify Redis (simulate external change)
            3. Worker B reads and sees the direct modification

        Expected:
            - No local caching that would return stale data
        """
        config = CircuitBreakerConfig(enabled=True)

        # Worker A opens circuit
        worker_a = CircuitBreakerService(config=config, repository=redis_circuit_breaker_repository)
        worker_a.force_open(
            service_name=service_name,
            reason="Source of truth test",
            controlled_by=admin_user,
        )

        # Directly close via repository (simulating external modification)
        redis_circuit_breaker_repository.atomic_force_close(
            service_name=service_name,
            reason="External close",
            controlled_by_id=admin_user.id,
        )

        # Worker B (new instance) reads
        worker_b = CircuitBreakerService(config=config, repository=redis_circuit_breaker_repository)

        # Assert: Worker B sees the external modification
        assert worker_b.should_allow(service_name) is True, "Worker B should see externally modified CLOSED state"

    def test_half_open_state_visible_across_workers(
        self,
        redis_circuit_breaker_repository,
        admin_user,
        service_name,
    ):
        """
        Purpose:
            Verify HALF_OPEN state is visible across workers.

        Scenario:
            1. Worker A transitions to HALF_OPEN
            2. Worker B queries and sees HALF_OPEN

        Expected:
            - HALF_OPEN state is distributed correctly
        """
        config = CircuitBreakerConfig(enabled=True)

        # First create the state
        redis_circuit_breaker_repository.get_or_create(service_name)

        # Transition to half_open via update_state (the correct Repository API)
        redis_circuit_breaker_repository.update_state(service_name, state="half_open")

        # Worker B queries
        worker_b = CircuitBreakerService(config=config, repository=redis_circuit_breaker_repository)
        state = redis_circuit_breaker_repository.get_state(service_name)

        assert state.state == "half_open", "Worker B should see HALF_OPEN state"
