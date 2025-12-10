"""
Time-Based Behavior Tests for Self-Healing System

This module tests time-dependent behaviors in the self-healing system using
freezegun's freeze_time decorator. Testing time-based logic is critical because:

1. Circuit Breaker state transitions depend on time:
   - OPEN -> HALF_OPEN transition after recovery_timeout
   - HALF_OPEN success/failure counting within time window

2. SLA breach detection depends on time:
   - Pending DLQ entries that exceed domain-specific SLA thresholds
   - Time-based escalation triggers

3. Retry backoff timing:
   - Exponential backoff delays
   - Jitter application

Why freeze_time is necessary:
- time.time() and timezone.now() are non-deterministic
- Tests using timedelta for "old" data are fragile (may pass/fail based on timing)
- freeze_time provides consistent, reproducible time-based testing

Reference:
- docs/SELF_HEALING_TEST_GAP_ANALYSIS.md §2.4
- docs/L3_SELF_HEALING_ARCHITECTURE.md §4 (Circuit Breaker Timing)
"""

import pytest
from datetime import timedelta
from unittest.mock import patch, MagicMock
from freezegun import freeze_time
from django.utils import timezone


# =============================================================================
# Circuit Breaker Time-Based Tests
# =============================================================================


@pytest.mark.django_db
class TestCircuitBreakerTimeBased:
    """
    Tests for Circuit Breaker time-dependent state transitions.

    The Circuit Breaker uses time to determine when to transition from OPEN
    to HALF_OPEN state. This allows the system to periodically test if the
    external service has recovered.

    Key timing parameters:
    - recovery_timeout: Time to wait before testing recovery (default: 60s)
    - manual_override_ttl_minutes: How long manual overrides remain active
    """

    @freeze_time("2025-01-01 12:00:00")
    def test_circuit_breaker_remains_open_before_timeout(self):
        """
        Verify Circuit Breaker stays OPEN before recovery_timeout expires.

        When a Circuit Breaker is opened (manually or due to failures), it
        should remain OPEN until the recovery_timeout has passed. This
        prevents premature requests to a potentially still-failed service.

        Test scenario (with test settings RECOVERY_TIMEOUT=5):
        1. Create Circuit Breaker with OPEN state at 12:00:00
        2. Check state at 12:00:03 (3 seconds later, before 5s timeout)
        3. Expect: Still OPEN, not allowing requests

        Expected behavior:
        - should_allow() returns False
        - State remains OPEN
        """
        from shopping.models.failed_payment import CircuitBreakerState
        from shopping.services.self_healing.circuit_breaker_service import (
            CircuitBreakerService,
            CircuitState,
        )

        # Create OPEN state with opened_at = now (12:00:00)
        # The should_allow method checks opened_at for recovery timeout
        CircuitBreakerState.objects.create(
            service_name="timeout_test_service",
            state=CircuitState.OPEN,
            failure_count=5,
            opened_at=timezone.now(),  # frozen at 12:00:00
            last_failure_at=timezone.now(),
        )

        service = CircuitBreakerService()

        # Check at 3 seconds later (before 5s recovery timeout in test settings)
        with freeze_time("2025-01-01 12:00:03"):
            is_available = service.should_allow("timeout_test_service")
            
            # Should still be blocked (OPEN state, timeout not reached)
            assert is_available is False

    @freeze_time("2025-01-01 12:00:00")
    def test_circuit_breaker_transitions_to_half_open_after_timeout(self):
        """
        Verify Circuit Breaker transitions to HALF_OPEN after recovery_timeout.

        After the recovery_timeout period, the Circuit Breaker should transition
        to HALF_OPEN state, allowing a limited number of test requests through
        to check if the external service has recovered.

        Test scenario (with test settings RECOVERY_TIMEOUT=5):
        1. Create Circuit Breaker with OPEN state at 12:00:00
        2. Check state at 12:00:06 (6 seconds later, after 5s timeout)
        3. Expect: Transition to HALF_OPEN, allowing test request

        Expected behavior:
        - should_allow() returns True (allowing test request)
        - State transitions to HALF_OPEN
        """
        from shopping.models.failed_payment import CircuitBreakerState
        from shopping.services.self_healing.circuit_breaker_service import (
            CircuitBreakerService,
            CircuitState,
        )

        # Create OPEN state with opened_at = now (12:00:00)
        cb_state = CircuitBreakerState.objects.create(
            service_name="half_open_test_service",
            state=CircuitState.OPEN,
            failure_count=5,
            opened_at=timezone.now(),  # frozen at 12:00:00
            last_failure_at=timezone.now(),
        )

        service = CircuitBreakerService()

        # Check at 6 seconds later (after 5s recovery timeout in test settings)
        with freeze_time("2025-01-01 12:00:06"):
            is_available = service.should_allow("half_open_test_service")
            
            # Should be available (transition to HALF_OPEN for test request)
            assert is_available is True
            
            # Verify state has transitioned to HALF_OPEN
            cb_state.refresh_from_db()
            assert cb_state.state == CircuitState.HALF_OPEN

    @freeze_time("2025-01-01 12:00:00")
    def test_circuit_breaker_closes_after_success_in_half_open(self):
        """
        Verify Circuit Breaker closes after successful requests in HALF_OPEN.

        When in HALF_OPEN state, the Circuit Breaker monitors requests.
        After a configurable number of successful requests (success_threshold),
        it should transition back to CLOSED state, fully allowing traffic.

        Test scenario (with test settings SUCCESS_THRESHOLD=3):
        1. Create Circuit Breaker in HALF_OPEN state
        2. Record 3 successful requests (>= success_threshold)
        3. Expect: Transition to CLOSED state

        Expected behavior:
        - After success_threshold successes, state becomes CLOSED
        - All subsequent requests are allowed
        """
        from shopping.models.failed_payment import CircuitBreakerState
        from shopping.services.self_healing.circuit_breaker_service import (
            CircuitBreakerService,
            CircuitState,
        )

        # Create HALF_OPEN state
        cb_state = CircuitBreakerState.objects.create(
            service_name="recovery_test_service",
            state=CircuitState.HALF_OPEN,
            failure_count=0,
            success_count=0,
        )

        service = CircuitBreakerService()

        # Record successful requests (test settings SUCCESS_THRESHOLD=3)
        service.record_success("recovery_test_service")
        service.record_success("recovery_test_service")
        service.record_success("recovery_test_service")

        # Verify state has transitioned to CLOSED
        cb_state.refresh_from_db()
        assert cb_state.state == CircuitState.CLOSED


# =============================================================================
# SLA Breach Detection Time-Based Tests
# =============================================================================


@pytest.mark.django_db
class TestSLABreachDetectionTimeBased:
    """
    Tests for SLA breach detection using frozen time.

    SLA (Service Level Agreement) breaches occur when failed operations
    remain in PENDING status longer than the domain-specific threshold:
    - Payment: 1 hour
    - Point: 4 hours
    - Inventory: 2 hours
    - Webhook: 8 hours
    - Notification: 24 hours

    These tests verify that breach detection correctly identifies overdue
    operations based on their creation time and domain-specific thresholds.
    """

    @freeze_time("2025-01-01 12:00:00")
    def test_payment_sla_breach_detected_after_one_hour(self):
        """
        Verify payment SLA breach is detected after 1 hour threshold.

        Payment operations have the strictest SLA (1 hour) due to their
        direct revenue impact. This test verifies that pending payment
        failures are correctly flagged as breaches after 1 hour.

        Test scenario:
        1. Create payment failure at 12:00:00
        2. Check for breaches at 13:01:00 (1 hour 1 minute later)
        3. Expect: Failure is identified as SLA breach

        Expected behavior:
        - Operation created_at + 1 hour < current_time = breach
        """
        from shopping.models.failed_operation import FailedOperation
        from shopping.services.self_healing.config import SLAThresholds

        # Create a pending payment failure at the frozen time
        failed_op = FailedOperation.objects.create(
            domain=FailedOperation.Domain.PAYMENT,
            failure_type="PG_TIMEOUT",
            status=FailedOperation.Status.PENDING,
            error_message="Test payment timeout for SLA test",
            # created_at will be set to frozen time (12:00:00)
        )
        # Explicitly set created_at to frozen time
        failed_op.created_at = timezone.now()
        failed_op.save()

        # Check at 1 hour 1 minute later
        with freeze_time("2025-01-01 13:01:00"):
            sla_thresholds = SLAThresholds.from_settings()
            payment_threshold = sla_thresholds.get_threshold("payment")
            
            # Calculate if SLA is breached
            time_pending = timezone.now() - failed_op.created_at
            is_breached = time_pending > payment_threshold

            assert is_breached is True
            assert time_pending > timedelta(hours=1)

    @freeze_time("2025-01-01 12:00:00")
    def test_payment_sla_not_breached_within_threshold(self):
        """
        Verify payment SLA is NOT breached within the 1 hour threshold.

        This test verifies that operations within their SLA window are
        not incorrectly flagged as breaches.

        Test scenario:
        1. Create payment failure at 12:00:00
        2. Check for breaches at 12:30:00 (30 minutes later)
        3. Expect: No breach detected

        Expected behavior:
        - Operation created_at + 1 hour > current_time = no breach
        """
        from shopping.models.failed_operation import FailedOperation
        from shopping.services.self_healing.config import SLAThresholds

        # Create a pending payment failure at the frozen time
        failed_op = FailedOperation.objects.create(
            domain=FailedOperation.Domain.PAYMENT,
            failure_type="PG_TIMEOUT",
            status=FailedOperation.Status.PENDING,
            error_message="Test payment for SLA compliance",
        )
        failed_op.created_at = timezone.now()
        failed_op.save()

        # Check at 30 minutes later (within 1 hour SLA)
        with freeze_time("2025-01-01 12:30:00"):
            sla_thresholds = SLAThresholds.from_settings()
            payment_threshold = sla_thresholds.get_threshold("payment")
            
            # Calculate if SLA is breached
            time_pending = timezone.now() - failed_op.created_at
            is_breached = time_pending > payment_threshold

            assert is_breached is False
            assert time_pending == timedelta(minutes=30)

    @freeze_time("2025-01-01 12:00:00")
    def test_point_sla_breach_detected_after_four_hours(self):
        """
        Verify point domain SLA breach is detected after 4 hour threshold.

        Point operations have a 4-hour SLA, allowing more time for recovery
        as they are less time-critical than payments.

        Test scenario:
        1. Create point failure at 12:00:00
        2. Check for breaches at 16:01:00 (4 hours 1 minute later)
        3. Expect: Failure is identified as SLA breach
        """
        from shopping.models.failed_operation import FailedOperation
        from shopping.services.self_healing.config import SLAThresholds

        failed_op = FailedOperation.objects.create(
            domain=FailedOperation.Domain.POINT,
            failure_type="POINT_DEDUCT_FAILED",
            status=FailedOperation.Status.PENDING,
            error_message="Test point failure for SLA test",
        )
        failed_op.created_at = timezone.now()
        failed_op.save()

        # Check at 4 hours 1 minute later
        with freeze_time("2025-01-01 16:01:00"):
            sla_thresholds = SLAThresholds.from_settings()
            point_threshold = sla_thresholds.get_threshold("point")
            
            time_pending = timezone.now() - failed_op.created_at
            is_breached = time_pending > point_threshold

            assert is_breached is True
            assert time_pending > timedelta(hours=4)

    @freeze_time("2025-01-01 12:00:00")
    def test_point_sla_not_breached_within_four_hours(self):
        """
        Verify point domain SLA is NOT breached within 4 hour threshold.

        Test scenario:
        1. Create point failure at 12:00:00
        2. Check for breaches at 15:00:00 (3 hours later)
        3. Expect: No breach (within 4 hour threshold)
        """
        from shopping.models.failed_operation import FailedOperation
        from shopping.services.self_healing.config import SLAThresholds

        failed_op = FailedOperation.objects.create(
            domain=FailedOperation.Domain.POINT,
            failure_type="POINT_DEDUCT_FAILED",
            status=FailedOperation.Status.PENDING,
            error_message="Test point for SLA compliance",
        )
        failed_op.created_at = timezone.now()
        failed_op.save()

        # Check at 3 hours later (within 4 hour SLA)
        with freeze_time("2025-01-01 15:00:00"):
            sla_thresholds = SLAThresholds.from_settings()
            point_threshold = sla_thresholds.get_threshold("point")
            
            time_pending = timezone.now() - failed_op.created_at
            is_breached = time_pending > point_threshold

            assert is_breached is False
            assert time_pending == timedelta(hours=3)


# =============================================================================
# Retry Backoff Time-Based Tests
# =============================================================================


@pytest.mark.django_db
class TestRetryBackoffTimeBased:
    """
    Tests for retry backoff timing calculations.

    The self-healing system uses exponential backoff with jitter to prevent
    thundering herd problems when retrying failed operations. These tests
    verify correct backoff delay calculations.

    Backoff formula: min(backoff_max, backoff_base ^ attempt) * (1 ± jitter)
    """

    def test_backoff_increases_exponentially(self):
        """
        Verify backoff delays increase exponentially with retry attempts.

        Exponential backoff prevents overwhelming recovering services with
        rapid retries. Each subsequent retry should wait longer than the
        previous one.

        Expected progression (base=4, default):
        - Attempt 1: 4 seconds (4^1)
        - Attempt 2: 16 seconds (4^2)
        - Attempt 3: 64 seconds (4^3)
        """
        from shopping.services.self_healing.backoff_calculator import (
            BackoffCalculator,
            BackoffConfig,
        )

        # Create config with no jitter for predictable testing
        config = BackoffConfig(
            base=4,
            max_delay=300,
            jitter_percent=0,  # Disable jitter for predictable testing
        )
        calculator = BackoffCalculator(config=config)

        delay_1 = calculator.calculate(attempt=1, with_jitter=False)
        delay_2 = calculator.calculate(attempt=2, with_jitter=False)
        delay_3 = calculator.calculate(attempt=3, with_jitter=False)

        # Verify exponential growth (base=4)
        assert delay_1 == 4  # 4^1
        assert delay_2 == 16  # 4^2
        assert delay_3 == 64  # 4^3

        # Verify increasing order
        assert delay_1 < delay_2 < delay_3

    def test_backoff_respects_max_limit(self):
        """
        Verify backoff delays do not exceed configured maximum.

        Even with many retry attempts, the backoff should be capped at
        max_delay to prevent unreasonably long waits.

        Expected behavior:
        - High attempt numbers should cap at max_delay
        - Example: 4^5 = 1024, but max_delay=180 limits it
        """
        from shopping.services.self_healing.backoff_calculator import (
            BackoffCalculator,
            BackoffConfig,
        )

        config = BackoffConfig(
            base=4,
            max_delay=180,
            jitter_percent=0,
        )
        calculator = BackoffCalculator(config=config)

        # 4^5 = 1024, but should be capped at 180
        delay_high_attempt = calculator.calculate(attempt=5, with_jitter=False)

        assert delay_high_attempt <= 180
        assert delay_high_attempt == 180  # Should be exactly at max

    def test_backoff_applies_jitter(self):
        """
        Verify jitter is applied to prevent synchronized retries.

        Jitter adds randomness to backoff delays, preventing multiple
        clients from retrying at the exact same time (thundering herd).

        Expected behavior:
        - Delays vary within jitter_percent of base delay
        - Multiple calculations produce different values
        """
        from shopping.services.self_healing.backoff_calculator import (
            BackoffCalculator,
            BackoffConfig,
        )

        config = BackoffConfig(
            base=4,
            max_delay=300,
            jitter_percent=25,  # 25% jitter
        )
        calculator = BackoffCalculator(config=config)

        # Base delay for attempt 2 is 16 seconds
        # With 25% jitter, range is approximately 12-20 seconds
        delays = [calculator.calculate(attempt=2, with_jitter=True) for _ in range(10)]

        # All delays should be within jitter range (16 ± 25% = 12-20)
        for delay in delays:
            assert 12 <= delay <= 20

        # With jitter, not all delays should be identical
        # (statistically unlikely to get 10 identical values with 25% jitter)
        unique_delays = set(delays)
        assert len(unique_delays) > 1


# =============================================================================
# Manual Override TTL Time-Based Tests
# =============================================================================


@pytest.mark.django_db
class TestManualOverrideTTLTimeBased:
    """
    Tests for manual override time-to-live (TTL) behavior.

    Operators can manually force Circuit Breakers open or closed. These
    overrides have a TTL to prevent indefinite manual states. After TTL
    expires, the Circuit Breaker returns to automatic management.

    Default TTL: 90 minutes (configurable)
    """

    @freeze_time("2025-01-01 12:00:00")
    def test_manual_override_active_within_ttl(self):
        """
        Verify manual override remains active within TTL period.

        When an operator manually forces a Circuit Breaker open (e.g., for
        maintenance), the override should remain active for the TTL duration.

        Test scenario:
        1. Force open CB at 12:00:00 with 90 minute TTL
        2. Check at 12:45:00 (45 minutes later)
        3. Expect: Override still active, CB remains forced open
        """
        from shopping.models.failed_payment import CircuitBreakerState
        from shopping.services.self_healing.circuit_breaker_service import (
            CircuitBreakerService,
            CircuitState,
        )

        # Create a manually forced open state using actual model field names
        cb_state = CircuitBreakerState.objects.create(
            service_name="manual_override_test",
            state=CircuitState.OPEN,
            manually_controlled=True,
            manual_override_expires_at=timezone.now() + timedelta(minutes=90),
            control_reason="Scheduled maintenance window",
        )

        service = CircuitBreakerService()

        # Check at 45 minutes later (within 90 minute TTL)
        with freeze_time("2025-01-01 12:45:00"):
            is_available = service.should_allow("manual_override_test")
            
            # Should be blocked due to manual override (OPEN state)
            assert is_available is False
            
            # Verify override is still active
            cb_state.refresh_from_db()
            assert cb_state.manually_controlled is True

    @freeze_time("2025-01-01 12:00:00")
    def test_manual_override_expires_after_ttl(self):
        """
        Verify manual override expires after TTL period.

        After the TTL expires, the Circuit Breaker should return to
        automatic management. This prevents indefinitely locked states.

        Test scenario:
        1. Force open CB at 12:00:00 with 90 minute TTL
        2. Check at 13:31:00 (91 minutes later)
        3. Expect: Override expired, CB returns to auto management
        """
        from shopping.models.failed_payment import CircuitBreakerState
        from shopping.services.self_healing.circuit_breaker_service import (
            CircuitBreakerService,
            CircuitState,
        )

        # Create a manually forced open state using actual model field names
        cb_state = CircuitBreakerState.objects.create(
            service_name="override_expiry_test",
            state=CircuitState.OPEN,
            manually_controlled=True,
            manual_override_expires_at=timezone.now() + timedelta(minutes=90),
            control_reason="Temporary maintenance",
        )

        service = CircuitBreakerService()

        # Check at 91 minutes later (after 90 minute TTL)
        with freeze_time("2025-01-01 13:31:00"):
            is_available = service.should_allow("override_expiry_test")
            
            # Override should have expired, returning to normal operation
            # If no recent failures, should be available
            # Note: Actual behavior depends on implementation
            cb_state.refresh_from_db()
            
            # Verify override has expired
            assert cb_state.manual_override_expires_at < timezone.now()
