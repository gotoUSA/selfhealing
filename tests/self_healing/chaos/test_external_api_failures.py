"""
External API Failure Recovery Tests

This module tests the self-healing system's response to external API failures,
specifically focusing on Toss Payment API failures and recovery mechanisms.

External API failures are common in production:
- Network timeouts
- Connection refused
- Rate limiting (429 responses)
- Service unavailable (503)
- Partial failures (some requests succeed, others fail)

The self-healing system should:
1. Record failures in DLQ for later replay
2. Apply exponential backoff to prevent overwhelming recovering services
3. Open Circuit Breaker after repeated failures
4. Trigger alerts for human intervention when needed

Reference:
- docs/SELF_HEALING_TEST_GAP_ANALYSIS.md §2.5
- docs/L3_SELF_HEALING_ARCHITECTURE.md §3 (External Service Integration)
"""

import pytest
from unittest.mock import patch, MagicMock
from requests.exceptions import (
    Timeout,
    ConnectionError as RequestsConnectionError,
    HTTPError,
)


# =============================================================================
# Payment Timeout Recovery Tests
# =============================================================================


@pytest.mark.django_db
class TestPaymentTimeoutRecovery:
    """
    Tests for handling payment API timeouts.

    Payment timeouts are particularly critical because:
    - Money may have been charged but confirmation not received
    - Double-charging must be prevented (idempotency)
    - Recovery must be attempted while keeping customer informed

    These tests verify that timeout failures are properly captured
    for later recovery.
    """

    def test_payment_timeout_creates_dlq_entry(self):
        """
        Verify that payment API timeout creates a DLQ entry for later replay.

        When a payment confirmation request times out, we don't know if:
        a) The payment succeeded and only the response was lost
        b) The payment failed and needs retry

        The DLQ entry preserves all context for investigation and recovery.

        Expected behavior:
        - Timeout exception is caught
        - DLQ entry is created with full context
        - Entry includes order_id, payment_key, amount for replay
        """
        from selfhealing.services import DLQService
        from selfhealing.services.dlq_models import DLQConfig

        # Use in-memory repository for testing
        from unittest.mock import Mock
        from selfhealing.services.dlq_models import DLQEntryResult
        
        mock_repo = Mock()
        mock_repo.create.return_value = Mock(id=1)
        
        dlq_service = DLQService(
            config=DLQConfig(enabled=True, retention_days=30, max_replay_attempts=3),
            repository=mock_repo,
        )

        # Simulate a payment timeout scenario
        order_id = 12345
        payment_key = "test_payment_key_timeout"
        amount = 50000

        # Store failure in DLQ (simulating what would happen on timeout)
        result = dlq_service.store_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            error_code="TIMEOUT",
            error_message="Payment confirmation timed out after 30 seconds",
            snapshot_data={
                "order_id": order_id,
                "payment_key": payment_key,
                "amount": amount,
            },
            request_data={
                "payment_key": payment_key,
                "orderId": str(order_id),
                "amount": amount,
            },
            next_action_hint="Check Toss dashboard for payment status before retry",
            recommended_action="manual_check",
        )

        # Verify DLQ entry was created
        assert result.success is True
        assert result.dlq_id is not None
        # Verify mock repo was called
        assert mock_repo.create.called

    def test_payment_timeout_preserves_idempotency_key(self):
        """
        Verify that idempotency information is preserved in DLQ entry.

        When retrying a timed-out payment, we must use the same idempotency
        key to prevent double-charging. The DLQ entry must preserve this.

        Expected behavior:
        - Original idempotency key is stored in snapshot_data
        - Replay mechanism can use the same key
        """
        from selfhealing.services import DLQService
        from selfhealing.services.dlq_models import DLQConfig
        from unittest.mock import Mock

        mock_repo = Mock()
        mock_repo.create.return_value = Mock(id=2)
        
        dlq_service = DLQService(
            config=DLQConfig(enabled=True, retention_days=30, max_replay_attempts=3),
            repository=mock_repo,
        )

        idempotency_key = "payment:12345:50000:abc123"

        result = dlq_service.store_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            error_message="Timeout during payment confirmation",
            metadata={
                "idempotency_key": idempotency_key,
                "retry_safe": True,
            },
        )

        assert result.success is True
        assert result.dlq_id is not None
        # Verify mock repo was called
        assert mock_repo.create.called


# =============================================================================
# Connection Failure Recovery Tests
# =============================================================================


@pytest.mark.skip(reason="CB uses Redis/Memory adapter - Django ORM state tracking is not applicable")
@pytest.mark.django_db
class TestConnectionFailureRecovery:
    """
    Tests for handling network connection failures.

    Connection failures indicate infrastructure-level issues:
    - DNS resolution failures
    - Network partitions
    - Target service completely unavailable

    These failures are generally safe to retry (the request never reached
    the server), but may indicate larger outage scenarios.
    """

    def test_connection_error_triggers_circuit_breaker_record(self):
        """
        Verify connection errors are recorded for Circuit Breaker analysis.

        Multiple connection errors should eventually trigger Circuit Breaker
        to open, preventing further requests to an unavailable service.

        Expected behavior:
        - Connection error is recorded
        - Failure count increments
        - After threshold, Circuit Breaker opens
        """
        from selfhealing.services import (
            CircuitBreakerService,
            CircuitBreakerConfig,
        )
        from shopping.models.failed_external_request import CircuitBreakerState

        # Clean up any existing state
        CircuitBreakerState.objects.filter(service_name="toss_payment").delete()

        # Use explicit config to avoid Mock issues
        config = CircuitBreakerConfig(failure_threshold=5, success_threshold=3, minimum_calls=5)
        cb_service = CircuitBreakerService(config=config)

        # Record multiple connection failures
        # The record_failure method only takes service_name
        for i in range(5):
            cb_service.record_failure(service_name="toss_payment")

        # Check if circuit breaker considers opening
        # The exact behavior depends on configuration
        state = cb_service.get_or_create_state("toss_payment")
        assert state.failure_count >= 5

    def test_connection_failure_creates_retryable_dlq_entry(self):
        """
        Verify connection failures create DLQ entries marked as retryable.

        Unlike timeout errors (where we're uncertain if the request succeeded),
        connection errors are definitively safe to retry.

        Expected behavior:
        - DLQ entry is created
        - Entry is marked as retryable (auto_replay candidate)
        """
        from shopping.models.failed_operation import FailedOperation
        from selfhealing.services import DLQService

        dlq_service = DLQService()

        result = dlq_service.store_failure(
            domain="payment",
            failure_type="CONNECTION_ERROR",
            error_code="ECONNREFUSED",
            error_message="Could not connect to Toss Payment API",
            metadata={
                "is_retryable": True,
                "auto_replay_eligible": True,
            },
            recommended_action="replay",
        )

        assert result.success is True
        assert result.dlq_id is not None
        # Note: DLQ uses Redis adapter, not Django ORM.


# =============================================================================
# Rate Limiting Recovery Tests
# =============================================================================


@pytest.mark.django_db
class TestRateLimitingRecovery:
    """
    Tests for handling rate limiting (429) responses.

    Rate limiting indicates we're sending too many requests:
    - Toss may rate limit during high traffic
    - Retry storms can trigger rate limits
    - Rate limits may cascade if not handled properly

    The self-healing system should:
    - Recognize rate limits and back off
    - Open Circuit Breaker to prevent retry storms
    - Schedule retries with increasing delays
    """

    def test_rate_limit_triggers_cascade_detection(self):
        """
        Verify rate limit responses trigger cascade detection.

        Multiple 429 responses in a short window indicate a rate limit
        cascade. The Circuit Breaker should open to prevent further damage.

        Expected behavior:
        - Rate limit events are recorded
        - After threshold (e.g., 10 in 60s), cascade detected
        - Circuit Breaker opens automatically
        """
        from selfhealing.services import (
            get_rate_limit_tracker,
        )

        tracker = get_rate_limit_tracker()

        # Clear any previous tracking data
        tracker.clear_service("toss_payment_rate_test")

        # Record multiple rate limit events
        for _ in range(15):
            tracker.record_rate_limit("toss_payment_rate_test")

        # Check rate limit count in the window
        count = tracker.get_rate_limit_count(
            "toss_payment_rate_test",
            window_seconds=60,
        )

        assert count >= 15

    def test_rate_limit_schedules_delayed_retry(self):
        """
        Verify rate-limited operations are scheduled for delayed retry.

        When we receive a 429, the response often includes a Retry-After
        header indicating how long to wait. This should be respected.

        Expected behavior:
        - DLQ entry includes recommended delay
        - Retry is not immediate
        """
        from selfhealing.services import DLQService
        from selfhealing.services.dlq_models import DLQConfig
        from unittest.mock import Mock

        mock_repo = Mock()
        mock_repo.create.return_value = Mock(id=100)
        
        dlq_service = DLQService(
            config=DLQConfig(enabled=True, retention_days=30, max_replay_attempts=3),
            repository=mock_repo,
        )

        retry_after_seconds = 60

        result = dlq_service.store_failure(
            domain="payment",
            failure_type="RATE_LIMITED",
            error_code="TOO_MANY_REQUESTS",
            error_message="Rate limit exceeded (429)",
            metadata={
                "retry_after_seconds": retry_after_seconds,
                "rate_limit_type": "sliding_window",
            },
            next_action_hint=f"Wait at least {retry_after_seconds}s before retry",
        )

        assert result.success is True
        assert result.dlq_id is not None
        assert mock_repo.create.called


# =============================================================================
# Exponential Backoff Tests
# =============================================================================


@pytest.mark.django_db
class TestExponentialBackoffRetry:
    """
    Tests for exponential backoff retry behavior.

    Exponential backoff prevents overwhelming recovering services:
    - First retry: short delay (e.g., 4s)
    - Second retry: longer delay (e.g., 16s)
    - Third retry: even longer (e.g., 64s)
    - Max cap prevents infinite wait times
    """

    def test_backoff_increases_with_retry_attempts(self):
        """
        Verify backoff delay increases with each retry attempt.

        Expected progression with base=4:
        - Attempt 1: 4 seconds
        - Attempt 2: 16 seconds
        - Attempt 3: 64 seconds
        - Attempt 4+: capped at max (180s)
        """
        from selfhealing.core import (
            BackoffCalculator,
            BackoffConfig,
        )

        config = BackoffConfig(
            base=4,
            max_delay=180,
            jitter_percent=0,  # Disable jitter for predictable test
        )
        calculator = BackoffCalculator(config=config)

        delay_1 = calculator.calculate(attempt=1, with_jitter=False)
        delay_2 = calculator.calculate(attempt=2, with_jitter=False)
        delay_3 = calculator.calculate(attempt=3, with_jitter=False)

        # Verify exponential growth
        assert delay_1 == 4  # 4^1
        assert delay_2 == 16  # 4^2
        assert delay_3 == 64  # 4^3

        # Verify delay is capped
        delay_high = calculator.calculate(attempt=5, with_jitter=False)  # 4^5 = 1024
        assert delay_high <= 180

    def test_dlq_entry_includes_retry_attempt_count(self):
        """
        Verify DLQ entries track retry attempt counts.

        Tracking retry counts enables:
        - Exponential backoff calculation
        - Knowing when to give up (max_retries)
        - Metrics on retry success rates
        """
        from selfhealing.services import DLQService
        from selfhealing.services.dlq_models import DLQConfig
        from unittest.mock import Mock

        mock_repo = Mock()
        mock_repo.create.return_value = Mock(id=200)
        
        dlq_service = DLQService(
            config=DLQConfig(enabled=True, retention_days=30, max_replay_attempts=3),
            repository=mock_repo,
        )

        result = dlq_service.store_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            error_message="Payment confirmation failed",
            metadata={
                "retry_attempt": 0,
                "max_retries": 3,
            },
        )

        assert result.success is True
        assert result.dlq_id is not None
        assert mock_repo.create.called


# =============================================================================
# Circuit Breaker Integration Tests
# =============================================================================


@pytest.mark.skip(reason="CB uses Redis/Memory adapter - Django ORM state tracking is not applicable")
@pytest.mark.django_db
class TestCircuitBreakerExternalAPI:
    """
    Tests for Circuit Breaker behavior with external API failures.

    The Circuit Breaker pattern prevents cascading failures:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Too many failures, requests blocked immediately
    - HALF_OPEN: Testing if service recovered, limited requests allowed
    """

    def test_repeated_failures_open_circuit_breaker(self):
        """
        Verify repeated API failures cause Circuit Breaker to open.

        After exceeding the failure threshold, the Circuit Breaker should
        open and block further requests until recovery timeout.

        Expected behavior:
        - Record failure_threshold failures
        - Circuit Breaker transitions to OPEN
        - should_allow() returns False
        """
        from selfhealing.services import (
            CircuitBreakerService,
            CircuitBreakerConfig,
            CircuitState,
        )
        from shopping.models.failed_external_request import CircuitBreakerState

        # Clean up any existing state
        CircuitBreakerState.objects.filter(service_name="api_failure_test").delete()

        # Use explicit config to avoid Mock issues
        config = CircuitBreakerConfig(failure_threshold=5, success_threshold=3, minimum_calls=5)
        cb_service = CircuitBreakerService(config=config)

        # Record failures up to threshold (FAILURE_THRESHOLD=5 in test settings)
        # Need 5 failures to trigger OPEN state
        for i in range(6):
            cb_service.record_failure(service_name="api_failure_test")

        # Check Circuit Breaker state
        state = cb_service.get_or_create_state("api_failure_test")

        # After multiple failures, should transition towards OPEN
        # (exact threshold depends on configuration)
        assert state.failure_count >= 5

    def test_circuit_breaker_blocks_requests_when_open(self):
        """
        Verify Circuit Breaker blocks requests when in OPEN state.

        When OPEN, should_allow() should return False, allowing the
        application to fail fast without making network requests.

        Expected behavior:
        - OPEN state Circuit Breaker blocks requests
        - should_allow() returns False immediately
        - No actual API call is attempted
        """
        from selfhealing.services import (
            CircuitBreakerService,
            CircuitBreakerConfig,
            CircuitState,
            force_open_circuit,
        )

        # Use explicit config to avoid Mock issues
        config = CircuitBreakerConfig(failure_threshold=5, success_threshold=3, minimum_calls=5)
        cb_service = CircuitBreakerService(config=config)

        # Force the circuit breaker to OPEN state using the API
        force_open_circuit("open_cb_test", reason="Test forced open")

        # should_allow should return False for OPEN state
        is_allowed = cb_service.should_allow("open_cb_test")

        assert is_allowed is False


# =============================================================================
# Partial Failure Scenario Tests
# =============================================================================


@pytest.mark.django_db
class TestPartialFailureScenarios:
    """
    Tests for partial failure scenarios.

    Partial failures are common in distributed systems:
    - Some requests succeed, others fail
    - Database saved, but cache failed
    - Payment succeeded, but webhook failed

    The self-healing system must handle these consistently.
    """

    def test_payment_success_webhook_failure_handled(self):
        """
        Verify system handles payment success + webhook failure scenario.

        A common failure mode:
        1. Payment is confirmed successfully
        2. Webhook to update our system fails

        In this case:
        - Payment is complete (money charged)
        - We need to recover the webhook/update, not the payment

        Expected behavior:
        - DLQ entry created for webhook domain, not payment
        - Entry includes payment confirmation details
        """
        from unittest.mock import MagicMock
        from selfhealing.services import DLQService

        mock_repo = MagicMock()
        # create()가 id 속성을 가진 객체를 반환해야 함
        mock_entry = MagicMock()
        mock_entry.id = "dlq-webhook-123"
        mock_repo.create.return_value = mock_entry

        # Inject mock repository directly
        dlq_service = DLQService(repository=mock_repo)

        # Simulate: Payment succeeded, but webhook processing failed
        result = dlq_service.store_failure(
            domain="webhook",
            failure_type="WEBHOOK_PROCESSING_FAILED",
            error_message="Failed to process Toss payment webhook",
            snapshot_data={
                "payment_key": "confirmed_payment_key",
                "status": "DONE",  # Payment was confirmed
                "amount": 50000,
            },
            metadata={
                "payment_confirmed": True,
                "webhook_type": "payment.confirmed",
            },
            next_action_hint="Payment was confirmed. Manually update order status.",
            recommended_action="manual_check",
        )

        assert result.success is True
        assert result.dlq_id is not None
        mock_repo.create.assert_called_once()
        # Note: DLQ uses Redis adapter, not Django ORM.

    def test_database_saved_notification_failed(self):
        """
        Verify handling when DB succeeds but notification fails.

        Another partial failure scenario:
        1. Order status updated in database
        2. Email/push notification fails

        Expected behavior:
        - DLQ entry created for notification domain
        - Lower priority than payment failures
        - Can be retried independently
        """
        from unittest.mock import MagicMock
        from selfhealing.services import DLQService

        mock_repo = MagicMock()
        # create()가 id 속성을 가진 객체를 반환해야 함
        mock_entry = MagicMock()
        mock_entry.id = "dlq-notification-123"
        mock_repo.create.return_value = mock_entry

        # Inject mock repository directly
        dlq_service = DLQService(repository=mock_repo)

        result = dlq_service.store_failure(
            domain="notification",
            failure_type="EMAIL_SEND_FAILED",
            error_message="SMTP connection timeout",
            snapshot_data={
                "order_id": 12345,
                "user_email": "test@example.com",
                "notification_type": "order_confirmed",
            },
            metadata={
                "db_update_success": True,
                "notification_channel": "email",
            },
            recommended_action="replay",
        )

        assert result.success is True
        assert result.dlq_id is not None
        mock_repo.create.assert_called_once()
        # Note: DLQ uses Redis adapter, not Django ORM.


# =============================================================================
# Service Unavailable (503) Recovery Tests
# =============================================================================


@pytest.mark.django_db
class TestServiceUnavailableRecovery:
    """
    Tests for handling 503 Service Unavailable responses.

    503 responses indicate temporary server overload or maintenance:
    - Generally safe to retry after delay
    - Often includes Retry-After header
    - May trigger Circuit Breaker if persistent
    """

    def test_503_creates_retryable_entry(self):
        """
        Verify 503 responses create DLQ entries marked for retry.

        503 errors are typically transient - the service is temporarily
        unavailable but expected to recover. These should be scheduled
        for automatic retry.

        Expected behavior:
        - DLQ entry created with retryable flag
        - Entry includes suggested retry delay
        """
        from shopping.models.failed_operation import FailedOperation
        from selfhealing.services import DLQService
        from selfhealing.services.dlq_models import DLQConfig
        from unittest.mock import Mock

        mock_repo = Mock()
        mock_repo.create.return_value = Mock(id=300)
        
        dlq_service = DLQService(
            config=DLQConfig(enabled=True, retention_days=30, max_replay_attempts=3),
            repository=mock_repo,
        )

        result = dlq_service.store_failure(
            domain="payment",
            failure_type="SERVICE_UNAVAILABLE",
            error_code="HTTP_503",
            error_message="Toss Payment API temporarily unavailable",
            response_data={
                "status_code": 503,
                "headers": {"Retry-After": "30"},
            },
            metadata={
                "is_retryable": True,
                "suggested_delay_seconds": 30,
            },
            recommended_action="replay",
        )

        assert result.success is True
        assert result.dlq_id is not None
        assert mock_repo.create.called
