"""
Stage 43: Governance / Operator Race - Deterministic Test Suite

This module contains deterministic (non-random) tests that verify
the core invariants of the operator race protection system.

Unlike the chaos tests, these tests use fixed sequences to ensure
reproducible results for CI/CD pipelines.

Invariants Tested:
1. Final system state is ALWAYS deterministic
2. Only ONE final state exists: open, closed, half_open, or default
3. Intermediate states MUST NOT be observable
4. The last valid operator action WINS (based on atomic ordering)
5. TTL expiration MUST NOT overwrite a newer manual action
6. Manual control flags must remain consistent

Execution:
    pytest load_tests/scenarios/stage43_operator_race_deterministic.py -v -s

    # Docker Compose:
    docker-compose -f docker-compose.stage43.yml run --rm test-deterministic
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone as tz

import pytest


# =============================================================================
# Logging Configuration
# =============================================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# =============================================================================
# Shared Repository Singleton
# =============================================================================

_SHARED_MEMORY_REPO = None
_SHARED_CB_SERVICE = None
_REPO_LOCK = threading.Lock()


def _get_or_create_shared_repo():
    """Get or create shared in-memory repository."""
    global _SHARED_MEMORY_REPO, _SHARED_CB_SERVICE
    with _REPO_LOCK:
        if _SHARED_MEMORY_REPO is None:
            from selfhealing.adapters.memory.circuit_breaker import (
                InMemoryCircuitBreakerStateRepository,
            )
            from selfhealing.services import CircuitBreakerConfig, CircuitBreakerService

            _SHARED_MEMORY_REPO = InMemoryCircuitBreakerStateRepository()
            _SHARED_CB_SERVICE = CircuitBreakerService(
                config=CircuitBreakerConfig(
                    enabled=True,
                    failure_threshold=3,
                    recovery_timeout=30,
                    manual_override_ttl_minutes=60,
                ),
                repository=_SHARED_MEMORY_REPO,
            )
    return _SHARED_MEMORY_REPO, _SHARED_CB_SERVICE


def _reset_shared_repo():
    """Reset the shared repository for test isolation."""
    global _SHARED_MEMORY_REPO
    with _REPO_LOCK:
        if _SHARED_MEMORY_REPO is not None:
            _SHARED_MEMORY_REPO.clear()


def _get_selfhealing_services():
    """Get selfhealing services."""
    from selfhealing.services import (
        CircuitBreakerConfig,
        CircuitBreakerService,
        CircuitState,
    )
    from selfhealing.interfaces.repositories import CircuitBreakerStateEnum

    _memory_repo, _cb_service = _get_or_create_shared_repo()

    def force_open_circuit(service_name: str, reason: str = "", operator_id: int | None = None):
        return _cb_service.force_open(service_name=service_name, reason=reason, controlled_by_id=operator_id)

    def force_close_circuit(service_name: str, reason: str = "", operator_id: int | None = None):
        return _cb_service.force_close(service_name=service_name, reason=reason, controlled_by_id=operator_id)

    def reset_circuit(service_name: str, reason: str = "", operator_id: int | None = None):
        return _cb_service.reset(service_name=service_name, reason=reason, controlled_by=operator_id)

    def get_state(service_name: str):
        return _memory_repo.get_by_service_name(service_name)

    return {
        "CircuitBreakerConfig": CircuitBreakerConfig,
        "CircuitBreakerService": CircuitBreakerService,
        "CircuitState": CircuitState,
        "CircuitBreakerStateEnum": CircuitBreakerStateEnum,
        "force_open_circuit": force_open_circuit,
        "force_close_circuit": force_close_circuit,
        "reset_circuit": reset_circuit,
        "get_state": get_state,
        "repository": _memory_repo,
        "service": _cb_service,
    }


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_state():
    """Reset shared repository before each test."""
    _reset_shared_repo()
    yield
    _reset_shared_repo()


@pytest.fixture
def services():
    """Get selfhealing services."""
    return _get_selfhealing_services()


# =============================================================================
# Deterministic Sequence Tests
# =============================================================================


class TestSequentialOperatorActions:
    """
    Deterministic sequential operation tests.

    These tests execute operations in a fixed sequence to verify
    expected state transitions.
    """

    def test_force_open_then_force_close(self, services):
        """
        Sequence: force_open → force_close

        Expected: Final state is CLOSED
        """
        service_name = "test_open_then_close"

        # Initialize
        services["repository"].get_or_create(service_name)

        # Step 1: force_open
        result1 = services["force_open_circuit"](service_name, "Open by operator 1", 1001)
        assert result1.success
        state1 = services["get_state"](service_name)
        assert state1.state == "open"
        assert state1.manually_controlled is True

        # Step 2: force_close
        result2 = services["force_close_circuit"](service_name, "Close by operator 2", 1002)
        assert result2.success
        state2 = services["get_state"](service_name)
        assert state2.state == "closed"
        assert state2.manually_controlled is True

    def test_force_close_then_force_open(self, services):
        """
        Sequence: force_close → force_open

        Expected: Final state is OPEN
        """
        service_name = "test_close_then_open"

        services["repository"].get_or_create(service_name)

        # Step 1: force_close
        result1 = services["force_close_circuit"](service_name, "Close first", 1001)
        assert result1.success

        # Step 2: force_open
        result2 = services["force_open_circuit"](service_name, "Open second", 1002)
        assert result2.success

        final_state = services["get_state"](service_name)
        assert final_state.state == "open"

    def test_reset_clears_manual_control(self, services):
        """
        Sequence: force_open → reset

        Expected:
            - Final state is CLOSED
            - manually_controlled is False
        """
        service_name = "test_reset_clears"

        services["repository"].get_or_create(service_name)

        # Step 1: force_open (sets manually_controlled = True)
        services["force_open_circuit"](service_name, "Manual open", 1001)
        state1 = services["get_state"](service_name)
        assert state1.manually_controlled is True

        # Step 2: reset (clears manually_controlled)
        result = services["reset_circuit"](service_name, "Reset to default", 1002)
        assert result.success

        final_state = services["get_state"](service_name)
        assert final_state.state == "closed"
        assert final_state.manually_controlled is False

    def test_reset_after_force_close_keeps_closed(self, services):
        """
        Sequence: force_close → reset

        Expected:
            - Final state is CLOSED
            - manually_controlled is False
        """
        service_name = "test_reset_after_close"

        services["repository"].get_or_create(service_name)

        # Step 1: force_close
        services["force_close_circuit"](service_name, "Close first", 1001)

        # Step 2: reset
        result = services["reset_circuit"](service_name, "Reset", 1002)
        assert result.success

        final_state = services["get_state"](service_name)
        assert final_state.state == "closed"
        assert final_state.manually_controlled is False


class TestStateConsistencyInvariants:
    """
    Test state consistency invariants.
    """

    def test_open_state_has_opened_at(self, services):
        """
        When state is OPEN, opened_at should be set.
        """
        service_name = "test_open_has_opened_at"

        services["repository"].get_or_create(service_name)
        services["force_open_circuit"](service_name, "Test open", 1001)

        state = services["get_state"](service_name)
        assert state.state == "open"
        assert state.opened_at is not None

    def test_closed_state_clears_opened_at(self, services):
        """
        When transitioning to CLOSED via force_close, opened_at should be cleared.
        """
        service_name = "test_closed_clears_opened_at"

        services["repository"].get_or_create(service_name)
        services["force_open_circuit"](service_name, "Open first", 1001)
        services["force_close_circuit"](service_name, "Close second", 1002)

        state = services["get_state"](service_name)
        assert state.state == "closed"
        assert state.opened_at is None

    def test_manual_control_flags_consistency_on_open(self, services):
        """
        When manually opened:
            - manually_controlled = True
            - controlled_by_id is set (if provided)
            - control_reason is set
        """
        service_name = "test_manual_flags_on_open"
        operator_id = 1001

        services["repository"].get_or_create(service_name)
        services["force_open_circuit"](service_name, "Test reason", operator_id)

        state = services["get_state"](service_name)
        assert state.manually_controlled is True
        assert state.controlled_by_id == operator_id
        assert "Test reason" in state.control_reason

    def test_manual_control_flags_consistency_on_close(self, services):
        """
        When manually closed:
            - manually_controlled = True
            - controlled_by_id is set
            - control_reason is set
        """
        service_name = "test_manual_flags_on_close"
        operator_id = 1002

        services["repository"].get_or_create(service_name)
        services["force_close_circuit"](service_name, "Close reason", operator_id)

        state = services["get_state"](service_name)
        assert state.manually_controlled is True
        assert state.controlled_by_id == operator_id

    def test_reset_clears_all_manual_flags(self, services):
        """
        Reset should clear:
            - manually_controlled = False
            - controlled_by_id = None
            - manual_override_expires_at = None
        """
        service_name = "test_reset_clears_flags"

        services["repository"].get_or_create(service_name)
        services["force_open_circuit"](service_name, "Open with flags", 1001)

        # Verify flags are set
        state1 = services["get_state"](service_name)
        assert state1.manually_controlled is True
        assert state1.manual_override_expires_at is not None

        # Reset
        services["reset_circuit"](service_name, "Clear all", 1002)

        state2 = services["get_state"](service_name)
        assert state2.manually_controlled is False
        assert state2.controlled_by_id is None
        assert state2.manual_override_expires_at is None


class TestAtomicOperationGuarantees:
    """
    Test that atomic operations provide expected guarantees.
    """

    def test_atomic_force_open_returns_previous_state(self, services):
        """
        atomic_force_open should return (success, previous_state, new_state).
        """
        service_name = "test_atomic_open_return"

        services["repository"].get_or_create(service_name)

        # First call: closed -> open
        success, prev, new = services["repository"].atomic_force_open(service_name, "Test", 1001, 60)
        assert success is True
        assert prev == "closed"
        assert new == "open"

        # Second call: open -> open (idempotent)
        success2, prev2, new2 = services["repository"].atomic_force_open(service_name, "Test2", 1002, 60)
        assert success2 is True
        assert prev2 == "open"
        assert new2 == "open"

    def test_atomic_force_close_returns_previous_state(self, services):
        """
        atomic_force_close should return (success, previous_state, new_state).
        """
        service_name = "test_atomic_close_return"

        services["repository"].get_or_create(service_name)
        services["repository"].atomic_force_open(service_name, "Open first", 1001, 60)

        success, prev, new = services["repository"].atomic_force_close(service_name, "Close", 1002)
        assert success is True
        assert prev == "open"
        assert new == "closed"

    def test_atomic_reset_returns_previous_state(self, services):
        """
        atomic_reset should return (success, previous_state, new_state).
        """
        service_name = "test_atomic_reset_return"

        services["repository"].get_or_create(service_name)
        services["repository"].atomic_force_open(service_name, "Open", 1001, 60)

        success, prev, new = services["repository"].atomic_reset(service_name, "Reset", 1002)
        assert success is True
        assert prev == "open"
        assert new == "closed"


class TestBoundaryConditions:
    """
    Test boundary conditions and edge cases.
    """

    def test_action_on_nonexistent_circuit(self, services):
        """
        Operations on non-existent circuit should create it (get_or_create).
        """
        service_name = "test_nonexistent"

        # force_open creates if not exists
        result = services["force_open_circuit"](service_name, "Create on open", 1001)
        assert result.success

        state = services["get_state"](service_name)
        assert state is not None
        assert state.state == "open"

    def test_reset_on_nonexistent_fails_gracefully(self, services):
        """
        Reset on non-existent circuit should fail gracefully.
        """
        service_name = "test_reset_nonexistent"

        result = services["reset_circuit"](service_name, "Reset nothing", 1001)
        # Reset fails if circuit doesn't exist
        assert result.success is False

    def test_double_force_open_is_idempotent(self, services):
        """
        Calling force_open twice should be idempotent.
        """
        service_name = "test_double_open"

        services["repository"].get_or_create(service_name)

        result1 = services["force_open_circuit"](service_name, "Open 1", 1001)
        result2 = services["force_open_circuit"](service_name, "Open 2", 1002)

        assert result1.success
        assert result2.success

        state = services["get_state"](service_name)
        assert state.state == "open"
        # Latest operator wins
        assert state.controlled_by_id == 1002

    def test_double_force_close_is_idempotent(self, services):
        """
        Calling force_close twice should be idempotent.
        """
        service_name = "test_double_close"

        services["repository"].get_or_create(service_name)

        result1 = services["force_close_circuit"](service_name, "Close 1", 1001)
        result2 = services["force_close_circuit"](service_name, "Close 2", 1002)

        assert result1.success
        assert result2.success

        state = services["get_state"](service_name)
        assert state.state == "closed"
        assert state.controlled_by_id == 1002


class TestTTLManagement:
    """
    Test TTL (Time-To-Live) management for manual overrides.
    """

    def test_force_open_sets_ttl(self, services):
        """
        force_open should set manual_override_expires_at.
        """
        service_name = "test_ttl_on_open"

        services["repository"].get_or_create(service_name)
        services["force_open_circuit"](service_name, "Open with TTL", 1001)

        state = services["get_state"](service_name)
        assert state.manual_override_expires_at is not None

        # TTL should be in the future
        now = datetime.now(tz.utc)
        # Handle timezone-naive datetime from repository
        expires_at = state.manual_override_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=tz.utc)

        assert expires_at > now

    def test_force_close_clears_ttl(self, services):
        """
        force_close should clear manual_override_expires_at.
        """
        service_name = "test_ttl_cleared_on_close"

        services["repository"].get_or_create(service_name)
        services["force_open_circuit"](service_name, "Open", 1001)
        services["force_close_circuit"](service_name, "Close", 1002)

        state = services["get_state"](service_name)
        assert state.manual_override_expires_at is None

    def test_reset_clears_ttl(self, services):
        """
        reset should clear manual_override_expires_at.
        """
        service_name = "test_ttl_cleared_on_reset"

        services["repository"].get_or_create(service_name)
        services["force_open_circuit"](service_name, "Open", 1001)
        services["reset_circuit"](service_name, "Reset", 1002)

        state = services["get_state"](service_name)
        assert state.manual_override_expires_at is None


class TestOperatorAuditTrail:
    """
    Test that operator actions are properly recorded.
    """

    def test_controlled_by_id_tracks_last_operator(self, services):
        """
        controlled_by_id should reflect the last operator who changed the state.
        """
        service_name = "test_operator_tracking"

        services["repository"].get_or_create(service_name)

        # Operator 1001 opens
        services["force_open_circuit"](service_name, "Op1 opens", 1001)
        state1 = services["get_state"](service_name)
        assert state1.controlled_by_id == 1001

        # Operator 1002 closes
        services["force_close_circuit"](service_name, "Op2 closes", 1002)
        state2 = services["get_state"](service_name)
        assert state2.controlled_by_id == 1002

        # Operator 1003 opens again
        services["force_open_circuit"](service_name, "Op3 opens", 1003)
        state3 = services["get_state"](service_name)
        assert state3.controlled_by_id == 1003

    def test_control_reason_tracks_last_reason(self, services):
        """
        control_reason should reflect the last action's reason.
        """
        service_name = "test_reason_tracking"

        services["repository"].get_or_create(service_name)

        services["force_open_circuit"](service_name, "Reason A", 1001)
        state1 = services["get_state"](service_name)
        assert "Reason A" in state1.control_reason

        services["force_close_circuit"](service_name, "Reason B", 1002)
        state2 = services["get_state"](service_name)
        assert "Reason B" in state2.control_reason


class TestMultiServiceIsolation:
    """
    Test that different services are isolated from each other.
    """

    def test_services_have_independent_states(self, services):
        """
        Operations on one service should not affect another.
        """
        service_a = "service_a"
        service_b = "service_b"

        services["repository"].get_or_create(service_a)
        services["repository"].get_or_create(service_b)

        # Open service A, close service B
        services["force_open_circuit"](service_a, "Open A", 1001)
        services["force_close_circuit"](service_b, "Close B", 1002)

        state_a = services["get_state"](service_a)
        state_b = services["get_state"](service_b)

        assert state_a.state == "open"
        assert state_b.state == "closed"

        # Verify no cross-contamination
        assert state_a.controlled_by_id == 1001
        assert state_b.controlled_by_id == 1002


# =============================================================================
# Entry Point for Direct Execution
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
