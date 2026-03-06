"""
CircuitBreakerBase shared state machine tests (307).

Tests the extracted abstract base class that provides lock-free
state transition logic reused by both CircuitBreaker and
HashChainCircuitBreaker.
"""

import threading
import time
from datetime import timezone
from unittest.mock import MagicMock, patch

from selfhealing.audit.graceful_degradation.enums import CircuitState
from selfhealing.audit.resilience.circuit_breaker import (
    AuditCircuitBreakerConfig,
    CircuitBreaker,
    CircuitBreakerBase,
    CircuitBreakerRegistry,
    CircuitBreakerSnapshot,
    get_circuit_breaker,
)


class ConcreteCircuitBreaker(CircuitBreakerBase):
    """Minimal concrete implementation for testing base logic."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.state_change_log: list[tuple[CircuitState, CircuitState]] = []

    def can_execute(self) -> bool:
        return self._can_execute_impl()

    def record_success(self) -> None:
        self._record_success_impl()

    def record_failure(self, error=None) -> None:
        self._record_failure_impl()

    def _on_state_changed(self, old, new):
        self.state_change_log.append((old, new))


# ============================================================================
# Contract Tests
# ============================================================================


class TestAuditCircuitBreakerConfigContract:
    """AuditCircuitBreakerConfig default contract values."""

    def test_failure_threshold_default(self):
        """Default failure_threshold is 3."""
        cfg = AuditCircuitBreakerConfig()
        assert cfg.failure_threshold == 3

    def test_success_threshold_default(self):
        """Default success_threshold is 2."""
        cfg = AuditCircuitBreakerConfig()
        assert cfg.success_threshold == 2

    def test_timeout_seconds_default(self):
        """Default timeout_seconds is 30.0."""
        cfg = AuditCircuitBreakerConfig()
        assert cfg.timeout_seconds == 30.0

    def test_call_timeout_seconds_default(self):
        """Default call_timeout_seconds is 5.0."""
        cfg = AuditCircuitBreakerConfig()
        assert cfg.call_timeout_seconds == 5.0


class TestCircuitBreakerSnapshotContract:
    """CircuitBreakerSnapshot default field contracts."""

    def test_default_state_is_closed(self):
        """Default snapshot state is CLOSED."""
        snap = CircuitBreakerSnapshot()
        assert snap.state == CircuitState.CLOSED

    def test_default_counters_are_zero(self):
        """Default counters are all 0."""
        snap = CircuitBreakerSnapshot()
        assert snap.failure_count == 0
        assert snap.success_count == 0
        assert snap.total_failures == 0
        assert snap.total_successes == 0

    def test_default_last_failure_time_is_none(self):
        """Default last_failure_time is None."""
        snap = CircuitBreakerSnapshot()
        assert snap.last_failure_time is None


class TestCircuitBreakerBaseStatsContract:
    """get_stats() returns the documented key set."""

    def test_stats_keys(self):
        """get_stats() returns required key set."""
        cb = ConcreteCircuitBreaker(
            name="test",
            failure_threshold=5,
            success_threshold=2,
            timeout_seconds=30.0,
        )
        stats = cb.get_stats()
        expected_keys = {
            "name",
            "state",
            "failure_count",
            "success_count",
            "total_requests",
            "total_failures",
            "total_successes",
            "state_changes",
            "last_failure_time",
        }
        assert expected_keys == set(stats.keys())

    def test_stats_initial_values(self):
        """Initial stats contain zeros and closed state."""
        cb = ConcreteCircuitBreaker(
            name="my-cb",
            failure_threshold=5,
            success_threshold=2,
            timeout_seconds=30.0,
        )
        stats = cb.get_stats()
        assert stats["name"] == "my-cb"
        assert stats["state"] == "closed"
        assert stats["failure_count"] == 0
        assert stats["total_requests"] == 0
        assert stats["last_failure_time"] is None


# ============================================================================
# Behavior Tests
# ============================================================================


class TestCircuitBreakerBaseStateTransitionBehavior:
    """State transition logic of CircuitBreakerBase."""

    def _make_cb(self, failure_threshold=3, success_threshold=2, timeout_seconds=30.0):
        return ConcreteCircuitBreaker(
            name="test",
            failure_threshold=failure_threshold,
            success_threshold=success_threshold,
            timeout_seconds=timeout_seconds,
        )

    def test_initial_state_is_closed(self):
        """New circuit breaker starts in CLOSED state."""
        cb = self._make_cb()
        assert cb._state == CircuitState.CLOSED

    def test_can_execute_returns_true_when_closed(self):
        """CLOSED state allows execution."""
        cb = self._make_cb()
        assert cb.can_execute() is True

    def test_closed_to_open_on_failure_threshold(self):
        """CLOSED -> OPEN after failure_threshold failures."""
        cb = self._make_cb(failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb._state == CircuitState.OPEN

    def test_stays_closed_below_threshold(self):
        """CLOSED state maintained with fewer failures than threshold."""
        cb = self._make_cb(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb._state == CircuitState.CLOSED

    def test_success_resets_failure_count_in_closed(self):
        """Success in CLOSED resets failure_count to 0."""
        cb = self._make_cb(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb._failure_count == 0

    def test_open_rejects_execution(self):
        """OPEN state rejects execution."""
        cb = self._make_cb(failure_threshold=1)
        cb.record_failure()
        assert cb._state == CircuitState.OPEN
        assert cb.can_execute() is False

    def test_open_to_half_open_after_timeout(self):
        """OPEN -> HALF_OPEN after timeout_seconds elapsed."""
        cb = self._make_cb(failure_threshold=1, timeout_seconds=0.05)
        cb.record_failure()
        assert cb._state == CircuitState.OPEN

        time.sleep(0.06)
        cb.can_execute()
        assert cb._state == CircuitState.HALF_OPEN

    def test_half_open_allows_execution_by_default(self):
        """HALF_OPEN allows execution (default _can_attempt_half_open returns True)."""
        cb = self._make_cb(failure_threshold=1, timeout_seconds=0.01)
        cb.record_failure()
        time.sleep(0.02)
        assert cb.can_execute() is True
        assert cb._state == CircuitState.HALF_OPEN

    def test_half_open_to_closed_on_success_threshold(self):
        """HALF_OPEN -> CLOSED after success_threshold successes."""
        cb = self._make_cb(
            failure_threshold=1, success_threshold=2, timeout_seconds=0.01
        )
        cb.record_failure()
        time.sleep(0.02)
        cb.can_execute()

        cb.record_success()
        assert cb._state == CircuitState.HALF_OPEN
        cb.record_success()
        assert cb._state == CircuitState.CLOSED

    def test_half_open_to_open_on_failure(self):
        """HALF_OPEN -> OPEN on any failure."""
        cb = self._make_cb(failure_threshold=1, timeout_seconds=0.01)
        cb.record_failure()
        time.sleep(0.02)
        cb.can_execute()
        assert cb._state == CircuitState.HALF_OPEN

        cb.record_failure()
        assert cb._state == CircuitState.OPEN

    def test_transition_to_closed_resets_counters(self):
        """Transition to CLOSED resets failure_count and success_count."""
        cb = self._make_cb(
            failure_threshold=1, success_threshold=1, timeout_seconds=0.01
        )
        cb.record_failure()
        time.sleep(0.02)
        cb.can_execute()
        cb.record_success()

        assert cb._state == CircuitState.CLOSED
        assert cb._failure_count == 0
        assert cb._success_count == 0

    def test_transition_to_half_open_resets_success_count(self):
        """Transition to HALF_OPEN resets success_count."""
        cb = self._make_cb(failure_threshold=1, timeout_seconds=0.01)
        cb.record_failure()
        time.sleep(0.02)
        cb.can_execute()

        assert cb._state == CircuitState.HALF_OPEN
        assert cb._success_count == 0


class TestCircuitBreakerBaseCountersBehavior:
    """Counters (total_requests, total_failures, total_successes) tracking."""

    def test_can_execute_increments_total_requests(self):
        """Each can_execute() call increments total_requests."""
        cb = ConcreteCircuitBreaker(
            name="test",
            failure_threshold=5,
            success_threshold=2,
            timeout_seconds=30.0,
        )
        cb.can_execute()
        cb.can_execute()
        cb.can_execute()
        assert cb._total_requests == 3

    def test_record_failure_increments_total_failures(self):
        """Each record_failure() increments total_failures."""
        cb = ConcreteCircuitBreaker(
            name="test",
            failure_threshold=5,
            success_threshold=2,
            timeout_seconds=30.0,
        )
        cb.record_failure()
        cb.record_failure()
        assert cb._total_failures == 2

    def test_record_success_increments_total_successes(self):
        """Each record_success() increments total_successes."""
        cb = ConcreteCircuitBreaker(
            name="test",
            failure_threshold=5,
            success_threshold=2,
            timeout_seconds=30.0,
        )
        cb.record_success()
        cb.record_success()
        assert cb._total_successes == 2

    def test_state_changes_counter_tracks_transitions(self):
        """state_changes increments on each state transition."""
        cb = ConcreteCircuitBreaker(
            name="test",
            failure_threshold=1,
            success_threshold=1,
            timeout_seconds=0.01,
        )
        cb.record_failure()  # CLOSED -> OPEN (+1)
        time.sleep(0.02)
        cb.can_execute()  # OPEN -> HALF_OPEN (+1)
        cb.record_success()  # HALF_OPEN -> CLOSED (+1)
        assert cb._state_changes == 3


class TestCircuitBreakerBaseObservabilityHookBehavior:
    """DR-6: _on_state_changed hook is called on every state transition."""

    def test_hook_called_on_closed_to_open(self):
        """_on_state_changed fires on CLOSED -> OPEN."""
        cb = ConcreteCircuitBreaker(
            name="test",
            failure_threshold=1,
            success_threshold=2,
            timeout_seconds=30.0,
        )
        cb.record_failure()
        assert len(cb.state_change_log) == 1
        assert cb.state_change_log[0] == (CircuitState.CLOSED, CircuitState.OPEN)

    def test_hook_called_on_open_to_half_open(self):
        """_on_state_changed fires on OPEN -> HALF_OPEN."""
        cb = ConcreteCircuitBreaker(
            name="test",
            failure_threshold=1,
            success_threshold=2,
            timeout_seconds=0.01,
        )
        cb.record_failure()
        time.sleep(0.02)
        cb.can_execute()

        assert cb.state_change_log[-1] == (CircuitState.OPEN, CircuitState.HALF_OPEN)

    def test_hook_called_on_half_open_to_closed(self):
        """_on_state_changed fires on HALF_OPEN -> CLOSED."""
        cb = ConcreteCircuitBreaker(
            name="test",
            failure_threshold=1,
            success_threshold=1,
            timeout_seconds=0.01,
        )
        cb.record_failure()
        time.sleep(0.02)
        cb.can_execute()
        cb.record_success()

        assert cb.state_change_log[-1] == (CircuitState.HALF_OPEN, CircuitState.CLOSED)


class TestCircuitBreakerBaseTimeBehavior:
    """DR-1: Monotonic time usage for timeout calculation."""

    def test_last_failure_mono_updated_on_failure(self):
        """record_failure sets _last_failure_mono via time.monotonic()."""
        cb = ConcreteCircuitBreaker(
            name="test",
            failure_threshold=5,
            success_threshold=2,
            timeout_seconds=30.0,
        )
        before = time.monotonic()
        cb.record_failure()
        after = time.monotonic()
        assert before <= cb._last_failure_mono <= after

    def test_last_failure_time_set_as_utc_datetime(self):
        """record_failure sets _last_failure_time as UTC datetime."""
        cb = ConcreteCircuitBreaker(
            name="test",
            failure_threshold=5,
            success_threshold=2,
            timeout_seconds=30.0,
        )
        cb.record_failure()
        assert cb._last_failure_time is not None
        assert cb._last_failure_time.tzinfo == timezone.utc

    def test_get_elapsed_seconds_returns_monotonic_diff(self):
        """_get_elapsed_seconds uses monotonic time difference."""
        cb = ConcreteCircuitBreaker(
            name="test",
            failure_threshold=5,
            success_threshold=2,
            timeout_seconds=30.0,
        )
        cb._last_failure_mono = time.monotonic() - 10.0
        elapsed = cb._get_elapsed_seconds()
        assert 9.9 < elapsed < 10.5

    def test_check_timeout_no_op_when_closed(self):
        """_check_timeout_impl is no-op when state is CLOSED."""
        cb = ConcreteCircuitBreaker(
            name="test",
            failure_threshold=5,
            success_threshold=2,
            timeout_seconds=0.01,
        )
        cb._last_failure_mono = time.monotonic() - 100
        cb._check_timeout_impl()
        assert cb._state == CircuitState.CLOSED

    def test_check_timeout_no_op_when_no_failure_recorded(self):
        """_check_timeout_impl is no-op when _last_failure_mono is 0.0."""
        cb = ConcreteCircuitBreaker(
            name="test",
            failure_threshold=1,
            success_threshold=2,
            timeout_seconds=0.01,
        )
        # Manually set to OPEN without recording failure
        cb._state = CircuitState.OPEN
        cb._check_timeout_impl()
        # Should stay OPEN because _last_failure_mono == 0.0
        assert cb._state == CircuitState.OPEN


class TestCircuitBreakerSyncBehavior:
    """CircuitBreaker (sync) wraps base logic with threading.RLock."""

    def setup_method(self):
        CircuitBreakerRegistry._instance = None

    def teardown_method(self):
        CircuitBreakerRegistry._instance = None

    def test_state_property_triggers_timeout_check(self):
        """Accessing .state triggers _check_timeout_impl."""
        config = AuditCircuitBreakerConfig(failure_threshold=1, timeout_seconds=0.01)
        cb = CircuitBreaker("test", config)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        time.sleep(0.02)
        assert cb.state == CircuitState.HALF_OPEN

    @patch("selfhealing.audit.resilience.metrics.AuditMetrics", autospec=True)
    def test_on_state_changed_calls_audit_metrics(self, mock_metrics_cls):
        """DR-6: State transition calls AuditMetrics.set_circuit_state."""
        mock_instance = MagicMock()
        mock_metrics_cls.get_instance.return_value = mock_instance

        config = AuditCircuitBreakerConfig(failure_threshold=1)
        cb = CircuitBreaker("test-backend", config)
        cb.record_failure()

        mock_instance.set_circuit_state.assert_called_with(
            "test-backend",
            "open",
        )

    def test_get_stats_includes_config_and_last_state_change(self):
        """get_stats() includes 'config' and 'last_state_change' keys."""
        config = AuditCircuitBreakerConfig(failure_threshold=5)
        cb = CircuitBreaker("test", config)
        stats = cb.get_stats()

        assert "config" in stats
        assert "last_state_change" in stats
        assert stats["config"]["failure_threshold"] == config.failure_threshold
        assert stats["config"]["success_threshold"] == config.success_threshold
        assert stats["config"]["timeout_seconds"] == config.timeout_seconds
        assert stats["config"]["call_timeout_seconds"] == config.call_timeout_seconds

    def test_reset_transitions_to_closed(self):
        """reset() transitions circuit to CLOSED."""
        config = AuditCircuitBreakerConfig(failure_threshold=1)
        cb = CircuitBreaker("test", config)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        cb.reset()
        assert cb.state == CircuitState.CLOSED

    def test_force_open_transitions_to_open(self):
        """force_open() transitions circuit to OPEN."""
        cb = CircuitBreaker("test")
        cb.force_open()
        assert cb.state == CircuitState.OPEN

    def test_name_property(self):
        """name property returns constructor name."""
        cb = CircuitBreaker("my-backend")
        assert cb.name == "my-backend"


class TestCircuitBreakerThreadSafetyBehavior:
    """Thread safety of CircuitBreaker with concurrent access."""

    def test_concurrent_record_operations_no_data_corruption(self):
        """10 threads doing record_success/failure preserve counter consistency."""
        config = AuditCircuitBreakerConfig(failure_threshold=10000)
        cb = CircuitBreaker("test", config)
        errors = []

        def worker():
            try:
                for _ in range(100):
                    cb.can_execute()
                    cb.record_success()
                    cb.record_failure()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert cb._total_successes == 1000
        assert cb._total_failures == 1000


class TestCircuitBreakerRegistrySingletonBehavior:
    """CircuitBreakerRegistry singleton and lifecycle."""

    def setup_method(self):
        CircuitBreakerRegistry._instance = None

    def teardown_method(self):
        CircuitBreakerRegistry._instance = None

    def test_get_instance_returns_same_instance(self):
        """get_instance() returns the same singleton."""
        r1 = CircuitBreakerRegistry.get_instance()
        r2 = CircuitBreakerRegistry.get_instance()
        assert r1 is r2

    def test_get_or_create_returns_same_breaker_for_same_name(self):
        """get_or_create returns cached breaker for same name."""
        registry = CircuitBreakerRegistry.get_instance()
        cb1 = registry.get_or_create("backend-a")
        cb2 = registry.get_or_create("backend-a")
        assert cb1 is cb2

    def test_get_returns_none_for_unknown(self):
        """get() returns None for unregistered name."""
        registry = CircuitBreakerRegistry.get_instance()
        assert registry.get("nonexistent") is None

    def test_get_open_circuits_lists_only_open(self):
        """get_open_circuits returns names of OPEN breakers only."""
        registry = CircuitBreakerRegistry.get_instance()
        cb1 = registry.get_or_create(
            "b1", AuditCircuitBreakerConfig(failure_threshold=1)
        )
        registry.get_or_create("b2")

        cb1.record_failure()

        open_list = registry.get_open_circuits()
        assert "b1" in open_list
        assert "b2" not in open_list

    def test_reset_all_closes_all_breakers(self):
        """reset_all() returns all breakers to CLOSED."""
        registry = CircuitBreakerRegistry.get_instance()
        cb1 = registry.get_or_create(
            "b1", AuditCircuitBreakerConfig(failure_threshold=1)
        )
        cb2 = registry.get_or_create(
            "b2", AuditCircuitBreakerConfig(failure_threshold=1)
        )
        cb1.record_failure()
        cb2.record_failure()

        registry.reset_all()
        assert cb1.state == CircuitState.CLOSED
        assert cb2.state == CircuitState.CLOSED


class TestGetCircuitBreakerBehavior:
    """get_circuit_breaker() convenience function."""

    def setup_method(self):
        CircuitBreakerRegistry._instance = None

    def teardown_method(self):
        CircuitBreakerRegistry._instance = None

    def test_returns_circuit_breaker_instance(self):
        """get_circuit_breaker returns a CircuitBreaker."""
        cb = get_circuit_breaker("test-backend")
        assert isinstance(cb, CircuitBreaker)
        assert cb.name == "test-backend"

    def test_returns_same_instance_for_same_name(self):
        """Repeated calls with same name return same instance."""
        cb1 = get_circuit_breaker("same")
        cb2 = get_circuit_breaker("same")
        assert cb1 is cb2
