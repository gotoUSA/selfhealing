"""
InMemoryCircuitBreakerStateRepository 테스트.
"""

import threading
from datetime import datetime, timedelta, timezone

import pytest


class TestInMemoryCircuitBreakerStateRepository:
    """Tests for InMemoryCircuitBreakerStateRepository."""

    @pytest.fixture
    def repo(self):
        """Create a fresh repository for each test."""
        from selfhealing.adapters.memory import InMemoryCircuitBreakerStateRepository

        return InMemoryCircuitBreakerStateRepository()

    def test_get_or_create_new(self, repo):
        """Test creating a new circuit breaker state."""
        from selfhealing.interfaces.repositories import CircuitBreakerStateEnum

        state = repo.get_or_create("toss_payment")

        assert state.id == 1
        assert state.service_name == "toss_payment"
        assert state.state == CircuitBreakerStateEnum.CLOSED.value
        assert state.failure_count == 0
        assert state.success_count == 0
        assert state.created_at is not None

    def test_get_or_create_existing(self, repo):
        """Test retrieving an existing circuit breaker state."""
        first = repo.get_or_create("toss_payment")
        second = repo.get_or_create("toss_payment")

        assert first.id == second.id
        assert first.service_name == second.service_name

    def test_get_by_service_name(self, repo):
        """Test getting state by service name."""
        repo.get_or_create("test_service")

        result = repo.get_by_service_name("test_service")
        assert result is not None
        assert result.service_name == "test_service"

        result = repo.get_by_service_name("non_existent")
        assert result is None

    def test_update_state(self, repo):
        """Test updating circuit breaker state."""
        from selfhealing.interfaces.repositories import CircuitBreakerStateEnum

        repo.get_or_create("test_service")

        now = datetime.now(timezone.utc)
        result = repo.update_state(
            service_name="test_service",
            state=CircuitBreakerStateEnum.OPEN.value,
            failure_count=5,
            opened_at=now,
        )

        assert result is True

        state = repo.get_by_service_name("test_service")
        assert state.state == CircuitBreakerStateEnum.OPEN.value
        assert state.failure_count == 5
        assert state.opened_at == now

    def test_increment_failure_count(self, repo):
        """Test incrementing failure count."""
        repo.get_or_create("test_service")

        new_count = repo.increment_failure_count("test_service")
        assert new_count == 1

        new_count = repo.increment_failure_count("test_service")
        assert new_count == 2

        state = repo.get_by_service_name("test_service")
        assert state.failure_count == 2
        assert state.last_failure_at is not None

    def test_reset_counts(self, repo):
        """Test resetting failure and success counts."""
        repo.get_or_create("test_service")
        repo.increment_failure_count("test_service")
        repo.increment_failure_count("test_service")

        result = repo.reset_counts("test_service")
        assert result is True

        state = repo.get_by_service_name("test_service")
        assert state.failure_count == 0
        assert state.success_count == 0

    def test_set_manual_control(self, repo):
        """Test setting manual control override."""
        from selfhealing.interfaces.repositories import CircuitBreakerStateEnum

        repo.get_or_create("test_service")

        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        result = repo.set_manual_control(
            service_name="test_service",
            state=CircuitBreakerStateEnum.OPEN.value,
            controlled_by_id=42,
            reason="Manual intervention during maintenance",
            expires_at=expires,
        )

        assert result is True

        state = repo.get_by_service_name("test_service")
        assert state.state == CircuitBreakerStateEnum.OPEN.value
        assert state.manually_controlled is True
        assert state.controlled_by_id == 42
        assert state.control_reason == "Manual intervention during maintenance"
        assert state.manual_override_expires_at == expires

    def test_clear_manual_control(self, repo):
        """clear_manual_control은 수동 제어 플래그만 해제하고 상태/카운터는 유지한다."""
        from selfhealing.interfaces.repositories import CircuitBreakerStateEnum

        repo.get_or_create("test_service")
        repo.set_manual_control(
            service_name="test_service",
            state=CircuitBreakerStateEnum.OPEN.value,
            controlled_by_id=42,
            reason="Test",
        )

        result = repo.clear_manual_control("test_service")
        assert result is True

        state = repo.get_by_service_name("test_service")
        # 상태는 set_manual_control에서 설정한 OPEN이 유지된다
        assert state.state == CircuitBreakerStateEnum.OPEN.value
        assert state.manually_controlled is False
        assert state.controlled_by_id is None

    def test_thread_safety(self, repo):
        """Test thread safety with concurrent increments."""
        repo.get_or_create("test_service")

        def increment():
            for _ in range(100):
                repo.increment_failure_count("test_service")

        threads = []
        for _ in range(5):
            t = threading.Thread(target=increment)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        state = repo.get_by_service_name("test_service")
        assert state.failure_count == 500
