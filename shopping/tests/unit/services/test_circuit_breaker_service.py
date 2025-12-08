"""
Circuit Breaker Service Unit Tests

Tests for CircuitBreakerService functionality including:
- Force open/close operations
- State transitions
- Conditional replay triggering
- Admin action integration

Reference: docs/L3_SELF_HEALING_OPERATIONS.md §9
"""

import pytest
from django.utils import timezone
from unittest.mock import patch, MagicMock

from shopping.models.failed_payment import CircuitBreakerState
from shopping.services.self_healing.circuit_breaker_service import (
    CircuitBreakerService,
    CircuitBreakerConfig,
    CircuitBreakerResult,
    CircuitState,
    get_circuit_breaker_service,
    should_allow_request,
    force_open_circuit,
    force_close_circuit,
)
from shopping.tests.factories import UserFactory


# =============================================================================
# Configuration Tests
# =============================================================================


class TestCircuitBreakerConfig:
    """Tests for CircuitBreakerConfig dataclass."""

    def test_default_values(self):
        """
        Purpose:
            Verify default configuration values are correct.
        """
        config = CircuitBreakerConfig()

        assert config.enabled is False
        assert config.failure_threshold == 5
        assert config.recovery_timeout == 60
        assert config.success_threshold == 2

    def test_custom_values(self):
        """
        Purpose:
            Verify custom configuration values are applied.
        """
        config = CircuitBreakerConfig(
            enabled=True,
            failure_threshold=10,
            recovery_timeout=120,
            success_threshold=3,
        )

        assert config.enabled is True
        assert config.failure_threshold == 10
        assert config.recovery_timeout == 120
        assert config.success_threshold == 3


class TestCircuitBreakerResult:
    """Tests for CircuitBreakerResult dataclass."""

    def test_succeeded_factory(self):
        """
        Purpose:
            Verify succeeded factory creates correct result.
        """
        result = CircuitBreakerResult.succeeded(
            service_name="toss_payment",
            previous_state="closed",
            new_state="open",
            message="Circuit opened",
        )

        assert result.success is True
        assert result.service_name == "toss_payment"
        assert result.previous_state == "closed"
        assert result.new_state == "open"
        assert result.message == "Circuit opened"
        assert result.error is None

    def test_failed_factory(self):
        """
        Purpose:
            Verify failed factory creates correct result.
        """
        result = CircuitBreakerResult.failed(
            service_name="toss_payment",
            error="Service not found",
        )

        assert result.success is False
        assert result.service_name == "toss_payment"
        assert result.error == "Service not found"


# =============================================================================
# Circuit Breaker Service Tests
# =============================================================================


@pytest.mark.django_db
class TestCircuitBreakerService:
    """Tests for CircuitBreakerService operations."""

    def setup_method(self):
        """Set up test fixtures."""
        self.config = CircuitBreakerConfig(
            enabled=True,
            failure_threshold=5,
            recovery_timeout=60,
            success_threshold=2,
        )
        self.service = CircuitBreakerService(config=self.config)

    # =========================================================================
    # State Query Tests
    # =========================================================================

    def test_get_or_create_state_creates_new(self):
        """
        Purpose:
            Verify get_or_create_state creates new circuit breaker.
        """
        state = self.service.get_or_create_state("new_service")

        assert state is not None
        assert state.service_name == "new_service"
        assert state.state == CircuitState.CLOSED
        assert state.failure_count == 0

    def test_get_or_create_state_returns_existing(self):
        """
        Purpose:
            Verify get_or_create_state returns existing circuit breaker.
        """
        # Create existing state
        CircuitBreakerState.objects.create(
            service_name="existing_service",
            state="open",
            failure_count=5,
        )

        state = self.service.get_or_create_state("existing_service")

        assert state.state == "open"
        assert state.failure_count == 5

    def test_get_state_returns_current_state(self):
        """
        Purpose:
            Verify get_state returns correct state string.
        """
        CircuitBreakerState.objects.create(
            service_name="test_service",
            state="half_open",
        )

        state = self.service.get_state("test_service")

        assert state == "half_open"

    def test_should_allow_when_closed(self):
        """
        Purpose:
            Verify requests are allowed when circuit is closed.
        """
        CircuitBreakerState.objects.create(
            service_name="test_service",
            state="closed",
        )

        result = self.service.should_allow("test_service")

        assert result is True

    def test_should_block_when_open(self):
        """
        Purpose:
            Verify requests are blocked when circuit is open.
        """
        CircuitBreakerState.objects.create(
            service_name="test_service",
            state="open",
            opened_at=timezone.now(),
        )

        result = self.service.should_allow("test_service")

        assert result is False

    def test_should_allow_when_disabled(self):
        """
        Purpose:
            Verify all requests are allowed when circuit breaker is disabled.
        """
        disabled_config = CircuitBreakerConfig(enabled=False)
        service = CircuitBreakerService(config=disabled_config)

        CircuitBreakerState.objects.create(
            service_name="test_service",
            state="open",
            opened_at=timezone.now(),
        )

        result = service.should_allow("test_service")

        assert result is True

    def test_get_all_states(self):
        """
        Purpose:
            Verify get_all_states returns all circuit breakers.
        """
        CircuitBreakerState.objects.create(service_name="service_1", state="closed")
        CircuitBreakerState.objects.create(service_name="service_2", state="open")

        states = self.service.get_all_states()

        assert len(states) == 2
        service_names = [s["service_name"] for s in states]
        assert "service_1" in service_names
        assert "service_2" in service_names

    # =========================================================================
    # Force Open Tests
    # =========================================================================

    def test_force_open_creates_new_in_open_state(self):
        """
        Purpose:
            Verify force_open creates new circuit in OPEN state.
        """
        user = UserFactory()

        result = self.service.force_open(
            service_name="new_service",
            reason="PG maintenance",
            controlled_by=user,
        )

        assert result.success is True
        assert result.previous_state == CircuitState.CLOSED
        assert result.new_state == CircuitState.OPEN

        state = CircuitBreakerState.objects.get(service_name="new_service")
        assert state.state == "open"
        assert state.manually_controlled is True
        assert state.controlled_by == user
        assert state.control_reason == "PG maintenance"

    def test_force_open_transitions_existing_circuit(self):
        """
        Purpose:
            Verify force_open transitions existing circuit to OPEN.
        """
        user = UserFactory()
        CircuitBreakerState.objects.create(
            service_name="test_service",
            state="closed",
        )

        result = self.service.force_open(
            service_name="test_service",
            reason="Detected outage",
            controlled_by=user,
        )

        assert result.success is True
        assert result.previous_state == "closed"
        assert result.new_state == CircuitState.OPEN

        state = CircuitBreakerState.objects.get(service_name="test_service")
        assert state.state == "open"
        assert state.opened_at is not None

    def test_force_open_already_open_succeeds(self):
        """
        Purpose:
            Verify force_open on already open circuit succeeds.
        """
        CircuitBreakerState.objects.create(
            service_name="test_service",
            state="open",
            opened_at=timezone.now(),
        )

        result = self.service.force_open(
            service_name="test_service",
            reason="Double check",
        )

        assert result.success is True
        assert result.previous_state == CircuitState.OPEN
        assert result.new_state == CircuitState.OPEN
        assert "already open" in result.message

    # =========================================================================
    # Force Close Tests
    # =========================================================================

    def test_force_close_transitions_to_closed(self):
        """
        Purpose:
            Verify force_close transitions circuit to CLOSED.
        """
        user = UserFactory()
        CircuitBreakerState.objects.create(
            service_name="test_service",
            state="open",
            opened_at=timezone.now(),
            failure_count=10,
        )

        result = self.service.force_close(
            service_name="test_service",
            reason="PG recovered",
            controlled_by=user,
            trigger_replay=False,
        )

        assert result.success is True
        assert result.previous_state == "open"
        assert result.new_state == CircuitState.CLOSED

        state = CircuitBreakerState.objects.get(service_name="test_service")
        assert state.state == "closed"
        assert state.failure_count == 0
        assert state.opened_at is None

    def test_force_close_nonexistent_fails(self):
        """
        Purpose:
            Verify force_close on nonexistent circuit fails.
        """
        result = self.service.force_close(
            service_name="nonexistent_service",
            reason="Test",
        )

        assert result.success is False
        assert "does not exist" in result.error

    def test_force_close_already_closed_succeeds(self):
        """
        Purpose:
            Verify force_close on already closed circuit succeeds.
        """
        CircuitBreakerState.objects.create(
            service_name="test_service",
            state="closed",
        )

        result = self.service.force_close(
            service_name="test_service",
            reason="Confirm",
        )

        assert result.success is True
        assert "already closed" in result.message

    @patch("shopping.services.self_healing.circuit_breaker_service.CircuitBreakerService._trigger_conditional_replay")
    def test_force_close_triggers_replay_when_requested(self, mock_replay):
        """
        Purpose:
            Verify force_close triggers conditional replay when requested.
        """
        CircuitBreakerState.objects.create(
            service_name="test_service",
            state="open",
            opened_at=timezone.now(),
        )

        result = self.service.force_close(
            service_name="test_service",
            reason="Recovered",
            trigger_replay=True,
        )

        assert result.success is True
        mock_replay.assert_called_once_with("test_service")

    @patch("shopping.services.self_healing.circuit_breaker_service.CircuitBreakerService._trigger_conditional_replay")
    def test_force_close_does_not_trigger_replay_when_not_requested(self, mock_replay):
        """
        Purpose:
            Verify force_close does not trigger replay when not requested.
        """
        CircuitBreakerState.objects.create(
            service_name="test_service",
            state="open",
            opened_at=timezone.now(),
        )

        result = self.service.force_close(
            service_name="test_service",
            reason="Recovered",
            trigger_replay=False,
        )

        assert result.success is True
        mock_replay.assert_not_called()

    # =========================================================================
    # Failure/Success Recording Tests
    # =========================================================================

    def test_record_failure_increments_count(self):
        """
        Purpose:
            Verify record_failure increments failure count.
        """
        CircuitBreakerState.objects.create(
            service_name="test_service",
            state="closed",
            failure_count=0,
        )

        self.service.record_failure("test_service")

        state = CircuitBreakerState.objects.get(service_name="test_service")
        assert state.failure_count == 1
        assert state.last_failure_at is not None

    def test_record_failure_opens_circuit_at_threshold(self):
        """
        Purpose:
            Verify circuit opens when failure threshold is reached.
        """
        CircuitBreakerState.objects.create(
            service_name="test_service",
            state="closed",
            failure_count=4,  # One below threshold
        )

        self.service.record_failure("test_service")

        state = CircuitBreakerState.objects.get(service_name="test_service")
        assert state.failure_count == 5
        assert state.state == "open"
        assert state.opened_at is not None

    def test_record_failure_skips_manually_controlled(self):
        """
        Purpose:
            Verify record_failure skips manually controlled circuits.
        """
        CircuitBreakerState.objects.create(
            service_name="test_service",
            state="closed",
            failure_count=0,
            manually_controlled=True,
        )

        self.service.record_failure("test_service")

        state = CircuitBreakerState.objects.get(service_name="test_service")
        assert state.failure_count == 0  # Should not change

    def test_record_success_in_half_open_closes_circuit(self):
        """
        Purpose:
            Verify enough successes in half-open closes circuit.
        """
        CircuitBreakerState.objects.create(
            service_name="test_service",
            state="half_open",
            success_count=1,  # One success already
        )

        self.service.record_success("test_service")

        state = CircuitBreakerState.objects.get(service_name="test_service")
        assert state.state == "closed"
        assert state.failure_count == 0

    def test_record_success_in_closed_resets_failure_count(self):
        """
        Purpose:
            Verify success in closed state resets failure count.
        """
        CircuitBreakerState.objects.create(
            service_name="test_service",
            state="closed",
            failure_count=3,
        )

        self.service.record_success("test_service")

        state = CircuitBreakerState.objects.get(service_name="test_service")
        assert state.failure_count == 0

    # =========================================================================
    # Reset Tests
    # =========================================================================

    def test_reset_clears_all_counters(self):
        """
        Purpose:
            Verify reset clears all counters and sets state to closed.
        """
        user = UserFactory()
        CircuitBreakerState.objects.create(
            service_name="test_service",
            state="open",
            failure_count=10,
            success_count=5,
            opened_at=timezone.now(),
            manually_controlled=True,
        )

        result = self.service.reset(
            service_name="test_service",
            reason="Full reset",
            controlled_by=user,
        )

        assert result.success is True

        state = CircuitBreakerState.objects.get(service_name="test_service")
        assert state.state == "closed"
        assert state.failure_count == 0
        assert state.success_count == 0
        assert state.opened_at is None
        assert state.manually_controlled is False

    def test_reset_nonexistent_fails(self):
        """
        Purpose:
            Verify reset on nonexistent circuit fails.
        """
        result = self.service.reset(
            service_name="nonexistent_service",
            reason="Test",
        )

        assert result.success is False
        assert "does not exist" in result.error


# =============================================================================
# Module-level Function Tests
# =============================================================================


@pytest.mark.django_db
class TestModuleLevelFunctions:
    """Tests for module-level convenience functions."""

    def test_get_circuit_breaker_service_returns_singleton(self):
        """
        Purpose:
            Verify get_circuit_breaker_service returns singleton.
        """
        service1 = get_circuit_breaker_service()
        service2 = get_circuit_breaker_service()

        assert service1 is service2

    @patch("shopping.services.self_healing.circuit_breaker_service.get_circuit_breaker_service")
    def test_should_allow_request_delegates_to_service(self, mock_get_service):
        """
        Purpose:
            Verify should_allow_request delegates to service.
        """
        mock_service = MagicMock()
        mock_service.should_allow.return_value = True
        mock_get_service.return_value = mock_service

        result = should_allow_request("test_service")

        assert result is True
        mock_service.should_allow.assert_called_once_with("test_service")

    @patch("shopping.services.self_healing.circuit_breaker_service.get_circuit_breaker_service")
    def test_force_open_circuit_delegates_to_service(self, mock_get_service):
        """
        Purpose:
            Verify force_open_circuit delegates to service.
        """
        mock_service = MagicMock()
        mock_result = CircuitBreakerResult.succeeded(
            service_name="test",
            previous_state="closed",
            new_state="open",
        )
        mock_service.force_open.return_value = mock_result
        mock_get_service.return_value = mock_service

        result = force_open_circuit("test", reason="Test")

        assert result.success is True
        mock_service.force_open.assert_called_once()

    @patch("shopping.services.self_healing.circuit_breaker_service.get_circuit_breaker_service")
    def test_force_close_circuit_delegates_to_service(self, mock_get_service):
        """
        Purpose:
            Verify force_close_circuit delegates to service.
        """
        mock_service = MagicMock()
        mock_result = CircuitBreakerResult.succeeded(
            service_name="test",
            previous_state="open",
            new_state="closed",
        )
        mock_service.force_close.return_value = mock_result
        mock_get_service.return_value = mock_service

        result = force_close_circuit("test", reason="Test", trigger_replay=True)

        assert result.success is True
        mock_service.force_close.assert_called_once()


# =============================================================================
# Manual Override TTL Tests
# =============================================================================


@pytest.mark.django_db
class TestManualOverrideTTL:
    """Tests for Manual Override TTL functionality."""

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

    def test_force_open_sets_ttl_expiration(self):
        """
        Purpose:
            Verify force_open sets the manual override expiration time.
        """
        from datetime import timedelta

        now = timezone.now()
        
        self.service.force_open(
            service_name="test_ttl_service",
            reason="TTL test",
        )
        
        state = CircuitBreakerState.objects.get(service_name="test_ttl_service")
        
        assert state.manually_controlled is True
        assert state.manual_override_expires_at is not None
        # TTL should be approximately 90 minutes from now
        expected_min = now + timedelta(minutes=89)
        expected_max = now + timedelta(minutes=91)
        assert expected_min <= state.manual_override_expires_at <= expected_max

    def test_check_and_expire_manual_overrides_expires_past_ttl(self):
        """
        Purpose:
            Verify overrides past TTL are expired and transitioned to half-open.
        """
        from datetime import timedelta
        
        # Create circuit with expired TTL
        CircuitBreakerState.objects.create(
            service_name="expired_ttl_service",
            state="open",
            manually_controlled=True,
            manual_override_expires_at=timezone.now() - timedelta(minutes=10),
            control_reason="Test override",
        )
        
        expired = self.service.check_and_expire_manual_overrides()
        
        assert "expired_ttl_service" in expired
        
        state = CircuitBreakerState.objects.get(service_name="expired_ttl_service")
        assert state.state == "half_open"
        assert state.manually_controlled is False
        assert state.manual_override_expires_at is None
        assert "[EXPIRED]" in state.control_reason

    def test_check_and_expire_does_not_expire_valid_ttl(self):
        """
        Purpose:
            Verify overrides with valid TTL are not expired.
        """
        from datetime import timedelta
        
        # Create circuit with future TTL
        CircuitBreakerState.objects.create(
            service_name="valid_ttl_service",
            state="open",
            manually_controlled=True,
            manual_override_expires_at=timezone.now() + timedelta(minutes=60),
            control_reason="Test override",
        )
        
        expired = self.service.check_and_expire_manual_overrides()
        
        assert "valid_ttl_service" not in expired
        
        state = CircuitBreakerState.objects.get(service_name="valid_ttl_service")
        assert state.state == "open"
        assert state.manually_controlled is True

    def test_extend_manual_override_extends_ttl(self):
        """
        Purpose:
            Verify extend_manual_override correctly extends the TTL.
        """
        from datetime import timedelta
        
        initial_expiry = timezone.now() + timedelta(minutes=30)
        CircuitBreakerState.objects.create(
            service_name="extend_ttl_service",
            state="open",
            manually_controlled=True,
            manual_override_expires_at=initial_expiry,
            control_reason="Initial reason",
        )
        
        result = self.service.extend_manual_override(
            service_name="extend_ttl_service",
            additional_minutes=60,
            reason="Need more time",
        )
        
        assert result.success is True
        
        state = CircuitBreakerState.objects.get(service_name="extend_ttl_service")
        expected_min = initial_expiry + timedelta(minutes=59)
        expected_max = initial_expiry + timedelta(minutes=61)
        assert expected_min <= state.manual_override_expires_at <= expected_max
        assert "Extended:" in state.control_reason

    def test_extend_manual_override_fails_for_non_manual(self):
        """
        Purpose:
            Verify extend fails for circuits not under manual control.
        """
        CircuitBreakerState.objects.create(
            service_name="auto_service",
            state="open",
            manually_controlled=False,
        )
        
        result = self.service.extend_manual_override(
            service_name="auto_service",
            additional_minutes=60,
        )
        
        assert result.success is False
        assert "not under manual control" in result.error

    def test_reset_clears_ttl(self):
        """
        Purpose:
            Verify reset clears the manual override TTL.
        """
        from datetime import timedelta
        
        CircuitBreakerState.objects.create(
            service_name="reset_ttl_service",
            state="open",
            manually_controlled=True,
            manual_override_expires_at=timezone.now() + timedelta(minutes=60),
        )
        
        self.service.reset(service_name="reset_ttl_service", reason="Test reset")
        
        state = CircuitBreakerState.objects.get(service_name="reset_ttl_service")
        assert state.state == "closed"
        assert state.manually_controlled is False
        assert state.manual_override_expires_at is None

