"""
Unit tests for core types.
"""

import pytest
from datetime import datetime


class TestFailureType:
    """Tests for FailureType enum."""

    def test_failure_types_exist(self):
        from selfhealing.core.types import FailureType

        assert FailureType.NETWORK == "network"
        assert FailureType.DATABASE == "database"
        assert FailureType.TIMEOUT == "timeout"
        assert FailureType.PAYMENT == "payment"

    def test_failure_type_is_string(self):
        from selfhealing.core.types import FailureType

        assert isinstance(FailureType.NETWORK.value, str)
        assert str(FailureType.NETWORK) == "FailureType.NETWORK"


class TestOperationStatus:
    """Tests for OperationStatus enum."""

    def test_statuses_exist(self):
        from selfhealing.core.types import OperationStatus

        assert OperationStatus.PENDING == "pending"
        assert OperationStatus.PROCESSING == "processing"
        assert OperationStatus.COMPLETED == "completed"
        assert OperationStatus.FAILED == "failed"


class TestCircuitState:
    """Tests for CircuitState enum."""

    def test_states_exist(self):
        from selfhealing.core.types import CircuitState

        assert CircuitState.CLOSED == "closed"
        assert CircuitState.OPEN == "open"
        assert CircuitState.HALF_OPEN == "half_open"


class TestFailedOperationData:
    """Tests for FailedOperationData dataclass."""

    def test_create_with_required_fields(self):
        from selfhealing.core.types import FailedOperationData

        data = FailedOperationData(
            id=1,
            domain="payment",
            failure_type="network",
            status="pending",
            created_at=datetime.now(),
        )

        assert data.id == 1
        assert data.domain == "payment"
        assert data.retry_count == 0  # default
        assert data.max_retries == 3  # default
        assert data.context == {}  # default

    def test_create_with_all_fields(self):
        from selfhealing.core.types import FailedOperationData

        now = datetime.now()
        data = FailedOperationData(
            id=42,
            domain="order",
            failure_type="database",
            status="processing",
            created_at=now,
            context={"order_id": 123},
            error_message="DB connection failed",
            retry_count=2,
            max_retries=5,
        )

        assert data.id == 42
        assert data.context["order_id"] == 123
        assert data.retry_count == 2


class TestCircuitBreakerStateData:
    """Tests for CircuitBreakerStateData dataclass."""

    def test_create_with_defaults(self):
        from selfhealing.core.types import CircuitBreakerStateData

        data = CircuitBreakerStateData(
            service_name="test-service",
            state="closed",
        )

        assert data.service_name == "test-service"
        assert data.state == "closed"
        assert data.failure_count == 0
        assert data.failure_threshold == 5
        assert data.recovery_timeout == 60
