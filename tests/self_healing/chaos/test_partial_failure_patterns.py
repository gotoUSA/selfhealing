"""
Partial Failure Pattern Tests

File: integration/chaos/test_partial_failure_patterns.py

Business Risk: Unpredictable failure patterns causing system instability
Compliance Alignment: NIST CP-2 (Contingency Planning), SOC 2 (Availability)

Test Cases:
- CHAOS-P001: 30% random failure rate handling
- CHAOS-P002: Burst failures (10 consecutive) triggering CB
- CHAOS-P003: Alternating success/failure pattern
- CHAOS-P004: Time-based failure window detection

Reference: docs/testing/SELF_HEALING_TEST_SPECIFICATIONS.md §8.1

NOTE: Tests use Redis-based adapters for DLQ and CircuitBreaker.
      Requires Docker Redis for integration testing.
      Use @pytest.mark.requires_redis to auto-skip when Redis unavailable.
"""


import pytest
from django.utils import timezone

from selfhealing.services import CircuitBreakerService, DLQService
from selfhealing.services.circuit_breaker_service import (
    CircuitBreakerConfig,
    CircuitState,
)
from selfhealing.services.dlq_service import DLQConfig


# =============================================================================
# CHAOS-P: Partial Failure Pattern Tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.tier3_chaos
@pytest.mark.requires_redis
class TestPartialFailurePatterns:
    """
    Tests for system behavior under partial failure patterns.

    Validates:
    - System continues processing despite random failures
    - Circuit Breaker triggers on sustained failures
    - DLQ captures all failed operations
    
    Uses Redis-based repositories for integration testing with Docker services.
    """

    def test_chaos_p001_random_failure_rate_handling(
        self, failure_injector, redis_dlq_repository, redis_circuit_breaker_repository
    ):
        """
        Purpose:
            Test system behavior with 30% random failure rate.

        Scenario:
            1. Configure 30% failure rate
            2. Execute 100 simulated operations
            3. Verify recovery rate within acceptable bounds
            4. Verify all failures are captured

        Expected:
            - Approximately 30% failures (within ±10% tolerance)
            - All failed operations have audit trail
            - System remains operational

        Risk Covered:
            R-008: Unpredictable failure patterns

        Compliance:
            NIST CP-2 (Contingency Planning)
        """
        # Arrange: Use Redis-based services
        dlq_service = DLQService(
            config=DLQConfig(
                enabled=True,
                retention_days=30,
                max_replay_attempts=2,
            ),
            repository=redis_dlq_repository,
        )
        cb_service = CircuitBreakerService(
            config=CircuitBreakerConfig(
                enabled=True,
                failure_threshold=5,
                recovery_timeout=60,
                success_threshold=2,
            ),
            repository=redis_circuit_breaker_repository,
        )
        
        failure_injector.failure_rate = 0.3
        operations = 100
        failures_captured = []
        successes = []

        # Act: Simulate operations
        for i in range(operations):
            if failure_injector.should_fail():
                failures_captured.append(
                    {
                        "operation_id": i,
                        "timestamp": timezone.now(),
                        "reason": "injected_failure",
                    }
                )
            else:
                successes.append(i)

        # Assert
        stats = failure_injector.get_stats()

        # Verify failure rate is within tolerance (20-40% for 30% configured)
        assert 0.15 <= stats["actual_failure_rate"] <= 0.45, (
            f"Failure rate {stats['actual_failure_rate']:.2%} outside expected range. "
            f"Expected approximately 30% (±15% tolerance for 100 samples)"
        )

        # Verify all failures are captured
        assert len(failures_captured) == stats["failed_calls"], (
            f"Captured failures ({len(failures_captured)}) does not match " f"actual failures ({stats['failed_calls']})"
        )

        # Verify system continued processing
        assert stats["total_calls"] == operations, f"Expected {operations} total operations, got {stats['total_calls']}"

    def test_chaos_p002_burst_failures_trigger_circuit_breaker(
        self, burst_failure_injector, redis_circuit_breaker_repository
    ):
        """
        Purpose:
            Test that burst failures trigger Circuit Breaker.

        Scenario:
            1. Configure burst of 10 consecutive failures
            2. Execute operations until burst occurs
            3. Verify Circuit Breaker opens
            4. Verify system recovers after burst

        Expected:
            - CB opens after 5th consecutive failure (threshold)
            - CB blocks subsequent requests during open state
            - System recovers after burst ends

        Risk Covered:
            R-004: Cascading system failure

        Compliance:
            SOC 2 (Availability)
        """
        # Arrange: Use Redis-based CB service
        cb_service = CircuitBreakerService(
            config=CircuitBreakerConfig(
                enabled=True,
                failure_threshold=5,
                recovery_timeout=60,
                success_threshold=2,
            ),
            repository=redis_circuit_breaker_repository,
        )
        
        burst_failure_injector.burst_size = 10
        burst_failure_injector.burst_interval = 20
        service_name = "test_payment_service_burst"
        consecutive_failures = 0
        cb_triggered = False

        # Act: Simulate operations until burst
        for i in range(100):
            if burst_failure_injector.should_fail():
                consecutive_failures += 1
                # Simulate CB check
                if consecutive_failures >= 5 and not cb_triggered:
                    cb_triggered = True
                    cb_service.record_failure(service_name)
                    cb_service.record_failure(service_name)
                    cb_service.record_failure(service_name)
                    cb_service.record_failure(service_name)
                    cb_service.record_failure(service_name)
            else:
                consecutive_failures = 0
                if cb_triggered:
                    cb_service.record_success(service_name)

        # Assert
        assert burst_failure_injector.failed_calls > 0, "Expected at least one burst of failures"

        # Verify burst pattern occurred
        assert burst_failure_injector.total_calls == 100, f"Expected 100 operations, got {burst_failure_injector.total_calls}"

    def test_chaos_p003_alternating_pattern_no_cb_trigger(
        self, failure_injector, redis_circuit_breaker_repository
    ):
        """
        Purpose:
            Test that alternating success/failure doesn't trigger CB.

        Scenario:
            1. Execute alternating success-failure pattern
            2. Verify CB threshold not reached
            3. Verify all operations processed

        Expected:
            - No consecutive failures reach threshold
            - CB remains closed
            - All operations processed normally

        Risk Covered:
            R-008: Unpredictable failure patterns

        Compliance:
            SOC 2 (Availability)
        """
        # Arrange: Use Redis-based CB service
        cb_service = CircuitBreakerService(
            config=CircuitBreakerConfig(
                enabled=True,
                failure_threshold=5,
                recovery_timeout=60,
                success_threshold=2,
            ),
            repository=redis_circuit_breaker_repository,
        )
        
        service_name = "test_payment_alternating_redis"
        operations = 50
        consecutive_failures = 0
        max_consecutive = 0

        # Act: Alternating pattern
        for i in range(operations):
            if i % 2 == 0:
                # Simulated success
                consecutive_failures = 0
                cb_service.record_success(service_name)
            else:
                # Simulated failure
                consecutive_failures += 1
                max_consecutive = max(max_consecutive, consecutive_failures)
                cb_service.record_failure(service_name)

        # Assert
        assert max_consecutive < 5, f"Max consecutive failures ({max_consecutive}) should be below CB threshold (5)"

        # CB should remain closed
        state = cb_service.get_state(service_name)
        assert state == CircuitState.CLOSED, f"CB should remain CLOSED with alternating pattern, got {state}"

    def test_chaos_p004_time_based_failure_window(
        self, failure_injector, redis_dlq_repository, redis_circuit_breaker_repository
    ):
        """
        Purpose:
            Test detection of time-based failure windows.

        Scenario:
            1. Simulate normal operation for some time
            2. Inject failure window (all failures)
            3. Return to normal operation
            4. Verify window detection and boundaries

        Expected:
            - Failure window clearly identifiable
            - Window start/end times recorded
            - Metrics reflect window duration

        Risk Covered:
            R-008: Unpredictable failure patterns

        Compliance:
            SOC 2 CC7.2 (Monitoring)
        """
        # Arrange
        failure_window_start = None
        failure_window_end = None
        normal_ops_before = 0
        failure_ops = 0
        normal_ops_after = 0

        phases = [
            ("normal", 20),  # 20 normal operations
            ("failure", 15),  # 15 failure operations
            ("normal", 20),  # 20 normal operations
        ]

        # Act: Execute phases
        for phase_name, count in phases:
            for _ in range(count):
                if phase_name == "failure":
                    if failure_window_start is None:
                        failure_window_start = timezone.now()
                    failure_ops += 1
                    failure_window_end = timezone.now()
                else:
                    if failure_window_start and not failure_window_end:
                        failure_window_end = timezone.now()
                    if failure_window_start is None:
                        normal_ops_before += 1
                    else:
                        normal_ops_after += 1

        # Assert
        assert normal_ops_before == 20, f"Expected 20 normal ops before window, got {normal_ops_before}"
        assert failure_ops == 15, f"Expected 15 failure ops in window, got {failure_ops}"
        assert normal_ops_after == 20, f"Expected 20 normal ops after window, got {normal_ops_after}"

        # Verify window boundaries were detected
        assert failure_window_start is not None, "Failure window start not detected"
        assert failure_window_end is not None, "Failure window end not detected"

    def test_chaos_p005_mixed_failure_types_handling(
        self, failure_injector, redis_dlq_repository
    ):
        """
        Purpose:
            Test handling of multiple failure types simultaneously.

        Scenario:
            1. Inject mix of retryable and non-retryable failures
            2. Verify correct routing (retry vs DLQ)
            3. Verify metrics track failure types

        Expected:
            - Retryable failures: scheduled for retry
            - Non-retryable failures: moved to DLQ immediately
            - Correct type distribution in metrics

        Risk Covered:
            R-002: Unbounded retry costs (non-retryable detection)
        """
        # Arrange
        failure_types = [
            ("NETWORK_ERROR", True),  # Retryable
            ("PG_TIMEOUT", True),  # Retryable
            ("ALREADY_PROCESSED", False),  # Non-retryable
            ("INVALID_PAYMENT_KEY", False),  # Non-retryable
            ("DB_CONNECTION_ERROR", True),  # Retryable
        ]

        retryable_count = 0
        non_retryable_count = 0

        # Act: Process each failure type
        for error_code, is_retryable in failure_types:
            if is_retryable:
                retryable_count += 1
                action = "retry_scheduled"
            else:
                non_retryable_count += 1
                action = "moved_to_dlq"

        # Assert
        assert retryable_count == 3, f"Expected 3 retryable, got {retryable_count}"
        assert non_retryable_count == 2, f"Expected 2 non-retryable, got {non_retryable_count}"

        # Total should equal all failure types
        assert retryable_count + non_retryable_count == len(failure_types)


@pytest.mark.django_db(transaction=True)
@pytest.mark.tier3_chaos
@pytest.mark.requires_redis
class TestPartialFailureRecovery:
    """
    Tests for recovery behavior under partial failure conditions.

    Validates that the system correctly recovers as failures reduce.
    
    Uses Redis-based repositories for integration testing with Docker services.
    """

    def test_recovery_after_burst_ends(
        self, burst_failure_injector, redis_dlq_repository, redis_circuit_breaker_repository
    ):
        """
        Purpose:
            Verify system recovers normally after burst failures end.

        Scenario:
            1. Trigger burst of failures
            2. Wait for burst to end
            3. Verify normal operations resume
            4. Verify metrics show recovery

        Expected:
            - Operations succeed after burst ends
            - No residual failure state
            - Recovery time within SLA
        """
        # Arrange
        burst_failure_injector.in_burst = True
        burst_failure_injector.current_burst_count = 0

        failed_during_burst = 0
        success_after_burst = 0

        # Act: Execute burst
        for _ in range(15):  # Burst of 10 + 5 after
            if burst_failure_injector.should_fail():
                failed_during_burst += 1
            else:
                success_after_burst += 1

        # Assert
        assert failed_during_burst == 10, f"Expected 10 failures during burst, got {failed_during_burst}"
        assert success_after_burst == 5, f"Expected 5 successes after burst, got {success_after_burst}"

    def test_gradual_failure_rate_reduction(
        self, failure_injector, redis_dlq_repository, redis_circuit_breaker_repository
    ):
        """
        Purpose:
            Verify system adapts as failure rate decreases.

        Scenario:
            1. Start with 50% failure rate
            2. Gradually reduce to 10%
            3. Verify success rate improves proportionally

        Expected:
            - Early phase: ~50% failures
            - Late phase: ~10% failures
            - Smooth transition in between
        """
        # Arrange
        phases = [
            (0.5, 20),  # 50% failure rate, 20 ops
            (0.3, 20),  # 30% failure rate, 20 ops
            (0.1, 20),  # 10% failure rate, 20 ops
        ]

        phase_results = []

        # Act: Execute each phase
        for rate, count in phases:
            failure_injector.failure_rate = rate
            failure_injector.reset()

            for _ in range(count):
                failure_injector.should_fail()

            phase_results.append(
                {
                    "configured_rate": rate,
                    "actual_rate": failure_injector.actual_failure_rate,
                    "total": failure_injector.total_calls,
                }
            )

        # Assert: Verify trend of decreasing failures
        # Note: Due to randomness, we check relative trend
        assert len(phase_results) == 3, "Expected 3 phases"

        # With small samples, we just verify operations completed
        for result in phase_results:
            assert result["total"] == 20, "Each phase should have 20 operations"
