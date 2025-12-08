# Self-Healing Test Specifications

> **Document Type**: Detailed Test Case Specifications
> **Version**: 1.0
> **Last Updated**: 2025-12-08
> **Parent Document**: SELF_HEALING_TEST_STRATEGY.md

---

## 1. Multi-Tenancy Isolation Tests

**File**: `integration/self_healing/test_multi_tenancy_isolation.py`

**Business Risk**: Cross-tenant data leakage, unauthorized access, regulatory violation

**Compliance Alignment**: SOC 2 (Confidentiality), ISO 27001 A.9 (Access Control)

### Test Cases

| ID | Test Name | Scenario | Expected Behavior |
|----|-----------|----------|-------------------|
| MT-001 | `test_tenant_a_circuit_open_does_not_affect_tenant_b` | Tenant A's CB opens due to failures | Tenant B requests continue normally |
| MT-002 | `test_dlq_entries_isolated_by_tenant` | Both tenants have DLQ entries | Query returns only own tenant's entries |
| MT-003 | `test_tenant_specific_sla_policies_enforced` | Tenant A: 300s SLA, Tenant B: 600s SLA | Each tenant's timeout is respected |
| MT-004 | `test_tenant_metrics_aggregated_separately` | Both tenants emit metrics | Labels contain correct tenant_id |
| MT-005 | `test_tenant_specific_replay_rate_limits` | Tenant A: 10/min, Tenant B: 50/min | Rate limits enforced per tenant |
| MT-006 | `test_tenant_admin_cannot_force_close_other_tenant_cb` | Tenant A admin attempts to close Tenant B CB | Authorization error, audit logged |

### Sample Test Implementation

```python
@pytest.mark.tier2
@pytest.mark.tenant_aware
class TestMultiTenancyCircuitBreakerIsolation:
    """
    Multi-tenancy isolation tests for Circuit Breaker.

    Validates that tenant boundaries are enforced and
    one tenant's failures do not impact another.
    """

    def test_tenant_a_circuit_open_does_not_affect_tenant_b(
        self,
        tenant_a,
        tenant_b,
        circuit_breaker_service,
    ):
        """
        Purpose:
            Verify Circuit Breaker state is isolated per tenant.

        Scenario:
            1. Create Circuit Breaker for Tenant A (toss_payment)
            2. Force open Tenant A's Circuit Breaker
            3. Verify Tenant A requests are blocked
            4. Verify Tenant B requests are allowed

        Expected:
            - Tenant A: should_allow() returns False
            - Tenant B: should_allow() returns True
            - Audit log shows tenant_id for state change

        Risk Covered:
            R-001: Cross-tenant contamination

        Compliance:
            SOC 2 CC6.1 (Logical Access), ISO 27001 A.9.4.1
        """
        # Arrange
        service_name = "toss_payment"

        # Act: Open Tenant A's circuit
        circuit_breaker_service.force_open(
            service_name=service_name,
            tenant_id=tenant_a.id,
            reason="Tenant A PG maintenance",
            controlled_by=tenant_a.admin_user,
        )

        # Assert
        assert circuit_breaker_service.should_allow(
            service_name, tenant_id=tenant_a.id
        ) is False, "Tenant A should be blocked"

        assert circuit_breaker_service.should_allow(
            service_name, tenant_id=tenant_b.id
        ) is True, "Tenant B should NOT be affected by Tenant A's CB state"
```

### Required Fixtures

```python
@pytest.fixture
def tenant_a(db):
    """Create Tenant A with specific configuration."""
    return TenantFactory(
        name="Tenant A",
        sla_timeout_seconds=300,
        replay_rate_limit=10,
    )

@pytest.fixture
def tenant_b(db):
    """Create Tenant B with different configuration."""
    return TenantFactory(
        name="Tenant B",
        sla_timeout_seconds=600,
        replay_rate_limit=50,
    )
```

---

## 2. Cost-Aware Recovery Tests

**File**: `integration/self_healing/test_cost_aware_recovery.py`

**Business Risk**: Unbounded retry costs exceeding transaction value

**Compliance Alignment**: Internal cost governance, SOC 2 (Availability vs. Cost trade-off)

### Test Cases

| ID | Test Name | Scenario | Expected Behavior |
|----|-----------|----------|-------------------|
| COST-001 | `test_retry_stops_when_cost_exceeds_threshold` | Retry cost > 10% of transaction | Early DLQ with reason=`cost_prohibitive` |
| COST-002 | `test_low_value_transaction_skips_retry` | Transaction < ₩1,000, retry cost ₩500 | Direct DLQ, no retry attempted |
| COST-003 | `test_high_value_transaction_always_retries` | Transaction > ₩100,000 | Full retry attempts regardless of cost |
| COST-004 | `test_cost_threshold_dynamically_adjustable` | Config change from 10% to 5% | New threshold applies to next decision |
| COST-005 | `test_cost_decision_audit_log` | Any cost-based decision | `cost_estimate`, `threshold`, `decision` logged |
| COST-006 | `test_cumulative_cost_tracking` | Multiple retries | Total cost accumulates correctly |

### Sample Test Implementation

```python
@pytest.mark.tier2
@pytest.mark.cost_sensitive
class TestCostAwareRecovery:
    """
    Cost-aware recovery decision tests.

    Validates that the system makes economically rational
    decisions about retry vs. DLQ routing.
    """

    def test_retry_stops_when_cost_exceeds_threshold(
        self,
        recovery_handler,
        cost_tracker,
        sample_payment,
    ):
        """
        Purpose:
            Verify retry stops when cumulative cost exceeds threshold.

        Scenario:
            1. Payment of ₩10,000 fails
            2. Cost per retry: ₩500 (PG API call)
            3. Threshold: 10% of transaction (₩1,000)
            4. After 2 retries (₩1,000 cost), next retry should abort

        Expected:
            - 2 retries attempted
            - 3rd retry blocked, moved to DLQ
            - DLQ reason: "cost_prohibitive"
            - Audit log contains cost analysis

        Risk Covered:
            R-002: Unbounded retry costs
        """
        # Arrange
        sample_payment.amount = Decimal("10000")
        cost_tracker.cost_per_call = Decimal("500")

        # Act: Simulate failures
        result = recovery_handler.handle_failure_with_cost_awareness(
            payment=sample_payment,
            error_code="PG_TIMEOUT",
            cost_tracker=cost_tracker,
        )

        # Assert
        assert result["action"] == "moved_to_dlq"
        assert result["reason"] == "cost_prohibitive"
        assert cost_tracker.total_cost == Decimal("1000")
        assert result["audit"]["cost_estimate"] == "1000"
        assert result["audit"]["threshold"] == "1000"  # 10% of 10000
```

### Mock Cost Tracker

```python
class MockCostTracker:
    """
    Tracks mock API call costs for testing.

    Simulates PG API costs without actual external calls.
    """

    def __init__(self, cost_per_call: Decimal = Decimal("50")):
        self.cost_per_call = cost_per_call
        self.total_cost = Decimal("0")
        self.call_count = 0
        self.call_history = []

    def record_call(self, operation: str = "api_call"):
        self.call_count += 1
        self.total_cost += self.cost_per_call
        self.call_history.append({
            "operation": operation,
            "cost": self.cost_per_call,
            "cumulative": self.total_cost,
            "timestamp": timezone.now(),
        })

    def get_cost_analysis(self) -> dict:
        return {
            "total_calls": self.call_count,
            "total_cost": str(self.total_cost),
            "cost_per_call": str(self.cost_per_call),
            "history": self.call_history,
        }
```

---

## 3. Observability & Metrics Tests

**File**: `integration/self_healing/test_observability_metrics.py`

**Business Risk**: Silent metric loss, monitoring blind spots

**Compliance Alignment**: SOC 2 (Monitoring), NIST AU-3 (Audit Content)

### Test Cases

| ID | Test Name | Metric Validated | Expected Value |
|----|-----------|------------------|----------------|
| OBS-001 | `test_failure_counter_increments` | `payment_failures_total` | +1 per failure |
| OBS-002 | `test_circuit_breaker_state_change_emitted` | `circuit_breaker_state_changes_total` | +1 per transition |
| OBS-003 | `test_dlq_entry_creation_metric` | `dlq_entries_created_total` | Labeled by failure_type |
| OBS-004 | `test_sla_breach_histogram` | `sla_breaches_total` | Bucket populated |
| OBS-005 | `test_retry_latency_histogram` | `retry_latency_seconds` | Accurate duration |
| OBS-006 | `test_alert_fires_on_failure_rate_threshold` | Alert trigger | Fires when rate > 5% |
| OBS-007 | `test_trace_id_propagates_through_flow` | Trace correlation | Same ID across retry→DLQ→replay |
| OBS-008 | `test_tenant_label_attached_to_metrics` | All metrics | `tenant_id` label present |

### Metrics Protocol Abstraction

```python
from typing import Protocol

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
    """Mock implementation for testing."""

    def __init__(self):
        self._counters: dict[tuple, float] = {}
        self._histograms: dict[tuple, list[float]] = {}
        self._gauges: dict[tuple, float] = {}

    def _key(self, name: str, labels: dict | None) -> tuple:
        label_tuple = tuple(sorted((labels or {}).items()))
        return (name, label_tuple)

    def increment(self, name: str, value: int = 1, labels: dict | None = None):
        key = self._key(name, labels)
        self._counters[key] = self._counters.get(key, 0) + value

    def get_value(self, name: str, labels: dict | None = None) -> float:
        return self._counters.get(self._key(name, labels), 0)
```

### Sample Test Implementation

```python
@pytest.mark.tier2
class TestObservabilityMetrics:
    """
    Observability and metrics emission tests.

    Validates that all system events emit correct metrics
    for monitoring, alerting, and SLA tracking.
    """

    def test_failure_counter_increments_correctly(
        self,
        recovery_handler,
        mock_metrics,
        sample_payment,
    ):
        """
        Purpose:
            Verify payment failures increment the correct counter.

        Scenario:
            1. Initial counter value: 0
            2. Trigger 5 payment failures
            3. Check counter value

        Expected:
            - payment_failures_total = 5
            - Labels include error_code and tenant_id

        Risk Covered:
            R-003: Silent metric loss

        Compliance:
            SOC 2 CC7.2 (Monitoring), NIST AU-3
        """
        # Arrange
        initial_value = mock_metrics.get_value(
            "payment_failures_total",
            labels={"tenant_id": "tenant_a"},
        )

        # Act
        for i in range(5):
            recovery_handler.handle_failure(
                payment=sample_payment,
                error_code="PG_TIMEOUT",
            )

        # Assert
        final_value = mock_metrics.get_value(
            "payment_failures_total",
            labels={"tenant_id": "tenant_a"},
        )

        assert final_value == initial_value + 5, (
            f"Expected counter to increment by 5, "
            f"got {final_value - initial_value}"
        )
```

---

## 4. Cascading Failure Tests

**File**: `integration/self_healing/test_cascading_failures.py`

**Business Risk**: System-wide outage from failure chain reaction

**Compliance Alignment**: NIST CP-2 (Contingency Planning), SOC 2 (Availability)

### Failure Matrix

| ID | Primary Failure | Secondary Failure | Expected Behavior |
|----|-----------------|-------------------|-------------------|
| CASC-001 | PG Timeout | DB Connection Lost | Local fallback logging, no data loss |
| CASC-002 | DLQ Insert | Redis Down | Synchronous fallback queue |
| CASC-003 | Retry Task | Celery Broker Down | Persist to file, manual recovery |
| CASC-004 | CB State Update | DB Locked | Graceful degradation, default-allow |
| CASC-005 | Notification Send | SMTP Down | Async retry, no blocking |
| CASC-006 | Rollback Execution | Inventory Service Down | Compensating transaction queued |
| CASC-007 | Handler Crash | DLQ Full | REQUIRES_REVIEW escalation |

### Sample Test Implementation

```python
@pytest.mark.tier3_chaos
class TestCascadingFailures:
    """
    Cascading failure handling tests.

    Validates system behavior when multiple components
    fail simultaneously or in sequence.
    """

    def test_pg_timeout_with_db_connection_lost(
        self,
        recovery_handler,
        failure_injector,
        sample_payment,
    ):
        """
        Purpose:
            Validate graceful degradation when PG and DB both fail.

        Scenario:
            1. Inject PG timeout on payment attempt
            2. During DLQ write, inject DB connection error
            3. Verify fallback logging triggered
            4. Verify no data loss (fallback file exists)

        Expected:
            - Primary failure: PG_TIMEOUT detected
            - Secondary failure: DB write fails
            - Fallback: Local file logging activated
            - Recovery: Manual recovery path documented
            - No data loss: Fallback contains full context

        Risk Covered:
            R-004: Cascading system failure

        Compliance:
            NIST CP-2 (Contingency Planning)
        """
        # Arrange
        failure_injector.inject_pg_timeout()
        failure_injector.inject_db_connection_error_on_write()

        # Act
        result = recovery_handler.handle_failure(
            payment=sample_payment,
            error_code="PG_TIMEOUT",
        )

        # Assert
        assert result["action"] == "fallback_logged"
        assert result["fallback_path"] is not None

        # Verify fallback file contains full context
        with open(result["fallback_path"], "r") as f:
            fallback_data = json.load(f)

        assert fallback_data["payment_id"] == sample_payment.id
        assert fallback_data["error_code"] == "PG_TIMEOUT"
        assert fallback_data["secondary_error"] == "DB_CONNECTION_LOST"
        assert "recovery_instructions" in fallback_data
```

---

## 5. Cold Start / Restart Stability Tests

**File**: `integration/self_healing/test_cold_start_recovery.py`

**Business Risk**: Data loss or inconsistent state after restart

**Compliance Alignment**: NIST CP-2 (Contingency Planning), SOC 2 (Availability)

### Test Cases

| ID | Test Name | Scenario | Validation |
|----|-----------|----------|------------|
| COLD-001 | `test_cb_state_restored_from_db_after_restart` | Server restart | CB state matches pre-restart |
| COLD-002 | `test_pending_dlq_items_resume_after_restart` | Restart with pending DLQ | Processing continues |
| COLD-003 | `test_orphaned_retry_tasks_detected` | Restart during retry | Marked for re-queue |
| COLD-004 | `test_multiple_instance_startup_no_duplicates` | 3 instances start simultaneously | No duplicate processing |
| COLD-005 | `test_config_reload_without_restart` | Runtime config change | New config applies |
| COLD-006 | `test_partial_startup_degraded_mode` | DB up, Redis down | Degraded mode logging |

### Sample Test Implementation

```python
@pytest.mark.tier3_chaos
class TestColdStartRecovery:
    """
    Cold start and restart stability tests.

    Validates state persistence and recovery after
    service restart or deployment.
    """

    def test_circuit_breaker_state_restored_from_db(
        self,
        circuit_breaker_service,
        db,
    ):
        """
        Purpose:
            Verify CB state is correctly restored after restart.

        Scenario:
            1. Force open Circuit Breaker for "toss_payment"
            2. Simulate service restart (clear in-memory state)
            3. Re-initialize Circuit Breaker service
            4. Verify state is restored from DB

        Expected:
            - State is "open" after restart
            - All metadata (reason, controlled_by) preserved
            - No requests allowed until explicitly closed

        Risk Covered:
            R-005: Data loss on restart

        Compliance:
            NIST CP-2, SOC 2 CC7.4
        """
        service_name = "toss_payment"

        # Arrange: Set up open circuit breaker
        circuit_breaker_service.force_open(
            service_name=service_name,
            reason="Pre-restart test state",
            controlled_by=admin_user,
        )

        # Act: Simulate restart
        circuit_breaker_service._clear_cache()  # Clear in-memory state
        new_service = CircuitBreakerService()  # New instance

        # Assert
        state = new_service.get_state(service_name)
        assert state.state == CircuitState.OPEN
        assert state.control_reason == "Pre-restart test state"
        assert new_service.should_allow(service_name) is False
```

---

## 6. Audit Accountability Tests

**File**: `integration/self_healing/test_audit_accountability.py`

**Business Risk**: Unable to prove compliance, no forensic trail

**Compliance Alignment**: NIST AU-3, SOC 2 (Audit Logging), ISO 27001 A.12.4

### Audit Fields by Action Type

| Action Type | Required Fields |
|-------------|-----------------|
| `circuit_breaker_action` | `controlled_by`, `control_reason`, `previous_state`, `new_state`, `timestamp` |
| `dlq_entry` | `domain`, `failure_type`, `error_code`, `created_at`, `forensic_context` |
| `replay_action` | `replayed_by`, `replay_reason`, `attempt_number`, `outcome` |
| `cost_decision` | `transaction_value`, `cost_estimate`, `threshold`, `decision`, `rationale` |
| `sla_abort` | `sla_config`, `elapsed_time`, `abort_trigger`, `dlq_id` |
| `escalation` | `escalation_reason`, `failure_history`, `recommended_action` |

### Test Cases

| ID | Test Name | Action | Fields Validated |
|----|-----------|--------|------------------|
| AUDIT-001 | `test_manual_cb_force_open_audit` | CB force-open | controlled_by, reason, timestamp |
| AUDIT-002 | `test_dlq_replay_by_admin_audit` | DLQ replay | replayed_by, source_dlq_id |
| AUDIT-003 | `test_auto_retry_decision_audit` | Auto-retry | decision_engine, policy_version |
| AUDIT-004 | `test_cost_based_dlq_audit` | Cost-based abort | cost_estimate, threshold |
| AUDIT-005 | `test_sla_abort_action_audit` | SLA timeout | elapsed_time, sla_config |
| AUDIT-006 | `test_requires_review_escalation_audit` | Escalation | failure_history, reason |
| AUDIT-007 | `test_resolution_by_staff_audit` | DLQ resolution | resolved_by, outcome |

### Sample Test Implementation

```python
@pytest.mark.tier2
class TestAuditAccountability:
    """
    Audit trail and accountability tests.

    Validates that all autonomous decisions leave
    complete audit trails for compliance.
    """

    def test_manual_circuit_breaker_force_open_creates_audit(
        self,
        circuit_breaker_service,
        audit_log_repository,
        admin_user,
    ):
        """
        Purpose:
            Verify manual CB operations create complete audit trail.

        Scenario:
            1. Admin forces Circuit Breaker open
            2. Query audit log for the action
            3. Verify all required fields are present

        Expected:
            - Audit entry created
            - controlled_by: admin user ID
            - control_reason: provided reason
            - previous_state: "closed"
            - new_state: "open"
            - timestamp: within 1 second of action

        Risk Covered:
            R-006: Unaccountable decisions

        Compliance:
            SOC 2 CC4.1, NIST AU-3, ISO 27001 A.12.4.1
        """
        # Arrange
        service_name = "toss_payment"
        reason = "Emergency maintenance window"

        # Act
        result = circuit_breaker_service.force_open(
            service_name=service_name,
            reason=reason,
            controlled_by=admin_user,
        )

        # Assert: Query audit log
        audit_entries = audit_log_repository.find_by_action(
            action_type="circuit_breaker_action",
            service_name=service_name,
        )

        assert len(audit_entries) == 1
        entry = audit_entries[0]

        # Validate required fields
        assert entry.controlled_by == admin_user.id
        assert entry.control_reason == reason
        assert entry.previous_state == "closed"
        assert entry.new_state == "open"
        assert abs((entry.timestamp - timezone.now()).total_seconds()) < 1

        # Validate forensic context
        assert entry.ip_address is not None
        assert entry.user_agent is not None
```

---

## 7. Manual Override & Policy Conflict Tests

**File**: `integration/self_healing/test_manual_override_policy.py`

**Business Risk**: Policy deadlock, unclear precedence

**Compliance Alignment**: Change management, operational governance

### Test Cases

| ID | Test Name | Scenario | Expected Behavior |
|----|-----------|----------|-------------------|
| OVER-001 | `test_manual_override_takes_precedence` | Admin opens during auto-recovery | Manual state persists |
| OVER-002 | `test_auto_policy_blocked_while_manual_open` | Auto-close attempted | Remains open |
| OVER-003 | `test_manual_override_expires_after_ttl` | 90 min TTL expires | Auto-policy resumes |
| OVER-004 | `test_conflicting_admin_actions_last_wins` | Admin A opens, Admin B closes | B's action wins, both audited |
| OVER-005 | `test_emergency_bypass_requires_mfa` | Emergency bypass attempted | MFA required, audit logged |
| OVER-006 | `test_policy_version_mismatch_fallback` | Deployment mid-operation | Graceful fallback |

---

## 8. Chaos Engineering Tests

**Directory**: `integration/chaos/`

### 8.1 Partial Failure Patterns

**File**: `test_partial_failure_patterns.py`

| ID | Pattern | Validation |
|----|---------|------------|
| CHAOS-P001 | 30% random failure rate | Recovery rate matches expectation |
| CHAOS-P002 | Burst failures (10 consecutive) | CB triggers, then recovers |
| CHAOS-P003 | Alternating success/failure | No CB trigger (below threshold) |
| CHAOS-P004 | Time-based failure window | Window detection works |

### 8.2 Slow Degradation

**File**: `test_slow_degradation.py`

| ID | Pattern | Validation |
|----|---------|------------|
| CHAOS-S001 | Latency 1s → 30s over 5 min | SLA breach at threshold |
| CHAOS-S002 | Memory pressure gradual | Graceful shedding |
| CHAOS-S003 | Connection pool exhaustion | Backpressure handling |

### 8.3 Recovery During Chaos

**File**: `test_recovery_during_chaos.py`

| ID | Scenario | Validation |
|----|----------|------------|
| CHAOS-R001 | Service recovers while CB open | Half-open detected |
| CHAOS-R002 | Partial recovery (50%) | CB remains half-open |
| CHAOS-R003 | Full recovery during retries | Retries succeed |

---

## 9. End-to-End Lifecycle Tests

**Directory**: `e2e/`

### 9.1 Failure-Recovery Cycle

**File**: `test_failure_recovery_cycle.py`

```python
def test_complete_failure_recovery_failure_cycle(self):
    """
    Purpose:
        Validate repeated failure-recovery cycles.

    Scenario:
        1. Payment fails (PG timeout)
        2. Auto-retry succeeds on 2nd attempt
        3. Same user attempts another payment
        4. Payment fails again (different error)
        5. Max retries exhausted → DLQ
        6. Admin replays → succeeds
        7. Verify complete audit trail

    This is the "failure → recovery → failure again" case
    required by acceptance criteria.
    """
```

### 9.2 User-Invisible Flows

**File**: `test_user_invisible_flows.py`

| ID | User Action | System Behavior | User-Visible Result |
|----|-------------|-----------------|---------------------|
| E2E-U001 | Click "Pay" | PG timeout, auto-retry | Success (4s delay) |
| E2E-U002 | Click "Pay" | CB open, fallback PG | Success via alternate |
| E2E-U003 | Click "Pay" | All retries fail | "Processing, we'll notify" |
| E2E-U004 | Check order | DLQ in progress | Status: "Processing" |

---

## 10. Load & Stress Tests

**Directory**: `load/`

### Test Cases

| ID | Scenario | SLA Target |
|----|----------|------------|
| LOAD-001 | 100 concurrent failures | All 100 in DLQ, no loss |
| LOAD-002 | 1000 req/s, 10% failure | DLQ latency < 100ms |
| LOAD-003 | Queue buildup to 10,000 | No memory exhaustion |
| LOAD-004 | Batch replay of 500 | Complete < 5 minutes |
| LOAD-005 | SLA timer accuracy | ±1s precision at p99 |

---

## Appendix A: Required Fixtures Summary

```python
# conftest.py for self_healing tests

@pytest.fixture
def tenant_a(): ...

@pytest.fixture
def tenant_b(): ...

@pytest.fixture
def cost_tracker(): ...

@pytest.fixture
def mock_metrics(): ...

@pytest.fixture
def failure_injector(): ...

@pytest.fixture
def audit_log_repository(): ...

@pytest.fixture
def cold_start_simulator(): ...
```

---

## Appendix B: Pytest Markers

```python
# All markers used in this specification

pytest.mark.tier1          # Fast unit tests (< 2 min)
pytest.mark.tier2          # Integration tests (< 10 min)
pytest.mark.tier3_chaos    # Chaos engineering (< 30 min)
pytest.mark.tier4_load     # Load/stress tests (< 2 hours)
pytest.mark.tenant_aware   # Multi-tenancy tests
pytest.mark.cost_sensitive # Cost-aware tests
pytest.mark.requires_redis # Tests needing Redis
pytest.mark.requires_celery # Tests needing Celery
```
