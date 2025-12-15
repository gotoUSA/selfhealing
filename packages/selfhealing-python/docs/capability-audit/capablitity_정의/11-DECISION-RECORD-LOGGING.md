````markdown
# Decision Record Logging

**Document Version:** 1.0
**Created:** 2025-12-15
**Status:** Active

---

## Overview

This document defines the **Decision Record Logging** capability for the Self-Healing Reliability Library. Decision Record Logging provides **observability-only** augmentation to existing decision boundaries without modifying any behavior, thresholds, or control flow.

### Core Principle

> **This system does NOT make decisions. It ONLY records why an intervention was NOT allowed or WAS allowed, based on already-existing logic.**

---

## Purpose

Decision Record Logging addresses the need for **explainability** in self-healing systems:

| Need | Solution |
|------|----------|
| Post-incident analysis | Complete record of why system did/didn't intervene |
| Compliance auditing | Timestamped decisions with policy references |
| Debugging | Trace decision paths without enabling verbose logging |
| Capacity planning | Identify patterns in intervention frequency |

---

## Design Constraints (Non-Negotiable)

### What Decision Record Logging DOES

| Action | Description |
|--------|-------------|
| Attach labels (Reason Codes) to existing outcomes | Labels reflect existing logic, not new logic |
| Add logging at specific decision boundaries | Log ONLY at state transitions |
| Record policy version at decision time | Snapshot of active policy |
| Emit sparse, intentional logs | No per-request or per-metric logging |

### What Decision Record Logging DOES NOT DO

| Prohibited Action | Rationale |
|-------------------|-----------|
| Add new decision logic | Logging must be passive observation |
| Modify existing if/else conditions | Behavior must remain identical |
| Change thresholds, constants, or policy values | Configuration is immutable by logging |
| Refactor behavior or execution order | No structural changes |
| Introduce heuristics, probabilities, or AI-based logic | No intelligent behavior |
| Add automatic actions or new interventions | Logging cannot trigger actions |

---

## Core Signals (Conceptual Labels, Not Numeric)

Decision Record Logging uses conceptual labels to describe system state. These are NOT numeric values and do NOT introduce new calculations.

| Signal | Values | Description |
|--------|--------|-------------|
| `pressure` | `LOW` \| `HIGH` | Indicates whether the system is under stress |
| `stability` | `OK` \| `DEGRADED` | Indicates whether system health is normal |
| `constraints` | `ALLOW` \| `DENY` | Indicates whether policy allows intervention |

These labels are derived from **existing conditions** in the code, not computed independently.

---

## Reason Codes (Fixed, Non-Extensible)

Only the following reason codes are permitted. These codes are **labels**, not logic.

### Code Reference Table

| Reason Code | When to Use | Description |
|-------------|-------------|-------------|
| `THRESHOLD_NOT_MET` | Pressure exists, but intervention thresholds are not met | The system detected potential issues but configured thresholds were not exceeded |
| `STABILITY_OK_NO_INTERVENTION` | System stability is still OK, so intervention is unnecessary | The system is operating normally and no action is required |
| `POLICY_CONSTRAINT_ACTIVE` | Intervention is technically possible but denied by policy or organizational constraints | Manual override, maintenance mode, or policy restriction prevents intervention |
| `INTERVENTION_ALLOWED` | All conditions satisfied; intervention is allowed | All checks passed and the system will proceed with the intervention |

### Code Mapping Examples

| Existing Condition | Reason Code |
|-------------------|-------------|
| `failure_count < failure_threshold` | `THRESHOLD_NOT_MET` |
| `circuit_state == "closed"` | `STABILITY_OK_NO_INTERVENTION` |
| `manually_controlled == True` | `POLICY_CONSTRAINT_ACTIVE` |
| `circuit_state == "open" and should_open` | `INTERVENTION_ALLOWED` |
| `rate_limit_count < cascade_threshold` | `THRESHOLD_NOT_MET` |
| `retry_count < max_retries` | `THRESHOLD_NOT_MET` |
| `is_enabled == False` | `POLICY_CONSTRAINT_ACTIVE` |

---

## Event Types

### ENTER_PRE_DECISION_ZONE

Logged when the system enters a state where an intervention decision may be evaluated.

```python
{
    "event": "ENTER_PRE_DECISION_ZONE",
    "service_name": "toss_payment",
    "timestamp": "2025-12-15T10:30:00.000Z",
    "policy_version": "v1.2.0"
}
```

### INTERVENTION_EVALUATED

Logged when a decision boundary is reached and the system evaluates whether intervention is allowed.

```python
# Intervention NOT allowed
{
    "event": "INTERVENTION_EVALUATED",
    "allowed": false,
    "reason": "THRESHOLD_NOT_MET",
    "service_name": "toss_payment",
    "timestamp": "2025-12-15T10:30:01.000Z",
    "policy_version": "v1.2.0"
}

# Intervention allowed
{
    "event": "INTERVENTION_EVALUATED",
    "allowed": true,
    "reason": "INTERVENTION_ALLOWED",
    "service_name": "toss_payment",
    "timestamp": "2025-12-15T10:30:02.000Z",
    "policy_version": "v1.2.0"
}
```

### EXIT_PRE_DECISION_ZONE

Logged when the system exits a risk state and returns to normal without any intervention.

```python
{
    "event": "EXIT_PRE_DECISION_ZONE",
    "service_name": "toss_payment",
    "timestamp": "2025-12-15T10:30:45.000Z",
    "policy_version": "v1.2.0"
}
```

---

## Decision Boundaries

Decision Record Logging applies ONLY at the following existing evaluation points:

### 1. Circuit Breaker Checks

| Check Point | When Logged | Reason Code |
|-------------|-------------|-------------|
| `should_allow()` returns `True` with circuit closed | When circuit is healthy | `STABILITY_OK_NO_INTERVENTION` |
| `record_failure()` does not trigger open | When below threshold | `THRESHOLD_NOT_MET` |
| `record_failure()` triggers open | Immediately before opening | `INTERVENTION_ALLOWED` |
| `manually_controlled` prevents auto-action | When operator override active | `POLICY_CONSTRAINT_ACTIVE` |

### 2. Retry / Backoff Checks

| Check Point | When Logged | Reason Code |
|-------------|-------------|-------------|
| Retry attempt initiated | Before each retry | `INTERVENTION_ALLOWED` |
| Max retries not reached but returning | When retry delayed | `THRESHOLD_NOT_MET` |
| Max retries reached, routing to DLQ | Before DLQ routing | `INTERVENTION_ALLOWED` |

### 3. Rate Limit Checks

| Check Point | When Logged | Reason Code |
|-------------|-------------|-------------|
| Rate limit cascade not detected | Below threshold | `THRESHOLD_NOT_MET` |
| Rate limit cascade triggers circuit open | Before auto-open | `INTERVENTION_ALLOWED` |
| Self-DDoS protection suggests backoff | When threshold exceeded | `INTERVENTION_ALLOWED` |

### 4. Policy / Toggle Checks

| Check Point | When Logged | Reason Code |
|-------------|-------------|-------------|
| Circuit breaker disabled globally | When `is_enabled == False` | `POLICY_CONSTRAINT_ACTIVE` |
| Feature toggle prevents action | When toggle is off | `POLICY_CONSTRAINT_ACTIVE` |
| Maintenance window active | When maintenance mode | `POLICY_CONSTRAINT_ACTIVE` |

---

## Logging Constraints

### When to Log

| Condition | Log? | Event Type |
|-----------|------|------------|
| State transition: NORMAL → PRE-DECISION | ✅ | `ENTER_PRE_DECISION_ZONE` |
| State transition: PRE-DECISION → NORMAL (no intervention) | ✅ | `EXIT_PRE_DECISION_ZONE` |
| Decision boundary reached | ✅ | `INTERVENTION_EVALUATED` |
| Per-request processing | ❌ | — |
| Every metric change | ❌ | — |
| Internal calculation steps | ❌ | — |

### Log Content Requirements

Each decision log MUST include:

| Field | Required | Description |
|-------|----------|-------------|
| `event` | ✅ | Event type (ENTER / INTERVENTION_EVALUATED / EXIT) |
| `allowed` | ✅ (for INTERVENTION_EVALUATED) | `true` or `false` |
| `reason` | ✅ (for INTERVENTION_EVALUATED) | Reason code from fixed set |
| `service_name` | ✅ | Affected service identifier |
| `policy_version` | ⚠️ (if available) | Policy snapshot reference |
| `timestamp` | ✅ | ISO 8601 timestamp |

Each decision log MUST NOT include:

| Excluded Content | Rationale |
|-----------------|-----------|
| Internal calculation steps | Implementation detail |
| Raw metric streams | Too verbose |
| Implementation details | Security/abstraction |
| Request payloads | Privacy/performance |

---

## Implementation Guidelines

### Adding Decision Record Logging to Existing Code

**Rule 1: Log BEFORE intervention, never after**

```python
# CORRECT: Log before intervention
if failure_count >= threshold:
    _log_decision(allowed=True, reason="INTERVENTION_ALLOWED")
    self._open_circuit()  # Existing intervention

# INCORRECT: Log after intervention
if failure_count >= threshold:
    self._open_circuit()
    _log_decision(...)  # Too late, intervention already happened
```

**Rule 2: Log NOT allowed only at decision boundaries**

```python
# CORRECT: Log at the decision boundary
if failure_count < threshold:
    _log_decision(allowed=False, reason="THRESHOLD_NOT_MET")
    return  # Existing early return

# INCORRECT: Log inside every condition check
for condition in conditions:
    _log_decision(...)  # Too verbose
```

**Rule 3: Never modify existing conditions**

```python
# CORRECT: Add logging without changing condition
if manually_controlled:
    _log_decision(allowed=False, reason="POLICY_CONSTRAINT_ACTIVE")
    return  # Existing return

# INCORRECT: Modify the condition
if manually_controlled or new_condition:  # FORBIDDEN
    ...
```

---

## Integration with Existing Observability

### Relationship with Prometheus Metrics

| Prometheus Metrics | Decision Record Logging |
|-------------------|------------------------|
| Quantitative (counts, gauges) | Qualitative (reasons, context) |
| Time-series aggregation | Event-based records |
| Alerting thresholds | Audit/forensics |
| High cardinality concerns | Sparse, intentional events |

**Decision Record Logging complements but does NOT replace Prometheus metrics.**

### Relationship with OpenTelemetry Adapter

| OpenTelemetry Adapter | Decision Record Logging |
|----------------------|------------------------|
| Trace propagation | Decision boundary records |
| Span attributes | Reason codes |
| External APM export | Local structured logging |

**Decision Record Logs can be exported via OpenTelemetry if the adapter is enabled.**

---

## Verification Checklist

After implementing Decision Record Logging, verify:

| Check | Pass Criteria |
|-------|---------------|
| Behavior identical | All tests pass unchanged |
| No new thresholds | No new numeric constants added |
| No new conditions | No new if/else logic added |
| Sparse logging | Logs only on state transitions |
| Fixed reason codes | Only 4 permitted codes used |
| Policy version included | When available, policy is captured |

---

## Code References

| Component | Location | Status |
|-----------|----------|--------|
| Decision Logger | `core/decision_logger.py` | ✅ Phase 1 Implemented |
| Decision Logger Tests | `tests/core/test_decision_logger.py` | ✅ 18 tests passing |
| Circuit Breaker Integration | `services/circuit_breaker/service.py` | ⏳ Pending |
| Retry Handler Integration | `services/retry_handler.py` | ⏳ Pending |
| Protection Mixin Integration | `services/circuit_breaker/protection.py` | ⏳ Pending |

### Phase 1 Implementation (Sink-Only)

The `core/decision_logger.py` file provides structured logging at decision boundaries.

**Logger:** `selfhealing.decision_record`

**Logged Fields (Fixed Set):**
| Field | Event Types | Description |
|-------|-------------|-------------|
| `event` | All | Event type string |
| `allowed` | INTERVENTION_EVALUATED only | Boolean |
| `reason` | INTERVENTION_EVALUATED only | Reason code string |
| `service_name` | All | Service identifier |
| `policy_version` | All | Policy version (nullable) |
| `timestamp` | All | ISO 8601 UTC timestamp |

**Functions:**
- `log_enter_pre_decision_zone(service_name, policy_version)`
- `log_intervention_evaluated(service_name, allowed, reason, policy_version)`
- `log_exit_pre_decision_zone(service_name, policy_version)`

**Class:**
- `DecisionLogger`: Class-based interface delegating to module functions

**Example Output:**
```json
{"event": "INTERVENTION_EVALUATED", "allowed": false, "reason": "THRESHOLD_NOT_MET", "service_name": "toss_payment", "policy_version": null, "timestamp": "2025-12-15T10:30:01.000Z"}
```

---

## See Also

- [06-OBSERVABILITY-FORENSICS.md](06-OBSERVABILITY-FORENSICS.md) — Prometheus Metrics and Forensic Context
- [07-INTEGRATION-ADAPTERS.md](../interface/07-INTEGRATION-ADAPTERS.md) — Integration with external systems
- [10-OPENTELEMETRY-ADAPTER.md](10-OPENTELEMETRY-ADAPTER.md) — Optional OpenTelemetry export

````
