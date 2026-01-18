"""
InMemoryFailedOperationRepository 테스트.
"""

import threading

import pytest


class TestInMemoryFailedOperationRepository:
    """Tests for InMemoryFailedOperationRepository."""

    @pytest.fixture
    def repo(self):
        """Create a fresh repository for each test."""
        from selfhealing.adapters.memory import InMemoryFailedOperationRepository
        return InMemoryFailedOperationRepository()

    def test_create_failed_operation(self, repo):
        """Test creating a new failed operation."""
        from selfhealing.interfaces.repositories import FailedOperationStatus
        
        entry = repo.create(
            domain="payment",
            failure_type="gateway_timeout",
            error_message="Connection timeout to payment gateway",
            error_code="TIMEOUT_001",
            entity_type="order",
            entity_id="12345",
            entity_refs={"order_id": 12345, "payment_id": 67890},
            user_id=100,
        )

        assert entry.id == 1
        assert entry.domain == "payment"
        assert entry.failure_type == "gateway_timeout"
        assert entry.error_message == "Connection timeout to payment gateway"
        assert entry.error_code == "TIMEOUT_001"
        assert entry.entity_type == "order"
        assert entry.entity_id == "12345"
        assert entry.entity_refs.get("order_id") == 12345
        assert entry.entity_refs.get("payment_id") == 67890
        assert entry.user_id == 100
        assert entry.status == FailedOperationStatus.PENDING.value
        assert entry.created_at is not None
        assert entry.retry_count == 0

    def test_get_by_id(self, repo):
        """Test retrieving a failed operation by ID."""
        created = repo.create(
            domain="payment",
            failure_type="validation_error",
            error_message="Invalid card number",
        )

        retrieved = repo.get_by_id(created.id)
        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.domain == "payment"
        assert retrieved.failure_type == "validation_error"

    def test_get_by_id_not_found(self, repo):
        """Test retrieving a non-existent failed operation."""
        result = repo.get_by_id(99999)
        assert result is None

    def test_get_pending_by_domain(self, repo):
        """Test filtering pending operations by domain."""
        repo.create(domain="payment", failure_type="error1", error_message="err")
        repo.create(domain="payment", failure_type="error2", error_message="err")
        repo.create(domain="webhook", failure_type="error3", error_message="err")

        payment_entries = repo.get_pending_by_domain("payment")
        assert len(payment_entries) == 2

        webhook_entries = repo.get_pending_by_domain("webhook")
        assert len(webhook_entries) == 1

    def test_update_status(self, repo):
        """Test updating the status of a failed operation."""
        from selfhealing.interfaces.repositories import FailedOperationStatus
        
        entry = repo.create(
            domain="payment",
            failure_type="timeout",
            error_message="Request timeout",
        )

        result = repo.update_status(
            entry.id,
            FailedOperationStatus.RESOLVED.value,
            resolution_type="manual",
            resolution_note="Fixed by admin",
        )

        assert result is True

        updated = repo.get_by_id(entry.id)
        assert updated.status == FailedOperationStatus.RESOLVED.value
        assert updated.resolution_type == "manual"
        assert updated.resolution_note == "Fixed by admin"
        assert updated.resolved_at is not None

    def test_increment_retry_count(self, repo):
        """Test incrementing the retry count."""
        entry = repo.create(
            domain="payment",
            failure_type="network_error",
            error_message="Connection refused",
        )

        assert entry.retry_count == 0

        result = repo.increment_retry_count(entry.id)
        assert result is True

        updated = repo.get_by_id(entry.id)
        assert updated.retry_count == 1
        assert updated.last_retry_at is not None

        repo.increment_retry_count(entry.id)
        updated = repo.get_by_id(entry.id)
        assert updated.retry_count == 2

    def test_thread_safety(self, repo):
        """Test thread safety with concurrent operations."""
        results = []
        errors = []

        def create_operation(n):
            try:
                entry = repo.create(
                    domain="payment",
                    failure_type=f"error_{n}",
                    error_message=f"Error message {n}",
                )
                results.append(entry.id)
            except Exception as e:
                errors.append(str(e))

        threads = []
        for i in range(50):
            t = threading.Thread(target=create_operation, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(set(results)) == 50
        assert sorted(results) == list(range(1, 51))
