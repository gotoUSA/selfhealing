# Self-Healing Test Matrices & CI Configuration

> **Document Type**: Risk Matrix, Policy Coverage, CI Rules
> **Version**: 1.0
> **Last Updated**: 2025-12-08
> **Parent Document**: SELF_HEALING_TEST_STRATEGY.md

---

## 1. Risk → Test Coverage Matrix

This matrix maps identified business and technical risks to their corresponding test coverage, providing traceability for audit and compliance purposes.

### 1.1 Complete Risk Matrix

| Risk ID | Risk Description | Severity | Likelihood | Test Coverage | Test File(s) |
|---------|------------------|----------|------------|---------------|--------------|
| **R-001** | Cross-tenant data leakage | 🔴 Critical | Low | MT-001 to MT-006 | `test_multi_tenancy_isolation.py` |
| **R-002** | Unbounded retry costs | 🔴 Critical | Medium | COST-001 to COST-006 | `test_cost_aware_recovery.py` |
| **R-003** | Silent metric loss / monitoring blind spots | 🔴 Critical | Medium | OBS-001 to OBS-008 | `test_observability_metrics.py` |
| **R-004** | Cascading system failure | 🔴 Critical | Low | CASC-001 to CASC-007 | `test_cascading_failures.py` |
| **R-005** | Data loss on restart | 🟡 High | Low | COLD-001 to COLD-006 | `test_cold_start_recovery.py` |
| **R-006** | Unaccountable autonomous decisions | 🟡 High | Medium | AUDIT-001 to AUDIT-007 | `test_audit_accountability.py` |
| **R-007** | Policy conflict / deadlock | 🟡 High | Low | OVER-001 to OVER-006 | `test_manual_override_policy.py` |
| **R-008** | Unpredictable failure patterns | 🟡 High | High | CHAOS-P/S/R series | `integration/chaos/` |
| **R-009** | User-visible failure exposure | 🟡 High | Medium | E2E-U series | `e2e/test_user_invisible_flows.py` |
| **R-010** | Queue saturation under load | 🟡 High | Low | LOAD-001 to LOAD-005 | `load/` |
| **R-011** | Idempotency violation (duplicate payments) | 🔴 Critical | Low | IDEM-001 to IDEM-005 | `test_idempotency_integration.py` |
| **R-012** | SLA breach undetected | 🟡 High | Medium | L3-C series, SLA-* | `test_l3_self_healing.py` |
| **R-013** | Circuit Breaker stuck in wrong state | 🟡 High | Low | L3-D series, CB-* | `test_circuit_breaker.py` |
| **R-014** | DLQ entries lost or corrupted | 🔴 Critical | Low | L3-B series, DLQ-* | `test_dlq_storage_and_replay.py` |
| **R-015** | Retry storm overwhelming PG | 🟡 High | Medium | Backoff tests | `test_self_healing_policy.py` |

### 1.2 Risk Severity Definitions

| Severity | Definition | Response Time | Test Priority |
|----------|------------|---------------|---------------|
| 🔴 **Critical** | Business-stopping, regulatory violation, data breach | Immediate | Tier 1-2 |
| 🟡 **High** | Significant revenue/reputation impact | < 4 hours | Tier 2 |
| 🟢 **Medium** | Degraded service, workaround available | < 24 hours | Tier 3 |
| ⚪ **Low** | Minor inconvenience | Next sprint | Tier 4 |

### 1.3 Compliance Traceability

| Risk ID | Compliance Control | Audit Evidence |
|---------|-------------------|----------------|
| R-001 | SOC 2 CC6.1, ISO 27001 A.9.4.1 | Multi-tenancy test results |
| R-003 | SOC 2 CC7.2, NIST AU-3 | Metrics validation logs |
| R-005 | NIST CP-2, SOC 2 CC7.4 | Cold start test evidence |
| R-006 | NIST AU-3, ISO 27001 A.12.4.1 | Audit trail test results |
| R-011 | PCI-DSS 10.2 | Idempotency test evidence |
| R-014 | SOC 2 CC5.2 | DLQ integrity test results |

---

## 2. Policy Coverage Table

This table documents which tests validate which system policies, ensuring complete policy coverage.

### 2.1 Retry & Backoff Policies

| Policy | Config Key | Default Value | Validated By | Test File |
|--------|------------|---------------|--------------|-----------|
| Max Retry Attempts | `RETRY_MAX_ATTEMPTS` | 3 | L3-A series, COST-003 | `test_l3_self_healing.py` |
| Backoff Base | `RETRY_BACKOFF_BASE` | 4 | Unit: backoff tests | `test_self_healing_policy.py` |
| Backoff Max | `RETRY_BACKOFF_MAX` | 180s | Unit: backoff tests | `test_self_healing_policy.py` |
| Jitter Enabled | `RETRY_JITTER` | True (±25%) | L3-A-Jitter | `test_l3_self_healing.py` |
| Min Delay | `RETRY_MIN_DELAY` | 1s | Unit: backoff tests | `test_self_healing_policy.py` |

### 2.2 SLA Policies

| Policy | Config Key | Default Value | Validated By | Test File |
|--------|------------|---------------|--------------|-----------|
| SLA Timeout | `SLA_TIMEOUT_SECONDS` | 300s | L3-C series | `test_l3_self_healing.py` |
| SLA Abort Enabled | `SLA_ABORT_ENABLED` | True | L3-C-Disabled | `test_l3_self_healing.py` |
| SLA Warning Threshold | `SLA_WARNING_THRESHOLD` | 80% | OBS-004 | `test_observability_metrics.py` |
| Tenant SLA Override | `TENANT_SLA_OVERRIDE` | {} | MT-003 | `test_multi_tenancy_isolation.py` |

### 2.3 Circuit Breaker Policies

| Policy | Config Key | Default Value | Validated By | Test File |
|--------|------------|---------------|--------------|-----------|
| CB Enabled | `CB_ENABLED` | False | L3-D-Default | `test_l3_self_healing.py` |
| Failure Threshold | `CB_FAILURE_THRESHOLD` | 5 | L3-D-Threshold | `test_circuit_breaker.py` |
| Recovery Timeout | `CB_RECOVERY_TIMEOUT` | 60s | COLD-001 | `test_cold_start_recovery.py` |
| Success Threshold | `CB_SUCCESS_THRESHOLD` | 2 | CB-HalfOpen | `test_circuit_breaker.py` |
| Manual Override TTL | `CB_MANUAL_OVERRIDE_TTL` | 90min | OVER-003 | `test_manual_override_policy.py` |
| Half-Open Limit | `CB_HALF_OPEN_LIMIT` | 10 | CB-HalfOpen | `test_circuit_breaker.py` |

### 2.4 DLQ Policies

| Policy | Config Key | Default Value | Validated By | Test File |
|--------|------------|---------------|--------------|-----------|
| DLQ Enabled | `DLQ_ENABLED` | True | DLQ-Disabled | `test_dlq_storage_and_replay.py` |
| Retention Days | `DLQ_RETENTION_DAYS` | 30 | Unit: schema | `test_audit_record_schema.py` |
| Max Replay Attempts | `DLQ_MAX_REPLAY_ATTEMPTS` | 2 | Replay-MaxAttempts | `test_dlq_storage_and_replay.py` |
| Auto-Cleanup | `DLQ_AUTO_CLEANUP` | True | DLQ-Cleanup | `test_dlq_storage_and_replay.py` |

### 2.5 Cost Policies

| Policy | Config Key | Default Value | Validated By | Test File |
|--------|------------|---------------|--------------|-----------|
| Cost Threshold % | `COST_THRESHOLD_PERCENT` | 10 | COST-001, COST-002 | `test_cost_aware_recovery.py` |
| High-Value Override | `COST_HIGH_VALUE_THRESHOLD` | ₩100,000 | COST-003 | `test_cost_aware_recovery.py` |
| Cost Per API Call | `COST_PER_PG_CALL` | ₩50 | COST-006 | `test_cost_aware_recovery.py` |
| Cost Tracking Enabled | `COST_TRACKING_ENABLED` | True | COST-005 | `test_cost_aware_recovery.py` |

### 2.6 Multi-Tenancy Policies

| Policy | Config Key | Default Value | Validated By | Test File |
|--------|------------|---------------|--------------|-----------|
| Tenant Isolation | `TENANT_ISOLATION_ENABLED` | True | MT-001 to MT-006 | `test_multi_tenancy_isolation.py` |
| Tenant Rate Limit | `TENANT_REPLAY_RATE_LIMIT` | 10/min | MT-005 | `test_multi_tenancy_isolation.py` |
| Tenant Metrics Label | `TENANT_METRICS_LABEL` | True | OBS-008 | `test_observability_metrics.py` |

---

## 3. CI Execution Rules

### 3.1 Tier Classification

| Tier | Name | Trigger | Max Duration | Failure Action |
|------|------|---------|--------------|----------------|
| **Tier 1** | Fast | Every PR | < 2 min | Block merge |
| **Tier 2** | Integration | Merge to main | < 10 min | Block deployment |
| **Tier 3** | Chaos | Nightly / Manual | < 30 min | Alert + ticket |
| **Tier 4** | Load | Weekly / Release | < 2 hours | Alert + review |

### 3.2 Tier → Test Mapping

#### Tier 1: Fast (Every PR)

```
pytest -m "tier1" --timeout=120

Included:
├── unit/self_healing/test_backoff_policy.py
├── unit/self_healing/test_sla_timer_policy.py
├── unit/self_healing/test_retry_decision_table.py
├── unit/self_healing/test_failure_classification.py
├── unit/self_healing/test_audit_record_schema.py
├── unit/services/test_self_healing_cost_policy.py
├── unit/services/test_self_healing_tenant_policy.py
└── unit/test_self_healing_policy.py

Excluded:
- All integration tests
- All chaos tests
- All load tests
```

#### Tier 2: Integration (Merge to main)

```
pytest -m "tier2" --timeout=600

Included:
├── integration/self_healing/test_multi_tenancy_isolation.py
├── integration/self_healing/test_cost_aware_recovery.py
├── integration/self_healing/test_cascading_failures.py
├── integration/self_healing/test_cold_start_recovery.py
├── integration/self_healing/test_observability_metrics.py
├── integration/self_healing/test_audit_accountability.py
├── integration/self_healing/test_manual_override_policy.py
├── integration/self_healing/test_idempotency_integration.py
├── integration/test_l3_self_healing.py
├── integration/test_circuit_breaker.py
└── integration/test_dlq_storage_and_replay.py

Excluded:
- integration/chaos/*
- e2e/*
- load/*
```

#### Tier 3: Chaos (Nightly / Manual)

```
pytest -m "tier3_chaos" --timeout=1800

Included:
├── integration/chaos/test_partial_failure_patterns.py
├── integration/chaos/test_slow_degradation.py
├── integration/chaos/test_recovery_during_chaos.py
├── integration/chaos/test_resource_exhaustion.py
├── e2e/test_failure_recovery_cycle.py
├── e2e/test_user_invisible_flows.py
└── e2e/test_complete_dlq_lifecycle.py
```

#### Tier 4: Load (Weekly / Release)

```
pytest -m "tier4_load" --timeout=7200

Included:
├── load/test_concurrent_failures.py
├── load/test_queue_buildup.py
└── load/test_sla_under_load.py

Special Requirements:
- Dedicated CI runner with 16GB+ RAM
- Isolated database instance
- Extended Celery worker pool
```

### 3.3 High-Cost Test Exclusion Criteria

Tests are excluded from standard CI runs if they meet ANY of these criteria:

| Criterion | Threshold | Example |
|-----------|-----------|---------|
| **Execution Time** | > 5 minutes | Load tests, soak tests |
| **External Dependencies** | Real PG sandbox | Payment confirmation tests |
| **Resource Requirements** | > 4GB RAM | Queue buildup tests |
| **Flakiness Risk** | > 5% flake rate | Network simulation tests |
| **Cost** | > $0.10 per run | Real SMS/email tests |

### 3.4 Pytest Configuration

```ini
# pytest.ini additions

[pytest]
markers =
    tier1: Fast unit tests (< 2 min)
    tier2: Integration tests (< 10 min)
    tier3_chaos: Chaos engineering tests (< 30 min)
    tier4_load: Load and stress tests (< 2 hours)
    tenant_aware: Multi-tenancy tests
    cost_sensitive: Cost-aware recovery tests
    requires_redis: Tests requiring Redis
    requires_celery: Tests requiring Celery
    requires_db: Tests requiring database
    slow: Tests that take > 1 minute
    flaky: Tests with known flakiness (quarantined)

filterwarnings =
    ignore::DeprecationWarning

timeout = 300
timeout_method = signal
```

### 3.5 GitHub Actions Configuration

```yaml
# .github/workflows/self-healing-tests.yml

name: Self-Healing Test Suite

on:
  pull_request:
    paths:
      - 'shopping/services/self_healing/**'
      - 'shopping/tests/**/test_*self_healing*.py'
      - 'shopping/tests/**/test_*chaos*.py'
  push:
    branches: [main, develop]
  schedule:
    - cron: '0 2 * * *'  # Nightly at 2 AM UTC
  workflow_dispatch:
    inputs:
      tier:
        description: 'Test tier to run'
        required: true
        default: 'tier2'
        type: choice
        options:
          - tier1
          - tier2
          - tier3_chaos
          - tier4_load

jobs:
  tier1_fast:
    name: Tier 1 - Fast Tests
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4
      - name: Run Tier 1 Tests
        run: |
          pytest -m "tier1" \
            --timeout=120 \
            --tb=short \
            -q

  tier2_integration:
    name: Tier 2 - Integration Tests
    runs-on: ubuntu-latest
    timeout-minutes: 15
    needs: tier1_fast
    if: github.event_name == 'push' || github.event.inputs.tier == 'tier2'
    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_PASSWORD: postgres
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
      redis:
        image: redis:7
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    steps:
      - uses: actions/checkout@v4
      - name: Run Tier 2 Tests
        run: |
          pytest -m "tier2" \
            --timeout=600 \
            --tb=short \
            -v

  tier3_chaos:
    name: Tier 3 - Chaos Tests
    runs-on: ubuntu-latest
    timeout-minutes: 45
    if: github.event_name == 'schedule' || github.event.inputs.tier == 'tier3_chaos'
    steps:
      - uses: actions/checkout@v4
      - name: Run Tier 3 Tests
        run: |
          pytest -m "tier3_chaos" \
            --timeout=1800 \
            --tb=long \
            -v
      - name: Upload Chaos Test Report
        uses: actions/upload-artifact@v4
        if: failure()
        with:
          name: chaos-test-report
          path: reports/chaos/

  tier4_load:
    name: Tier 4 - Load Tests
    runs-on: ubuntu-latest-16core  # Larger runner
    timeout-minutes: 150
    if: github.event.inputs.tier == 'tier4_load'
    steps:
      - uses: actions/checkout@v4
      - name: Run Tier 4 Tests
        run: |
          pytest -m "tier4_load" \
            --timeout=7200 \
            --tb=long \
            -v
      - name: Upload Load Test Report
        uses: actions/upload-artifact@v4
        with:
          name: load-test-report
          path: reports/load/
```

---

## 4. Test Dependencies Matrix

### 4.1 Infrastructure Dependencies

| Test Domain | PostgreSQL | Redis | Celery | External APIs |
|-------------|------------|-------|--------|---------------|
| Unit Tests | ❌ | ❌ | ❌ | ❌ |
| Multi-Tenancy | ✅ | ❌ | ❌ | ❌ |
| Cost-Aware | ✅ | ❌ | ❌ | Mock |
| Observability | ✅ | ✅ | ❌ | ❌ |
| Cascading | ✅ | ✅ | ✅ | Mock |
| Cold Start | ✅ | ✅ | ✅ | ❌ |
| Audit | ✅ | ❌ | ❌ | ❌ |
| Chaos | ✅ | ✅ | ✅ | Mock |
| E2E | ✅ | ✅ | ✅ | Mock |
| Load | ✅ | ✅ | ✅ | Mock |

### 4.2 Test Data Dependencies

| Test Domain | User | Order | Payment | Tenant | DLQ Entry |
|-------------|------|-------|---------|--------|-----------|
| Multi-Tenancy | ✅ | ❌ | ❌ | ✅ | ❌ |
| Cost-Aware | ✅ | ✅ | ✅ | ❌ | ❌ |
| Observability | ✅ | ✅ | ✅ | ✅ | ✅ |
| Cascading | ✅ | ✅ | ✅ | ❌ | ❌ |
| Cold Start | ✅ | ❌ | ❌ | ❌ | ✅ |
| Audit | ✅ | ✅ | ✅ | ❌ | ✅ |
| Chaos | ✅ | ✅ | ✅ | ❌ | ❌ |

---

## 5. Failure Message Guidelines

All test failures must provide actionable, policy-aware messages.

### 5.1 Message Format

```python
# BAD: Generic assertion
assert result == expected

# GOOD: Policy-aware message
assert result["action"] == "moved_to_dlq", (
    f"Policy Violation: Expected DLQ routing when cost ({cost}) exceeds "
    f"threshold ({threshold}). Got action='{result['action']}'. "
    f"Check COST_THRESHOLD_PERCENT configuration."
)
```

### 5.2 Required Message Components

| Component | Purpose | Example |
|-----------|---------|---------|
| **Policy Name** | Which policy was violated | "Cost Threshold Policy" |
| **Expected Value** | What should have happened | "Expected DLQ routing" |
| **Actual Value** | What actually happened | "Got action='retry_scheduled'" |
| **Context** | Relevant parameters | "cost=1500, threshold=1000" |
| **Remediation Hint** | How to investigate | "Check COST_THRESHOLD_PERCENT" |

### 5.3 Example Messages by Domain

```python
# Multi-Tenancy
f"Tenant Isolation Violation: Tenant {tenant_a.id}'s CB state affected "
f"Tenant {tenant_b.id}. Expected isolation per SOC 2 CC6.1."

# Cost-Aware
f"Cost Policy Violation: Retry attempted when cumulative cost ({cost}) "
f"exceeded {threshold_percent}% of transaction value ({tx_value}). "
f"Expected early DLQ routing per cost governance policy."

# Observability
f"Metric Emission Failure: Expected {metric_name} to increment by {expected}, "
f"but observed {actual}. Check MetricsCollector integration."

# Audit
f"Audit Trail Incomplete: Required field '{field}' missing from audit entry "
f"for action '{action_type}'. This is a compliance violation (NIST AU-3)."

# SLA
f"SLA Timer Precision Failure: Timeout detected at {elapsed}s, "
f"expected at {sla_timeout}s (±1s tolerance). Timer drift detected."
```

---

## 6. Assumptions & Limitations

### 6.1 Documented Assumptions

| ID | Assumption | Impact if False | Mitigation |
|----|------------|-----------------|------------|
| A-001 | Multi-tenancy uses `tenant_id` field on models | MT tests require schema change | Schema migration first |
| A-002 | Cost tracking is mock-based until real PG API | Cost tests not production-accurate | Validate with PG sandbox post-integration |
| A-003 | Observability backend is abstracted via Protocol | Backend swap requires test updates | Use MockMetricsCollector for all tests |
| A-004 | `FailedOperation` model supports tenant isolation | Tenant tests may fail | Add tenant_id field if missing |
| A-005 | Circuit breaker can scope by (service, tenant) | Per-tenant CB not possible | Extend CB model |
| A-006 | Celery tasks are synchronous in tests | Race conditions hidden | Run async tests in Tier 3 |

### 6.2 Known Limitations

| ID | Limitation | Impact | Workaround |
|----|------------|--------|------------|
| L-001 | Real PG cost data unavailable | Cost tests use mock values | Update mocks when real data available |
| L-002 | Time-sensitive tests have ±1s variance | Occasional flakes in CI | Use tolerance assertions |
| L-003 | Load tests require dedicated CI runners | Cannot run on standard runners | Manual trigger only |
| L-004 | Network partition hard to simulate | Some chaos scenarios incomplete | Use timeout injection instead |
| L-005 | Multi-tenancy may require DB schema changes | Tests fail until migration | Phase 1 includes migration |

---

## 7. Review & Approval

### 7.1 Document Review Checklist

| Item | Reviewer | Status |
|------|----------|--------|
| Risk matrix completeness | Security Team | ⬜ Pending |
| Policy coverage accuracy | Engineering Lead | ⬜ Pending |
| CI tier appropriateness | DevOps | ⬜ Pending |
| Compliance alignment | Legal/Compliance | ⬜ Pending |
| Test case sufficiency | QA Lead | ⬜ Pending |

### 7.2 Change Log

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2025-12-08 | AI Test Engineer | Initial release |

---

## 8. Quick Reference Card

### Test Command Cheat Sheet

```bash
# Run all Tier 1 tests (fast, for PR)
pytest -m "tier1" -q

# Run all Tier 2 tests (integration)
pytest -m "tier2" -v

# Run specific domain tests
pytest -m "tenant_aware" -v
pytest -m "cost_sensitive" -v

# Run chaos tests (manual/nightly)
pytest -m "tier3_chaos" -v

# Run with coverage
pytest -m "tier2" --cov=shopping.services.self_healing --cov-report=html

# Run single test file
pytest shopping/tests/integration/self_healing/test_multi_tenancy_isolation.py -v

# Run with detailed failure output
pytest -m "tier2" --tb=long -v
```

### Risk Priority Order

```
🔴 Critical (Block Release):
   R-001 Cross-tenant leakage
   R-002 Unbounded costs
   R-011 Duplicate payments
   R-014 DLQ data loss

🟡 High (Block Deployment):
   R-003 Silent metric loss
   R-004 Cascading failure
   R-006 Unaccountable decisions

🟢 Medium (Alert & Track):
   R-005, R-007, R-008, R-009, R-010
```

---

**End of Document**
