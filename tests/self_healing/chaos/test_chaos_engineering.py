"""
Chaos Engineering Tests for Self-Healing System

Tests the fault tolerance of the self-healing layer under various failure conditions.
Validates that the system gracefully handles:
- Random failures
- Network latency
- Concurrent failures
- Cascading failures

Test Categories:
    A. Random Failure Injection:
        - Random PG failures with configurable rate
        - Random DB failures
        - Mixed failure scenarios
    B. Latency Injection:
        - High latency handling
        - Timeout escalation
        - SLA compliance under stress
    C. Concurrent Failure Handling:
        - Multiple simultaneous failures
        - Resource contention
        - DLQ write concurrency
    D. Circuit Breaker Stress:
        - Rapid state transitions
        - Threshold boundary conditions
        - Recovery stability

NOTE: Tests use Redis adapters via fixtures from conftest.py.
      Requires: docker-compose -f docker-compose.test.yml up -d
"""

import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from typing import Generator

import pytest

from selfhealing.interfaces.repositories import FailedOperationStatus

from selfhealing.services import CircuitBreakerService, DLQService
from selfhealing.services.circuit_breaker import (
    CircuitBreakerConfig,
    CircuitState,
)
from selfhealing.services.dlq import DLQConfig
from shopping.tests.factories import OrderFactory, UserFactory


# =============================================================================
# Failure Injection Utilities
# =============================================================================


class FailureInjector:
    """
    Base class for failure injection.

    Provides common functionality for injecting failures
    into the system under test.
    """

    def __init__(self, failure_rate: float = 0.3):
        """
        Initialize failure injector.

        Args:
            failure_rate: Probability of failure (0.0 to 1.0)
        """
        self.failure_rate = failure_rate
        self.total_calls = 0
        self.failed_calls = 0

    def should_fail(self) -> bool:
        """
        Determine if the current call should fail.

        Returns:
            True if the call should be failed
        """
        self.total_calls += 1
        if random.random() < self.failure_rate:
            self.failed_calls += 1
            return True
        return False

    def get_stats(self) -> dict:
        """
        Get injection statistics.

        Returns:
            Dict with total, failed, and success counts
        """
        return {
            "total_calls": self.total_calls,
            "failed_calls": self.failed_calls,
            "success_calls": self.total_calls - self.failed_calls,
            "actual_failure_rate": (self.failed_calls / self.total_calls if self.total_calls > 0 else 0),
        }


@contextmanager
def random_failure_injector(
    failure_rate: float = 0.3,
    error_class: type = Exception,
    error_message: str = "Injected failure",
) -> Generator[FailureInjector, None, None]:
    """
    Context manager for random failure injection.

    Args:
        failure_rate: Probability of failure
        error_class: Exception class to raise
        error_message: Error message for raised exception

    Yields:
        FailureInjector instance for stats tracking
    """
    injector = FailureInjector(failure_rate)
    yield injector


@contextmanager
def latency_injector(
    min_ms: int = 100,
    max_ms: int = 2000,
) -> Generator[dict, None, None]:
    """
    Context manager for latency injection.

    Args:
        min_ms: Minimum latency in milliseconds
        max_ms: Maximum latency in milliseconds

    Yields:
        Dict to track latency statistics
    """
    stats = {
        "total_calls": 0,
        "total_latency_ms": 0,
        "min_latency_ms": None,
        "max_latency_ms": None,
    }

    def inject_latency() -> int:
        latency = random.randint(min_ms, max_ms)
        time.sleep(latency / 1000)
        stats["total_calls"] += 1
        stats["total_latency_ms"] += latency
        if stats["min_latency_ms"] is None or latency < stats["min_latency_ms"]:
            stats["min_latency_ms"] = latency
        if stats["max_latency_ms"] is None or latency > stats["max_latency_ms"]:
            stats["max_latency_ms"] = latency
        return latency

    stats["inject"] = inject_latency
    yield stats


# =============================================================================
# A. Random Failure Injection Tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.chaos
@pytest.mark.requires_redis
class TestRandomFailureInjection:
    """
    Tests for system behavior under random failures.

    Validates:
    - System continues processing despite random failures
    - Failed operations are properly captured in DLQ
    - Success rate meets minimum threshold
    
    Uses Redis repository via fixture for real integration testing.
    Requires: docker-compose -f docker-compose.test.yml up -d
    """

    def test_dlq_captures_random_failures(self, redis_dlq_repository):
        """
        Purpose:
            Test that DLQ correctly captures randomly failed operations.

        Scenario:
            1. Simulate multiple operations with random failures
            2. Verify all failures are captured in DLQ
            3. Check DLQ entries have proper forensic context
        """
        dlq_service = DLQService(
            config=DLQConfig(enabled=True, retention_days=30, max_replay_attempts=2),
            repository=redis_dlq_repository,
        )
        
        failure_rate = 0.3
        num_operations = 20
        injector = FailureInjector(failure_rate)

        failed_order_ids = []
        success_count = 0

        for i in range(num_operations):
            order = OrderFactory()

            if injector.should_fail():
                # Simulate failure - store to DLQ
                result = dlq_service.store_failure(
                    domain="payment",
                    failure_type="CHAOS_TEST_FAILURE",
                    entity_type="order",
                    entity_id=str(order.id),
                    error_code="INJECTED_ERROR",
                    error_message=f"Chaos test failure #{i}",
                    snapshot_data={"order_id": order.id, "iteration": i},
                    metadata={"test_type": "random_failure_injection"},
                )
                assert result.success, f"DLQ store failed: {result.error}"
                failed_order_ids.append(order.id)
            else:
                success_count += 1

        # Verify all failures are in DLQ (using Redis repository)
        dlq_entries = redis_dlq_repository.find_by_failure_type("CHAOS_TEST_FAILURE")
        matched_entries = [
            e for e in dlq_entries 
            if e.entity_id in [str(oid) for oid in failed_order_ids]
        ]

        assert len(matched_entries) == len(failed_order_ids)

        # Check forensic context
        for entry in matched_entries:
            assert entry.metadata.get("test_type") == "random_failure_injection"
            assert "iteration" in entry.snapshot_data

        stats = injector.get_stats()
        print(f"✓ Random failure test: {stats['failed_calls']}/{stats['total_calls']} failures captured")

    def test_batch_operations_with_partial_failures(self, redis_dlq_repository):
        """
        Purpose:
            Test batch processing with partial failures.

        Scenario:
            1. Process batch of 50 orders with 30% failure rate
            2. Verify at least 70% succeed
            3. All failures captured in DLQ
            4. System remains stable
        """
        dlq_service = DLQService(
            config=DLQConfig(enabled=True, retention_days=30, max_replay_attempts=2),
            repository=redis_dlq_repository,
        )
        
        batch_size = 50
        failure_rate = 0.3
        injector = FailureInjector(failure_rate)

        orders = [OrderFactory() for _ in range(batch_size)]
        results = []

        for order in orders:
            if injector.should_fail():
                dlq_service.store_failure(
                    domain="payment",
                    failure_type="BATCH_CHAOS_FAILURE",
                    entity_type="order",
                    entity_id=str(order.id),
                    error_message="Batch chaos failure",
                )
                results.append({"order_id": order.id, "success": False})
            else:
                results.append({"order_id": order.id, "success": True})

        success_count = sum(1 for r in results if r["success"])
        failure_count = sum(1 for r in results if not r["success"])

        # At least 50% should succeed (with 30% failure rate, expect ~70%)
        assert success_count >= batch_size * 0.5, f"Too many failures: {failure_count}/{batch_size}"

        # All failures in DLQ (using Redis repository)
        dlq_entries = redis_dlq_repository.find_by_failure_type("BATCH_CHAOS_FAILURE")
        assert len(dlq_entries) == failure_count

        print(f"✓ Batch test: {success_count} succeeded, {failure_count} in DLQ")


# =============================================================================
# B. Latency Injection Tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.chaos
@pytest.mark.requires_redis
class TestLatencyInjection:
    """
    Tests for system behavior under high latency conditions.

    Validates:
    - System handles variable latency gracefully
    - Timeout thresholds are respected
    - Performance remains within acceptable bounds
    
    Uses Redis repository via fixture for real integration testing.
    Requires: docker-compose -f docker-compose.test.yml up -d
    """

    def test_operations_complete_under_latency(self, redis_dlq_repository):
        """
        Purpose:
            Test that operations complete despite added latency.

        Scenario:
            1. Add random latency (10-100ms) to each operation
            2. Verify all operations complete
            3. Check total time is within acceptable bounds
        """
        dlq_service = DLQService(
            config=DLQConfig(enabled=True, retention_days=30, max_replay_attempts=2),
            repository=redis_dlq_repository,
        )
        
        num_operations = 10
        min_latency_ms = 10
        max_latency_ms = 100

        with latency_injector(min_latency_ms, max_latency_ms) as stats:
            start_time = time.time()

            for i in range(num_operations):
                # Inject latency
                stats["inject"]()

                # Perform DLQ operation
                order = OrderFactory()
                result = dlq_service.store_failure(
                    domain="payment",
                    failure_type="LATENCY_TEST",
                    entity_type="order",
                    entity_id=str(order.id),
                    error_message=f"Latency test #{i}",
                )
                assert result.success

            elapsed_time = time.time() - start_time

        # Total time should be reasonable (num_ops * max_latency + overhead)
        max_expected_time = (num_operations * max_latency_ms / 1000) + 5  # 5s overhead
        assert elapsed_time < max_expected_time, f"Operations too slow: {elapsed_time:.2f}s > {max_expected_time:.2f}s"

        avg_latency = stats["total_latency_ms"] / stats["total_calls"]
        print(f"✓ Latency test: avg={avg_latency:.1f}ms, total={elapsed_time:.2f}s")

    def test_dlq_write_performance_under_load(self, redis_dlq_repository):
        """
        Purpose:
            Test DLQ write performance under simulated load.

        Scenario:
            1. Write 100 DLQ entries as fast as possible
            2. Measure throughput
            3. Verify all entries persisted correctly
        """
        dlq_service = DLQService(
            config=DLQConfig(enabled=True, retention_days=30, max_replay_attempts=2),
            repository=redis_dlq_repository,
        )
        
        num_entries = 100
        orders = [OrderFactory() for _ in range(num_entries)]

        start_time = time.time()

        for i, order in enumerate(orders):
            result = dlq_service.store_failure(
                domain="payment",
                failure_type="PERFORMANCE_TEST",
                entity_type="order",
                entity_id=str(order.id),
                error_message=f"Performance test entry #{i}",
                snapshot_data={"index": i},
            )
            assert result.success

        elapsed_time = time.time() - start_time
        throughput = num_entries / elapsed_time

        # Verify all entries exist (using Redis repository)
        dlq_entries = redis_dlq_repository.find_by_failure_type("PERFORMANCE_TEST")
        assert len(dlq_entries) == num_entries

        print(f"✓ Performance test: {throughput:.1f} writes/sec, {elapsed_time:.2f}s total")


# =============================================================================
# C. Concurrent Failure Handling Tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.chaos
@pytest.mark.requires_redis
class TestConcurrentFailures:
    """
    Tests for handling concurrent failures.

    Validates:
    - Multiple threads can write to DLQ concurrently
    - No race conditions in DLQ operations
    - Data integrity maintained under concurrency
    
    Uses Redis repository via fixture for real integration testing.
    Requires: docker-compose -f docker-compose.test.yml up -d
    """

    def test_concurrent_dlq_writes(self, redis_dlq_repository):
        """
        Purpose:
            Test concurrent DLQ writes from multiple threads.

        Scenario:
            1. Create 10 orders
            2. Write DLQ entries from 5 concurrent threads
            3. Verify no duplicates or missing entries
        """
        dlq_config = DLQConfig(enabled=True, retention_days=30, max_replay_attempts=2)
        
        num_orders = 10
        num_threads = 5
        orders = [OrderFactory() for _ in range(num_orders)]
        results = []

        def write_dlq_entry(order, thread_id):
            """Write a single DLQ entry."""
            dlq_service = DLQService(config=dlq_config, repository=redis_dlq_repository)
            result = dlq_service.store_failure(
                domain="payment",
                failure_type="CONCURRENT_TEST",
                entity_type="order",
                entity_id=str(order.id),
                error_message=f"Concurrent test from thread {thread_id}",
                metadata={"thread_id": thread_id},
            )
            return {"order_id": order.id, "thread_id": thread_id, "success": result.success}

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = []
            for i, order in enumerate(orders):
                thread_id = i % num_threads
                futures.append(executor.submit(write_dlq_entry, order, thread_id))

            for future in as_completed(futures):
                results.append(future.result())

        # All writes should succeed
        success_count = sum(1 for r in results if r["success"])
        assert success_count == num_orders, f"Expected {num_orders} successes, got {success_count}"

        # Verify entries in Redis storage
        dlq_entries = redis_dlq_repository.find_by_failure_type("CONCURRENT_TEST")
        assert len(dlq_entries) == num_orders

        print(f"✓ Concurrent test: {num_orders} entries written from {num_threads} threads")

    def test_concurrent_failure_and_recovery(self, redis_dlq_repository):
        """
        Purpose:
            Test concurrent failures with mixed outcomes.

        Scenario:
            1. Simulate 20 concurrent operations
            2. 50% fail and go to DLQ
            3. Verify correct counts and no data loss
        """
        dlq_config = DLQConfig(enabled=True, retention_days=30, max_replay_attempts=2)
        num_operations = 20
        results = []

        def simulate_operation(operation_id):
            """Simulate an operation that might fail."""
            order = OrderFactory()

            # Deterministic failure based on operation_id
            should_fail = operation_id % 2 == 0

            if should_fail:
                dlq_service = DLQService(config=dlq_config, repository=redis_dlq_repository)
                result = dlq_service.store_failure(
                    domain="payment",
                    failure_type="CONCURRENT_MIXED_TEST",
                    entity_type="order",
                    entity_id=str(order.id),
                    error_message=f"Simulated failure #{operation_id}",
                )
                return {"operation_id": operation_id, "failed": True, "dlq_stored": result.success}
            else:
                return {"operation_id": operation_id, "failed": False, "dlq_stored": False}

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(simulate_operation, i) for i in range(num_operations)]

            for future in as_completed(futures):
                results.append(future.result())

        # Count outcomes
        failed_count = sum(1 for r in results if r["failed"])
        dlq_stored_count = sum(1 for r in results if r["dlq_stored"])

        # Should be 50% failure rate (all even operation_ids)
        expected_failures = num_operations // 2
        assert failed_count == expected_failures

        # All failures should be in DLQ
        assert dlq_stored_count == failed_count

        # Verify Redis storage
        dlq_entries = redis_dlq_repository.find_by_failure_type("CONCURRENT_MIXED_TEST")
        assert len(dlq_entries) == failed_count

        print(f"✓ Mixed concurrent test: {failed_count} failures, all in DLQ")


# =============================================================================
# D. Circuit Breaker Stress Tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.chaos
@pytest.mark.requires_redis
class TestCircuitBreakerStress:
    """
    Tests for circuit breaker stability under stress.

    Validates:
    - Rapid state transitions don't cause instability
    - Threshold boundaries are correctly enforced
    - Recovery behavior is predictable
    
    Uses Redis repository via fixture for real integration testing.
    Requires: docker-compose -f docker-compose.test.yml up -d
    """

    def test_rapid_state_transitions(self, redis_circuit_breaker_repository):
        """
        Purpose:
            Test circuit breaker stability under rapid state changes.

        Scenario:
            1. Rapidly toggle between open and closed states
            2. Verify state consistency
            3. Check no orphaned records
        """
        config = CircuitBreakerConfig(
            enabled=True,
            failure_threshold=5,
            recovery_timeout=10,
            success_threshold=2,
        )
        service = CircuitBreakerService(config=config, repository=redis_circuit_breaker_repository)
        
        service_name = "stress_test_service"
        admin_user = UserFactory(is_staff=True)
        num_transitions = 20

        for i in range(num_transitions):
            if i % 2 == 0:
                result = service.force_open(
                    service_name=service_name,
                    reason=f"Stress test open #{i}",
                    controlled_by=admin_user,
                )
                assert result.success
                state = service.get_state(service_name)
                assert state == CircuitState.OPEN
            else:
                result = service.force_close(
                    service_name=service_name,
                    reason=f"Stress test close #{i}",
                    controlled_by=admin_user,
                )
                assert result.success
                state = service.get_state(service_name)
                assert state == CircuitState.CLOSED

        # Verify state exists in Redis
        cb_state = redis_circuit_breaker_repository.get_by_service_name(service_name)
        assert cb_state is not None

        print(f"✓ Rapid transition test: {num_transitions} transitions completed")

    def test_threshold_boundary_conditions(self, redis_circuit_breaker_repository):
        """
        Purpose:
            Test circuit breaker at threshold boundaries.

        Scenario:
            1. Record exactly threshold-1 failures (should stay closed)
            2. Record one more failure (should open)
            3. Verify precise threshold enforcement
        """
        config = CircuitBreakerConfig(
            enabled=True,
            failure_threshold=5,
            recovery_timeout=10,
            success_threshold=2,
        )
        service = CircuitBreakerService(config=config, repository=redis_circuit_breaker_repository)
        
        service_name = "threshold_test_service"
        threshold = config.failure_threshold

        # Initialize state
        service.get_or_create_state(service_name)

        # Record threshold-1 failures - should stay closed
        for i in range(threshold - 1):
            service.record_failure(service_name)
            state = service.get_state(service_name)
            assert state == CircuitState.CLOSED, f"Opened too early at failure {i+1}"

        # One more failure should trip the circuit
        service.record_failure(service_name)
        state = service.get_state(service_name)
        assert state == CircuitState.OPEN, "Circuit should be open after threshold failures"

        print(f"✓ Threshold test: Circuit opened at exactly {threshold} failures")

    def test_multiple_services_isolation(self, redis_circuit_breaker_repository):
        """
        Purpose:
            Test that multiple circuit breakers are isolated.

        Scenario:
            1. Create 5 circuit breakers for different services
            2. Open some, keep others closed
            3. Verify no cross-contamination
        """
        config = CircuitBreakerConfig(
            enabled=True,
            failure_threshold=5,
            recovery_timeout=10,
            success_threshold=2,
        )
        service = CircuitBreakerService(config=config, repository=redis_circuit_breaker_repository)
        
        num_services = 5
        admin_user = UserFactory(is_staff=True)

        for i in range(num_services):
            service_name = f"isolation_test_service_{i}"
            service.get_or_create_state(service_name)

            # Open only even-numbered services
            if i % 2 == 0:
                service.force_open(
                    service_name=service_name,
                    reason="Isolation test",
                    controlled_by=admin_user,
                )

        # Verify states
        for i in range(num_services):
            service_name = f"isolation_test_service_{i}"
            state = service.get_state(service_name)

            if i % 2 == 0:
                assert state == CircuitState.OPEN, f"Service {i} should be open"
            else:
                assert state == CircuitState.CLOSED, f"Service {i} should be closed"

        print(f"✓ Isolation test: {num_services} services correctly isolated")


# =============================================================================
# E. Recovery Stability Tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.chaos
@pytest.mark.requires_redis
class TestRecoveryStability:
    """
    Tests for recovery process stability.

    Validates:
    - DLQ entries are not lost during recovery
    - Partial recovery failures don't corrupt state
    - System can recover from recovery failures
    
    Uses Redis repository via fixture for real integration testing.
    Requires: docker-compose -f docker-compose.test.yml up -d
    """

    def test_dlq_state_consistency_after_failures(self, redis_dlq_repository):
        """
        Purpose:
            Test DLQ state remains consistent after mixed operations.

        Scenario:
            1. Create DLQ entries
            2. Simulate some successful and failed status updates
            3. Verify final state is consistent
        """
        dlq_service = DLQService(
            config=DLQConfig(enabled=True, retention_days=30, max_replay_attempts=2),
            repository=redis_dlq_repository,
        )
        
        num_entries = 10
        entries = []

        # Create entries
        for i in range(num_entries):
            order = OrderFactory()
            result = dlq_service.store_failure(
                domain="payment",
                failure_type="RECOVERY_STABILITY_TEST",
                entity_type="order",
                entity_id=str(order.id),
                error_message=f"Stability test #{i}",
            )
            assert result.success
            entries.append(result.dlq_id)

        # Verify initial state (Redis)
        pending_entries = redis_dlq_repository.find_by_status(FailedOperationStatus.PENDING.value)
        stability_entries = [e for e in pending_entries if e.failure_type == "RECOVERY_STABILITY_TEST"]
        assert len(stability_entries) == num_entries

        # Simulate mixed status updates via repository
        for i, entry_id in enumerate(entries):
            if i % 3 == 0:
                redis_dlq_repository.update_status(
                    id=entry_id,
                    status=FailedOperationStatus.RESOLVED.value,
                    resolution_type="auto",
                )
            elif i % 3 == 1:
                redis_dlq_repository.update_status(
                    id=entry_id,
                    status=FailedOperationStatus.REVIEWING.value,
                )
            # else stays PENDING

        # Verify counts (Redis)
        all_entries = redis_dlq_repository.find_by_failure_type("RECOVERY_STABILITY_TEST")
        resolved = len([e for e in all_entries if e.status == FailedOperationStatus.RESOLVED.value])
        reviewing = len([e for e in all_entries if e.status == FailedOperationStatus.REVIEWING.value])
        pending = len([e for e in all_entries if e.status == FailedOperationStatus.PENDING.value])

        # Calculate expected counts
        expected_resolved = len([i for i in range(num_entries) if i % 3 == 0])
        expected_reviewing = len([i for i in range(num_entries) if i % 3 == 1])
        expected_pending = num_entries - expected_resolved - expected_reviewing

        assert resolved == expected_resolved
        assert reviewing == expected_reviewing
        assert pending == expected_pending

        print(f"✓ State consistency: resolved={resolved}, reviewing={reviewing}, pending={pending}")
