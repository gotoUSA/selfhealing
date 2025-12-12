#!/usr/bin/env python
"""
Stage 28-4 In-Memory Repository Integration Test Script.

Tests the complete self-healing system with in-memory repositories:
- FailedOperation (DLQ)
- CircuitBreakerState
- SecurityIncident

Run with: python scripts/test_inmemory_integration.py
Or via Docker: docker-compose -f docker-compose.test.yml run test-inmemory
"""

import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add package path
sys.path.insert(0, "packages/selfhealing-python/src")

from selfhealing.factory import ProviderRegistry
from selfhealing.adapters.memory import (
    InMemoryFailedOperationRepository,
    InMemoryCircuitBreakerStateRepository,
    InMemorySecurityIncidentRepository,
)
from selfhealing.interfaces.repositories import (
    FailedOperationStatus,
    CircuitBreakerStateEnum,
    SecurityIncidentStatus,
)


class TestColors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    END = "\033[0m"


def print_test(name: str, passed: bool, details: str = ""):
    status = f"{TestColors.GREEN}✓ PASS{TestColors.END}" if passed else f"{TestColors.RED}✗ FAIL{TestColors.END}"
    print(f"  {status} {name}")
    if details and not passed:
        print(f"       {TestColors.YELLOW}{details}{TestColors.END}")


def test_provider_registry_integration():
    """Test that ProviderRegistry correctly provides in-memory repositories."""
    print(f"\n{TestColors.BLUE}=== ProviderRegistry Integration ==={TestColors.END}")

    # Clear existing instances
    ProviderRegistry.clear_instances()
    ProviderRegistry.set_defaults(repo="memory")

    tests_passed = True

    # Test 1: Check memory providers are registered
    providers = ProviderRegistry.list_providers()
    test1 = "memory" in providers["failed_operation_repo"]
    print_test("Memory repo registered for FailedOperation", test1)
    tests_passed &= test1

    # Test 2: Get failed operation repo
    try:
        repo = ProviderRegistry.get_failed_operation_repo(name="memory")
        test2 = isinstance(repo, InMemoryFailedOperationRepository)
        print_test("Get InMemoryFailedOperationRepository", test2)
        tests_passed &= test2
    except Exception as e:
        print_test("Get InMemoryFailedOperationRepository", False, str(e))
        tests_passed = False

    # Test 3: Get circuit breaker repo
    try:
        cb_repo = ProviderRegistry.get_circuit_breaker_repo(name="memory")
        test3 = isinstance(cb_repo, InMemoryCircuitBreakerStateRepository)
        print_test("Get InMemoryCircuitBreakerStateRepository", test3)
        tests_passed &= test3
    except Exception as e:
        print_test("Get InMemoryCircuitBreakerStateRepository", False, str(e))
        tests_passed = False

    # Test 4: Get security repo
    try:
        sec_repo = ProviderRegistry.get_security_repo(name="memory")
        test4 = isinstance(sec_repo, InMemorySecurityIncidentRepository)
        print_test("Get InMemorySecurityIncidentRepository", test4)
        tests_passed &= test4
    except Exception as e:
        print_test("Get InMemorySecurityIncidentRepository", False, str(e))
        tests_passed = False

    # Test 5: Singleton behavior
    repo1 = ProviderRegistry.get_failed_operation_repo(name="memory")
    repo2 = ProviderRegistry.get_failed_operation_repo(name="memory")
    test5 = repo1 is repo2
    print_test("Singleton pattern works", test5)
    tests_passed &= test5

    return tests_passed


def test_dlq_workflow():
    """Test complete DLQ (Dead Letter Queue) workflow."""
    print(f"\n{TestColors.BLUE}=== DLQ Workflow Test ==={TestColors.END}")

    repo = InMemoryFailedOperationRepository()
    tests_passed = True

    # Test 1: Create failed operation
    entry = repo.create(
        domain="payment",
        failure_type="gateway_timeout",
        error_message="Toss payment gateway timeout after 30s",
        error_code="TOSS_TIMEOUT",
        order_id=12345,
        payment_id=67890,
        user_id=100,
        max_retries=3,
        snapshot_data={"amount": 50000, "currency": "KRW"},
    )
    test1 = entry.id == 1 and entry.status == FailedOperationStatus.PENDING.value
    print_test("Create failed operation", test1)
    tests_passed &= test1

    # Test 2: First retry
    result = repo.increment_retry_count(entry.id)
    updated = repo.get_by_id(entry.id)
    test2 = result and updated.retry_count == 1 and updated.last_retry_at is not None
    print_test("First retry increment", test2)
    tests_passed &= test2

    # Test 3: Second retry
    repo.increment_retry_count(entry.id)
    updated = repo.get_by_id(entry.id)
    test3 = updated.retry_count == 2
    print_test("Second retry increment", test3)
    tests_passed &= test3

    # Test 4: Resolve after successful retry
    result = repo.update_status(
        entry.id,
        FailedOperationStatus.RESOLVED.value,
        resolution_type="auto_retry",
        resolution_note="Succeeded on 2nd retry",
        resolved_by_id=None,
    )
    final = repo.get_by_id(entry.id)
    test4 = result and final.status == FailedOperationStatus.RESOLVED.value and final.resolved_at is not None
    print_test("Resolve operation", test4)
    tests_passed &= test4

    # Test 5: Get pending operations by domain
    repo.create(domain="payment", failure_type="error", error_message="Test")
    repo.create(domain="webhook", failure_type="error", error_message="Test")
    pending = repo.get_pending_by_domain("payment")
    test5 = len(pending) == 1  # Only the new pending one
    print_test("Get pending by domain", test5, f"Expected 1, got {len(pending)}")
    tests_passed &= test5

    return tests_passed


def test_circuit_breaker_workflow():
    """Test complete circuit breaker workflow."""
    print(f"\n{TestColors.BLUE}=== Circuit Breaker Workflow Test ==={TestColors.END}")

    repo = InMemoryCircuitBreakerStateRepository()
    service_name = "toss_payment_api"
    tests_passed = True

    # Test 1: Get or create (starts CLOSED)
    state = repo.get_or_create(service_name)
    test1 = state.state == CircuitBreakerStateEnum.CLOSED.value and state.failure_count == 0
    print_test("Initial state is CLOSED", test1)
    tests_passed &= test1

    # Test 2: Record failures
    for i in range(5):
        repo.increment_failure_count(service_name)
    state = repo.get_by_service_name(service_name)
    test2 = state.failure_count == 5 and state.last_failure_at is not None
    print_test("Record 5 failures", test2)
    tests_passed &= test2

    # Test 3: Open circuit
    now = datetime.now(timezone.utc)
    result = repo.update_state(
        service_name,
        CircuitBreakerStateEnum.OPEN.value,
        failure_count=5,
        opened_at=now,
    )
    state = repo.get_by_service_name(service_name)
    test3 = state.state == CircuitBreakerStateEnum.OPEN.value and state.opened_at == now
    print_test("Open circuit after threshold", test3)
    tests_passed &= test3

    # Test 4: Transition to HALF_OPEN
    result = repo.update_state(service_name, CircuitBreakerStateEnum.HALF_OPEN.value)
    state = repo.get_by_service_name(service_name)
    test4 = state.state == CircuitBreakerStateEnum.HALF_OPEN.value
    print_test("Transition to HALF_OPEN", test4)
    tests_passed &= test4

    # Test 5: Reset and close on success
    repo.reset_counts(service_name)
    repo.update_state(service_name, CircuitBreakerStateEnum.CLOSED.value)
    state = repo.get_by_service_name(service_name)
    test5 = state.state == CircuitBreakerStateEnum.CLOSED.value and state.failure_count == 0
    print_test("Reset and close circuit", test5)
    tests_passed &= test5

    # Test 6: Manual control
    result = repo.set_manual_control(
        service_name,
        CircuitBreakerStateEnum.OPEN.value,
        controlled_by_id=42,
        reason="Maintenance window",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    state = repo.get_by_service_name(service_name)
    test6 = state.manually_controlled and state.controlled_by_id == 42
    print_test("Manual control override", test6)
    tests_passed &= test6

    # Test 7: Clear manual control
    result = repo.clear_manual_control(service_name)
    state = repo.get_by_service_name(service_name)
    test7 = not state.manually_controlled and state.state == CircuitBreakerStateEnum.CLOSED.value
    print_test("Clear manual control", test7)
    tests_passed &= test7

    return tests_passed


def test_security_incident_workflow():
    """Test security incident workflow."""
    print(f"\n{TestColors.BLUE}=== Security Incident Workflow Test ==={TestColors.END}")

    repo = InMemorySecurityIncidentRepository()
    tests_passed = True

    # Test 1: Create incident
    incident = repo.create(
        incident_type="webhook_signature_invalid",
        severity="critical",
        description="Invalid HMAC-SHA256 signature on webhook payload",
        source_ip="192.168.1.100",
        user_agent="curl/7.68.0",
        raw_payload={"order_id": "12345", "status": "DONE"},
    )
    test1 = incident.id == 1 and incident.status == SecurityIncidentStatus.OPEN.value
    print_test("Create security incident", test1)
    tests_passed &= test1

    # Test 2: Start investigation
    result = repo.update_status(
        incident.id,
        SecurityIncidentStatus.INVESTIGATING.value,
        investigation_notes="Checking request logs",
        assigned_to_id=1,
    )
    updated = repo.get_by_id(incident.id)
    test2 = result and updated.status == SecurityIncidentStatus.INVESTIGATING.value
    print_test("Start investigation", test2)
    tests_passed &= test2

    # Test 3: Find by source IP
    # Create more incidents from same IP
    repo.create(
        incident_type="rate_limit_abuse",
        severity="high",
        source_ip="192.168.1.100",
    )
    incidents = repo.find_by_source_ip("192.168.1.100")
    test3 = len(incidents) == 2
    print_test("Find by source IP", test3, f"Expected 2, got {len(incidents)}")
    tests_passed &= test3

    # Test 4: Get open incidents
    open_incidents = repo.get_open_incidents()
    test4 = len(open_incidents) == 1  # Only the rate_limit_abuse one
    print_test("Get open incidents", test4)
    tests_passed &= test4

    # Test 5: Resolve incident
    result = repo.mark_as_resolved(incident.id, "False alarm - development testing")
    final = repo.get_by_id(incident.id)
    test5 = final.status == SecurityIncidentStatus.RESOLVED.value and final.resolved_at is not None
    print_test("Mark as resolved", test5)
    tests_passed &= test5

    # Test 6: Get by severity
    repo.create(incident_type="test", severity="critical", source_ip="10.0.0.1")
    critical = repo.get_by_severity("critical")
    test6 = len(critical) == 2  # First incident + new one
    print_test("Get by severity", test6, f"Expected 2, got {len(critical)}")
    tests_passed &= test6

    return tests_passed


def test_concurrent_operations():
    """Test thread safety with concurrent operations."""
    print(f"\n{TestColors.BLUE}=== Concurrent Operations Test ==={TestColors.END}")

    tests_passed = True

    # Test 1: Concurrent DLQ creation
    dlq_repo = InMemoryFailedOperationRepository()
    errors = []
    ids = []

    def create_dlq_entry(n):
        try:
            entry = dlq_repo.create(
                domain="payment",
                failure_type=f"error_{n}",
                error_message=f"Error {n}",
            )
            ids.append(entry.id)
        except Exception as e:
            errors.append(str(e))

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(create_dlq_entry, i) for i in range(100)]
        for future in as_completed(futures):
            pass

    test1 = len(errors) == 0 and len(set(ids)) == 100
    print_test("Concurrent DLQ creation (100 ops)", test1, f"Errors: {len(errors)}, Unique IDs: {len(set(ids))}")
    tests_passed &= test1

    # Test 2: Concurrent circuit breaker updates
    cb_repo = InMemoryCircuitBreakerStateRepository()
    cb_repo.get_or_create("stress_test_service")

    def increment_failures():
        for _ in range(100):
            cb_repo.increment_failure_count("stress_test_service")

    threads = []
    for _ in range(5):
        t = threading.Thread(target=increment_failures)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    state = cb_repo.get_by_service_name("stress_test_service")
    test2 = state.failure_count == 500
    print_test("Concurrent CB increments (500 ops)", test2, f"Expected 500, got {state.failure_count}")
    tests_passed &= test2

    # Test 3: Concurrent security incident creation
    sec_repo = InMemorySecurityIncidentRepository()
    sec_ids = []

    def create_incident(n):
        try:
            incident = sec_repo.create(
                incident_type=f"type_{n % 5}",
                severity="high",
                description=f"Incident {n}",
            )
            sec_ids.append(incident.id)
        except Exception as e:
            errors.append(str(e))

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(create_incident, i) for i in range(50)]
        for future in as_completed(futures):
            pass

    test3 = len(set(sec_ids)) == 50
    print_test("Concurrent security incidents (50 ops)", test3, f"Unique IDs: {len(set(sec_ids))}")
    tests_passed &= test3

    return tests_passed


def test_performance():
    """Test performance metrics."""
    print(f"\n{TestColors.BLUE}=== Performance Test ==={TestColors.END}")

    tests_passed = True
    iterations = 10000

    # Test 1: DLQ creation performance
    repo = InMemoryFailedOperationRepository()
    start = time.perf_counter()
    for i in range(iterations):
        repo.create(domain="payment", failure_type="error", error_message=f"Error {i}")
    elapsed = time.perf_counter() - start
    ops_per_sec = iterations / elapsed
    test1 = ops_per_sec > 10000  # Should be able to do 10k+ ops/sec
    print_test(f"DLQ creation: {ops_per_sec:.0f} ops/sec", test1)
    tests_passed &= test1

    # Test 2: Circuit breaker increment performance
    cb_repo = InMemoryCircuitBreakerStateRepository()
    cb_repo.get_or_create("perf_test")
    start = time.perf_counter()
    for _ in range(iterations):
        cb_repo.increment_failure_count("perf_test")
    elapsed = time.perf_counter() - start
    ops_per_sec = iterations / elapsed
    test2 = ops_per_sec > 10000
    print_test(f"CB increment: {ops_per_sec:.0f} ops/sec", test2)
    tests_passed &= test2

    # Test 3: Get by ID performance
    start = time.perf_counter()
    for i in range(1, min(iterations // 10, 1000) + 1):
        repo.get_by_id(i)
    elapsed = time.perf_counter() - start
    ops_per_sec = (iterations // 10) / elapsed
    test3 = ops_per_sec > 50000  # Should be very fast for in-memory
    print_test(f"Get by ID: {ops_per_sec:.0f} ops/sec", test3)
    tests_passed &= test3

    return tests_passed


def main():
    """Run all Stage 28-4 integration tests."""
    print(f"\n{TestColors.BLUE}{'='*60}{TestColors.END}")
    print(f"{TestColors.BLUE}  Stage 28-4: In-Memory Repository Integration Tests{TestColors.END}")
    print(f"{TestColors.BLUE}{'='*60}{TestColors.END}")

    all_passed = True

    # Run all test suites
    all_passed &= test_provider_registry_integration()
    all_passed &= test_dlq_workflow()
    all_passed &= test_circuit_breaker_workflow()
    all_passed &= test_security_incident_workflow()
    all_passed &= test_concurrent_operations()
    all_passed &= test_performance()

    # Summary
    print(f"\n{TestColors.BLUE}{'='*60}{TestColors.END}")
    if all_passed:
        print(f"{TestColors.GREEN}✓ All Stage 28-4 tests PASSED!{TestColors.END}")
        print(f"{TestColors.GREEN}In-Memory Repository implementation is complete.{TestColors.END}")
    else:
        print(f"{TestColors.RED}✗ Some tests FAILED{TestColors.END}")
    print(f"{TestColors.BLUE}{'='*60}{TestColors.END}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
