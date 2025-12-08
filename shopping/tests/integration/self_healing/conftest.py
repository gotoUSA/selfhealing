"""
Self-Healing Test Fixtures

Provides fixtures for multi-tenancy, cost tracking, metrics collection,
and audit logging tests as specified in SELF_HEALING_TEST_SPECIFICATIONS.md.
"""

from datetime import timedelta
from decimal import Decimal
from typing import Any, Protocol
from dataclasses import dataclass, field
import uuid

import pytest
from django.utils import timezone

from shopping.models.failed_payment import CircuitBreakerState, FailedPayment
from shopping.models.user import User
from shopping.services.payment_recovery_service import CeleryPaymentRecovery
from shopping.tests.factories import OrderFactory, PaymentFactory, UserFactory


# =============================================================================
# Tenant Model Extension (Simulated for testing)
# =============================================================================


@dataclass
class Tenant:
    """
    Simulated Tenant for multi-tenancy testing.

    In production, this would be a Django model with proper DB storage.
    For testing purposes, we use a dataclass with configurable policies.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = "Default Tenant"
    sla_timeout_seconds: int = 300  # 5 minutes default
    replay_rate_limit: int = 10  # replays per minute
    admin_user: User | None = None
    max_retry_cost_percent: Decimal = Decimal("10.0")  # 10% of transaction value
    cost_per_retry: Decimal = Decimal("50")  # KRW per API call


@pytest.fixture
def tenant_a(db) -> Tenant:
    """
    Create Tenant A with specific configuration.

    Tenant A uses stricter SLA policies (300s) and lower rate limits (10/min).
    """
    admin = UserFactory.admin(username="tenant_a_admin")
    return Tenant(
        id="tenant_a",
        name="Tenant A",
        sla_timeout_seconds=300,
        replay_rate_limit=10,
        admin_user=admin,
        max_retry_cost_percent=Decimal("10.0"),
        cost_per_retry=Decimal("500"),
    )


@pytest.fixture
def tenant_b(db) -> Tenant:
    """
    Create Tenant B with different configuration.

    Tenant B uses relaxed SLA policies (600s) and higher rate limits (50/min).
    """
    admin = UserFactory.admin(username="tenant_b_admin")
    return Tenant(
        id="tenant_b",
        name="Tenant B",
        sla_timeout_seconds=600,
        replay_rate_limit=50,
        admin_user=admin,
        max_retry_cost_percent=Decimal("15.0"),
        cost_per_retry=Decimal("100"),
    )


# =============================================================================
# Circuit Breaker Service Wrapper (Tenant-aware)
# =============================================================================


class TenantAwareCircuitBreakerService:
    """
    Tenant-aware Circuit Breaker service wrapper.

    Provides tenant isolation by using tenant-specific service names.
    """

    def __init__(self):
        self._recovery_handler = CeleryPaymentRecovery()

    def _get_service_key(self, service_name: str, tenant_id: str | None = None) -> str:
        """Generate tenant-specific service key."""
        if tenant_id:
            return f"{service_name}:{tenant_id}"
        return service_name

    def get_or_create_state(
        self, service_name: str, tenant_id: str | None = None
    ) -> CircuitBreakerState:
        """Get or create circuit breaker state for tenant-specific service."""
        key = self._get_service_key(service_name, tenant_id)
        state, _ = CircuitBreakerState.objects.get_or_create(
            service_name=key,
            defaults={"state": "closed"},
        )
        return state

    def should_allow(self, service_name: str, tenant_id: str | None = None) -> bool:
        """Check if requests should be allowed for tenant's circuit breaker."""
        state = self.get_or_create_state(service_name, tenant_id)
        # For manually controlled open state, always block
        if state.manually_controlled and state.state == "open":
            return False
        return state.should_allow_request()

    def force_open(
        self,
        service_name: str,
        tenant_id: str | None = None,
        reason: str = "",
        controlled_by: User | None = None,
        ttl_minutes: int = 90,
    ) -> CircuitBreakerState:
        """Force circuit breaker to OPEN state for specific tenant."""
        state = self.get_or_create_state(service_name, tenant_id)
        state.force_open(controlled_by=controlled_by, reason=reason, ttl_minutes=ttl_minutes)
        return state

    def force_close(
        self,
        service_name: str,
        tenant_id: str | None = None,
        reason: str = "",
        controlled_by: User | None = None,
    ) -> CircuitBreakerState:
        """Force circuit breaker to CLOSED state for specific tenant."""
        state = self.get_or_create_state(service_name, tenant_id)
        state.force_close(controlled_by=controlled_by, reason=reason)
        return state

    def get_state(self, service_name: str, tenant_id: str | None = None) -> CircuitBreakerState:
        """Get current circuit breaker state for tenant."""
        return self.get_or_create_state(service_name, tenant_id)


@pytest.fixture
def circuit_breaker_service(db) -> TenantAwareCircuitBreakerService:
    """Tenant-aware circuit breaker service instance."""
    return TenantAwareCircuitBreakerService()


# =============================================================================
# Cost Tracking Mock Framework
# =============================================================================


@dataclass
class CostRecord:
    """Individual cost record for tracking."""

    operation: str
    cost: Decimal
    cumulative: Decimal
    timestamp: Any = field(default_factory=timezone.now)


class MockCostTracker:
    """
    Tracks mock API call costs for testing.

    Simulates PG API costs without actual external calls.
    Provides cost analysis for cost-aware recovery decisions.
    """

    def __init__(self, cost_per_call: Decimal = Decimal("50")):
        self.cost_per_call = cost_per_call
        self.total_cost = Decimal("0")
        self.call_count = 0
        self.call_history: list[CostRecord] = []

    def record_call(self, operation: str = "api_call") -> CostRecord:
        """Record an API call cost."""
        self.call_count += 1
        self.total_cost += self.cost_per_call
        record = CostRecord(
            operation=operation,
            cost=self.cost_per_call,
            cumulative=self.total_cost,
            timestamp=timezone.now(),
        )
        self.call_history.append(record)
        return record

    def get_cost_analysis(self) -> dict:
        """Get complete cost analysis."""
        return {
            "total_calls": self.call_count,
            "total_cost": str(self.total_cost),
            "cost_per_call": str(self.cost_per_call),
            "history": [
                {
                    "operation": r.operation,
                    "cost": str(r.cost),
                    "cumulative": str(r.cumulative),
                }
                for r in self.call_history
            ],
        }

    def reset(self) -> None:
        """Reset all tracking data."""
        self.total_cost = Decimal("0")
        self.call_count = 0
        self.call_history.clear()

    def would_exceed_threshold(
        self, transaction_amount: Decimal, threshold_percent: Decimal
    ) -> bool:
        """Check if next call would exceed cost threshold."""
        threshold = transaction_amount * threshold_percent / Decimal("100")
        # Check if NEXT call would exceed (not current + next)
        potential_cost = self.total_cost + self.cost_per_call
        return potential_cost > threshold


@pytest.fixture
def cost_tracker() -> MockCostTracker:
    """Mock cost tracker with default cost per call."""
    return MockCostTracker(cost_per_call=Decimal("50"))


@pytest.fixture
def high_cost_tracker() -> MockCostTracker:
    """Mock cost tracker with high cost per call (for threshold testing)."""
    return MockCostTracker(cost_per_call=Decimal("500"))


# =============================================================================
# Metrics Collection Protocol and Mock
# =============================================================================


class MetricsCollector(Protocol):
    """
    Protocol for metrics collection backends.

    Allows testing with mock collector while supporting
    real backends (Prometheus, Datadog, CloudWatch).
    """

    def increment(
        self,
        name: str,
        value: int = 1,
        labels: dict | None = None,
    ) -> None:
        """Increment a counter metric."""
        ...

    def observe(
        self,
        name: str,
        value: float,
        labels: dict | None = None,
    ) -> None:
        """Observe a histogram/summary value."""
        ...

    def gauge(
        self,
        name: str,
        value: float,
        labels: dict | None = None,
    ) -> None:
        """Set a gauge metric."""
        ...

    def get_value(
        self,
        name: str,
        labels: dict | None = None,
    ) -> float:
        """Get current metric value (for testing)."""
        ...


class MockMetricsCollector:
    """
    Mock implementation for metrics collection testing.

    Stores all metrics in memory for inspection during tests.
    """

    def __init__(self):
        self._counters: dict[tuple, float] = {}
        self._histograms: dict[tuple, list[float]] = {}
        self._gauges: dict[tuple, float] = {}
        self._events: list[dict] = []

    def _key(self, name: str, labels: dict | None) -> tuple:
        """Generate unique key for metric + labels combination."""
        label_tuple = tuple(sorted((labels or {}).items()))
        return (name, label_tuple)

    def increment(self, name: str, value: int = 1, labels: dict | None = None) -> None:
        """Increment a counter metric."""
        key = self._key(name, labels)
        self._counters[key] = self._counters.get(key, 0) + value
        self._events.append({
            "type": "counter",
            "name": name,
            "value": value,
            "labels": labels or {},
            "timestamp": timezone.now(),
        })

    def observe(self, name: str, value: float, labels: dict | None = None) -> None:
        """Observe a histogram/summary value."""
        key = self._key(name, labels)
        if key not in self._histograms:
            self._histograms[key] = []
        self._histograms[key].append(value)
        self._events.append({
            "type": "histogram",
            "name": name,
            "value": value,
            "labels": labels or {},
            "timestamp": timezone.now(),
        })

    def gauge(self, name: str, value: float, labels: dict | None = None) -> None:
        """Set a gauge metric."""
        key = self._key(name, labels)
        self._gauges[key] = value
        self._events.append({
            "type": "gauge",
            "name": name,
            "value": value,
            "labels": labels or {},
            "timestamp": timezone.now(),
        })

    def get_value(self, name: str, labels: dict | None = None) -> float:
        """Get current counter value."""
        key = self._key(name, labels)
        return self._counters.get(key, 0)

    def get_histogram(self, name: str, labels: dict | None = None) -> list[float]:
        """Get histogram observations."""
        key = self._key(name, labels)
        return self._histograms.get(key, [])

    def get_gauge(self, name: str, labels: dict | None = None) -> float:
        """Get gauge value."""
        key = self._key(name, labels)
        return self._gauges.get(key, 0)

    def get_events(self, name: str | None = None) -> list[dict]:
        """Get all events, optionally filtered by metric name."""
        if name:
            return [e for e in self._events if e["name"] == name]
        return self._events

    def reset(self) -> None:
        """Reset all metrics."""
        self._counters.clear()
        self._histograms.clear()
        self._gauges.clear()
        self._events.clear()

    def has_label(self, name: str, label_key: str, label_value: str) -> bool:
        """Check if any metric with given name has the specified label."""
        for event in self._events:
            if event["name"] == name and event["labels"].get(label_key) == label_value:
                return True
        return False


@pytest.fixture
def mock_metrics() -> MockMetricsCollector:
    """Mock metrics collector instance."""
    return MockMetricsCollector()


# =============================================================================
# Audit Log Repository Mock
# =============================================================================


@dataclass
class AuditEntry:
    """Individual audit log entry."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:16])
    action_type: str = ""
    service_name: str = ""
    controlled_by: int | None = None
    control_reason: str = ""
    previous_state: str = ""
    new_state: str = ""
    timestamp: Any = field(default_factory=timezone.now)
    metadata: dict = field(default_factory=dict)
    ip_address: str | None = "127.0.0.1"
    user_agent: str | None = "Test-Agent"

    # Cost-related fields
    transaction_value: Decimal | None = None
    cost_estimate: Decimal | None = None
    cost_threshold: Decimal | None = None
    cost_decision: str | None = None

    # SLA-related fields
    sla_config: int | None = None
    elapsed_time: float | None = None
    abort_trigger: str | None = None

    # DLQ-related fields
    dlq_id: int | None = None
    failure_type: str | None = None
    error_code: str | None = None


class MockAuditLogRepository:
    """
    Mock audit log repository for testing.

    Stores audit entries in memory for verification during tests.
    """

    def __init__(self):
        self._entries: list[AuditEntry] = []

    def log(self, entry: AuditEntry) -> AuditEntry:
        """Add an audit entry."""
        self._entries.append(entry)
        return entry

    def log_circuit_breaker_action(
        self,
        service_name: str,
        previous_state: str,
        new_state: str,
        controlled_by: User | None = None,
        reason: str = "",
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuditEntry:
        """Log a circuit breaker state change."""
        entry = AuditEntry(
            action_type="circuit_breaker_action",
            service_name=service_name,
            previous_state=previous_state,
            new_state=new_state,
            controlled_by=controlled_by.id if controlled_by else None,
            control_reason=reason,
            ip_address=ip_address or "127.0.0.1",
            user_agent=user_agent or "Test-Agent",
        )
        return self.log(entry)

    def log_cost_decision(
        self,
        transaction_value: Decimal,
        cost_estimate: Decimal,
        threshold: Decimal,
        decision: str,
        rationale: str = "",
    ) -> AuditEntry:
        """Log a cost-based decision."""
        entry = AuditEntry(
            action_type="cost_decision",
            transaction_value=transaction_value,
            cost_estimate=cost_estimate,
            cost_threshold=threshold,
            cost_decision=decision,
            control_reason=rationale,
        )
        return self.log(entry)

    def log_dlq_entry(
        self,
        dlq_id: int,
        failure_type: str,
        error_code: str,
        metadata: dict | None = None,
    ) -> AuditEntry:
        """Log a DLQ entry creation."""
        entry = AuditEntry(
            action_type="dlq_entry",
            dlq_id=dlq_id,
            failure_type=failure_type,
            error_code=error_code,
            metadata=metadata or {},
        )
        return self.log(entry)

    def log_sla_abort(
        self,
        sla_config: int,
        elapsed_time: float,
        abort_trigger: str,
        dlq_id: int | None = None,
    ) -> AuditEntry:
        """Log an SLA timeout abort."""
        entry = AuditEntry(
            action_type="sla_abort",
            sla_config=sla_config,
            elapsed_time=elapsed_time,
            abort_trigger=abort_trigger,
            dlq_id=dlq_id,
        )
        return self.log(entry)

    def find_by_action(
        self,
        action_type: str,
        service_name: str | None = None,
    ) -> list[AuditEntry]:
        """Find audit entries by action type and optional service name."""
        results = [e for e in self._entries if e.action_type == action_type]
        if service_name:
            results = [e for e in results if e.service_name == service_name]
        return results

    def find_by_dlq_id(self, dlq_id: int) -> list[AuditEntry]:
        """Find audit entries by DLQ ID."""
        return [e for e in self._entries if e.dlq_id == dlq_id]

    def get_all(self) -> list[AuditEntry]:
        """Get all audit entries."""
        return self._entries.copy()

    def reset(self) -> None:
        """Reset all audit entries."""
        self._entries.clear()


@pytest.fixture
def audit_log_repository() -> MockAuditLogRepository:
    """Mock audit log repository instance."""
    return MockAuditLogRepository()


# =============================================================================
# Recovery Handler with Instrumentation
# =============================================================================


class InstrumentedRecoveryHandler(CeleryPaymentRecovery):
    """
    Recovery handler with metrics and audit instrumentation.

    Extends CeleryPaymentRecovery with metrics emission and audit logging
    for testing observability and accountability.
    """

    def __init__(
        self,
        metrics: MockMetricsCollector | None = None,
        audit: MockAuditLogRepository | None = None,
        cost_tracker: MockCostTracker | None = None,
    ):
        super().__init__()
        self.metrics = metrics or MockMetricsCollector()
        self.audit = audit or MockAuditLogRepository()
        self.cost_tracker = cost_tracker

    def handle_failure(self, *args, **kwargs):
        """Handle failure with metrics emission."""
        # Emit failure counter
        labels = {"tenant_id": kwargs.get("tenant_id", "default")}
        self.metrics.increment("payment_failures_total", labels=labels)

        # Track cost if tracker is available
        if self.cost_tracker:
            self.cost_tracker.record_call("handle_failure")

        return super().handle_failure(*args, **kwargs)

    def handle_failure_with_cost_awareness(
        self,
        payment,
        error_code: str,
        cost_tracker: MockCostTracker,
        tenant: Tenant | None = None,
    ) -> dict:
        """
        Handle failure with cost-aware decision making.

        Implements cost-based retry/DLQ routing logic.
        """
        threshold_percent = tenant.max_retry_cost_percent if tenant else Decimal("10.0")
        threshold = payment.amount * threshold_percent / Decimal("100")

        # Simulate retries until cost exceeds threshold
        retry_count = 0
        while not cost_tracker.would_exceed_threshold(payment.amount, threshold_percent):
            cost_tracker.record_call("retry_attempt")
            retry_count += 1
            if retry_count >= 3:  # Max retries
                break

        # Record the decision
        decision = "continue" if retry_count < 3 else "moved_to_dlq"
        reason = "cost_prohibitive" if cost_tracker.total_cost >= threshold else "max_retries"

        # Log audit entry
        self.audit.log_cost_decision(
            transaction_value=payment.amount,
            cost_estimate=cost_tracker.total_cost,
            threshold=threshold,
            decision=decision,
            rationale=f"Retry count: {retry_count}, Cost: {cost_tracker.total_cost}",
        )

        return {
            "action": "moved_to_dlq",
            "reason": reason if cost_tracker.total_cost >= threshold else "max_retries_exceeded",
            "retry_count": retry_count,
            "audit": {
                "cost_estimate": str(int(cost_tracker.total_cost)),
                "threshold": str(int(threshold)),
            },
        }


@pytest.fixture
def recovery_handler(mock_metrics, audit_log_repository) -> InstrumentedRecoveryHandler:
    """Instrumented recovery handler with metrics and audit."""
    return InstrumentedRecoveryHandler(
        metrics=mock_metrics,
        audit=audit_log_repository,
    )


# =============================================================================
# Sample Data Fixtures
# =============================================================================


@pytest.fixture
def sample_payment(db):
    """Create a sample payment for testing."""
    user = UserFactory.with_points(50000)
    order = OrderFactory(user=user, status="confirmed")
    payment = PaymentFactory(
        order=order,
        status="in_progress",
        amount=Decimal("10000"),
    )
    return payment


@pytest.fixture
def high_value_payment(db):
    """Create a high-value payment for cost-aware testing."""
    user = UserFactory.with_points(500000)
    order = OrderFactory(user=user, status="confirmed")
    payment = PaymentFactory(
        order=order,
        status="in_progress",
        amount=Decimal("100000"),
    )
    return payment


@pytest.fixture
def low_value_payment(db):
    """Create a low-value payment for cost-aware testing."""
    user = UserFactory.with_points(5000)
    order = OrderFactory(user=user, status="confirmed")
    payment = PaymentFactory(
        order=order,
        status="in_progress",
        amount=Decimal("1000"),
    )
    return payment


@pytest.fixture
def admin_user(db) -> User:
    """Create an admin user for audit tests."""
    return UserFactory.admin(username="test_admin")
