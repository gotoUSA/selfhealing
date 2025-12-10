"""
Integration Test Fixtures for Self-Healing System

Provides fixtures for testing without Django dependencies.
"""

import pytest
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
import uuid


# =============================================================================
# Mock Data Classes
# =============================================================================


@dataclass
class MockFailedOperation:
    """Mock failed operation for testing."""

    id: int
    domain: str
    failure_type: str
    status: str = "pending"
    context: Dict[str, Any] = field(default_factory=dict)
    error_message: str = ""
    retry_count: int = 0
    max_retries: int = 3
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None


@dataclass
class MockCircuitBreakerState:
    """Mock circuit breaker state for testing."""

    service_name: str
    state: str = "closed"
    failure_count: int = 0
    success_count: int = 0
    last_failure_at: Optional[datetime] = None
    opened_at: Optional[datetime] = None


# =============================================================================
# Mock Services
# =============================================================================


class MockDLQService:
    """Mock DLQ Service for integration testing."""

    def __init__(self, enabled: bool = True, retention_days: int = 30, max_replay_attempts: int = 2):
        self.enabled = enabled
        self.retention_days = retention_days
        self.max_replay_attempts = max_replay_attempts
        self._operations: Dict[int, MockFailedOperation] = {}
        self._next_id = 1

    def store(
        self,
        domain: str,
        failure_type: str,
        context: Dict[str, Any],
        error_message: str = "",
        max_retries: int = 3,
    ) -> MockFailedOperation:
        """Store a failed operation in DLQ."""
        op = MockFailedOperation(
            id=self._next_id,
            domain=domain,
            failure_type=failure_type,
            context=context,
            error_message=error_message,
            max_retries=max_retries,
        )
        self._operations[op.id] = op
        self._next_id += 1
        return op

    def get_by_id(self, op_id: int) -> Optional[MockFailedOperation]:
        """Get operation by ID."""
        return self._operations.get(op_id)

    def get_pending(self, domain: Optional[str] = None, limit: int = 100) -> List[MockFailedOperation]:
        """Get pending operations."""
        results = [op for op in self._operations.values() if op.status == "pending"]
        if domain:
            results = [op for op in results if op.domain == domain]
        return results[:limit]

    def get_replayable(self, limit: int = 100) -> List[MockFailedOperation]:
        """Get operations eligible for replay."""
        return [
            op for op in self._operations.values()
            if op.status == "pending" and op.retry_count < op.max_retries
        ][:limit]

    def mark_processing(self, op_id: int) -> bool:
        """Mark operation as processing."""
        op = self._operations.get(op_id)
        if op and op.status == "pending":
            op.status = "processing"
            return True
        return False

    def mark_resolved(self, op_id: int) -> bool:
        """Mark operation as resolved."""
        op = self._operations.get(op_id)
        if op:
            op.status = "resolved"
            op.resolved_at = datetime.now()
            return True
        return False

    def increment_retry(self, op_id: int) -> bool:
        """Increment retry count."""
        op = self._operations.get(op_id)
        if op:
            op.retry_count += 1
            if op.retry_count >= op.max_retries:
                op.status = "requires_review"
            return True
        return False

    def get_stats(self) -> Dict[str, int]:
        """Get DLQ statistics."""
        stats = {"total": 0, "pending": 0, "processing": 0, "resolved": 0, "requires_review": 0}
        for op in self._operations.values():
            stats["total"] += 1
            stats[op.status] = stats.get(op.status, 0) + 1
        return stats


class MockCircuitBreakerService:
    """Mock Circuit Breaker Service for integration testing."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        success_threshold: int = 2,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self._states: Dict[str, MockCircuitBreakerState] = {}

    def _get_state(self, service_name: str) -> MockCircuitBreakerState:
        """Get or create circuit breaker state."""
        if service_name not in self._states:
            self._states[service_name] = MockCircuitBreakerState(service_name=service_name)
        return self._states[service_name]

    def record_failure(self, service_name: str) -> MockCircuitBreakerState:
        """Record a failure."""
        state = self._get_state(service_name)
        state.failure_count += 1
        state.last_failure_at = datetime.now()

        if state.failure_count >= self.failure_threshold:
            state.state = "open"
            state.opened_at = datetime.now()

        return state

    def record_success(self, service_name: str) -> MockCircuitBreakerState:
        """Record a success."""
        state = self._get_state(service_name)
        state.success_count += 1

        if state.state == "half_open":
            if state.success_count >= self.success_threshold:
                state.state = "closed"
                state.failure_count = 0

        return state

    def get_state(self, service_name: str) -> str:
        """Get current circuit breaker state."""
        return self._get_state(service_name).state

    def is_open(self, service_name: str) -> bool:
        """Check if circuit is open."""
        return self.get_state(service_name) == "open"

    def should_allow_request(self, service_name: str) -> bool:
        """Check if request should be allowed."""
        state = self.get_state(service_name)
        return state in ("closed", "half_open")

    def force_open(self, service_name: str, reason: str = "") -> MockCircuitBreakerState:
        """Force circuit to open state."""
        state = self._get_state(service_name)
        state.state = "open"
        state.opened_at = datetime.now()
        return state

    def force_close(self, service_name: str, reason: str = "") -> MockCircuitBreakerState:
        """Force circuit to closed state."""
        state = self._get_state(service_name)
        state.state = "closed"
        state.failure_count = 0
        return state

    def transition_to_half_open(self, service_name: str) -> MockCircuitBreakerState:
        """Transition to half-open state."""
        state = self._get_state(service_name)
        if state.state == "open":
            state.state = "half_open"
            state.success_count = 0
        return state


class MockReplayService:
    """Mock Replay Service for integration testing."""

    def __init__(self, dlq_service: MockDLQService):
        self.dlq_service = dlq_service
        self._replay_handlers: Dict[str, callable] = {}
        self.replay_history: List[Dict[str, Any]] = []

    def register_handler(self, domain: str, handler: callable):
        """Register a replay handler for a domain."""
        self._replay_handlers[domain] = handler

    def replay(self, op_id: int) -> Dict[str, Any]:
        """Replay a failed operation."""
        op = self.dlq_service.get_by_id(op_id)
        if not op:
            return {"success": False, "error": "Operation not found"}

        handler = self._replay_handlers.get(op.domain)
        if not handler:
            return {"success": False, "error": f"No handler for domain: {op.domain}"}

        self.dlq_service.mark_processing(op_id)

        try:
            result = handler(op)
            if result.get("success"):
                self.dlq_service.mark_resolved(op_id)
            else:
                self.dlq_service.increment_retry(op_id)

            self.replay_history.append({
                "op_id": op_id,
                "domain": op.domain,
                "result": result,
                "timestamp": datetime.now(),
            })
            return result
        except Exception as e:
            self.dlq_service.increment_retry(op_id)
            return {"success": False, "error": str(e)}

    def batch_replay(
        self,
        domain: Optional[str] = None,
        failure_type: Optional[str] = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """Replay a batch of operations."""
        ops = self.dlq_service.get_replayable(limit=limit)

        if domain:
            ops = [op for op in ops if op.domain == domain]
        if failure_type:
            ops = [op for op in ops if op.failure_type == failure_type]

        results = {"total": len(ops), "success": 0, "failed": 0, "details": []}

        for op in ops:
            result = self.replay(op.id)
            if result.get("success"):
                results["success"] += 1
            else:
                results["failed"] += 1
            results["details"].append({"op_id": op.id, "result": result})

        return results


# =============================================================================
# Pytest Fixtures
# =============================================================================


@pytest.fixture
def mock_dlq_service():
    """Create a mock DLQ service."""
    return MockDLQService(enabled=True, retention_days=30, max_replay_attempts=2)


@pytest.fixture
def mock_circuit_breaker_service():
    """Create a mock circuit breaker service."""
    return MockCircuitBreakerService(
        failure_threshold=5,
        recovery_timeout=60,
        success_threshold=2,
    )


@pytest.fixture
def mock_replay_service(mock_dlq_service):
    """Create a mock replay service."""
    return MockReplayService(dlq_service=mock_dlq_service)


@pytest.fixture
def sample_failed_ops(mock_dlq_service):
    """Create sample failed operations."""
    ops = []
    for i in range(5):
        op = mock_dlq_service.store(
            domain=["payment", "order", "notification"][i % 3],
            failure_type=["PG_TIMEOUT", "NETWORK_ERROR", "VALIDATION"][i % 3],
            context={"test_id": i},
            error_message=f"Test error {i}",
        )
        ops.append(op)
    return ops
