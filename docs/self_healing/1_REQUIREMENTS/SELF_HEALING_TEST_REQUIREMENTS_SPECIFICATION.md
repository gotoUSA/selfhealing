# L3 Self-Healing Reliability Layer — Test Requirements Specification

> **Version**: 1.0
> **Created**: 2025-12-09
> **Status**: Active
> **Source**: Architecture & Operations Documentation

---

## Table of Contents

1. [Failure Classification Tests](#1-failure-classification-tests)
2. [Idempotency Guarantee Tests](#2-idempotency-guarantee-tests)
3. [Retry Strategy & Backoff Tests](#3-retry-strategy--backoff-tests)
4. [DLQ Processing & Replay Tests](#4-dlq-processing--replay-tests)
5. [Circuit Breaker Behavior Tests](#5-circuit-breaker-behavior-tests)
6. [SLA & Escalation Tests](#6-sla--escalation-tests)
7. [Notification Routing Tests](#7-notification-routing-tests)
8. [Observability & Metrics Tests](#8-observability--metrics-tests)
9. [Forensic Context Completeness Tests](#9-forensic-context-completeness-tests)
10. [Domain-Specific Test Requirements](#10-domain-specific-test-requirements)

---

## 1. Failure Classification Tests

### Purpose

Ensure each failure type is routed to the correct policy handler with appropriate actions.

### Failure Categories

| Failure Category | Expected Behavior | Required Tests |
|------------------|-------------------|----------------|
| **Validation Failure** | No retry, No DLQ, Immediate user-facing response | Unit |
| **Permanent Failure** | No retry, Archived or Logged | Unit |
| **Technical Retryable Failure** | Retry → DLQ after max attempts | Unit + Integration |
| **Security Violation** | Never retry, Never DLQ, Block + SecurityIncident | Unit + Integration |

### Test Goals

| Goal | Validation |
|------|------------|
| Failure → correct classification | Error type maps to correct category |
| Correct action: retry / skip / block | Handler takes expected action |
| **Forbidden**: retrying permanent or security failures | No retry attempt made |
| **Forbidden**: storing security events in DLQ | `FailedOperation` table has no security records |

### Required Test Cases

```
TEST-FC-001: test_validation_failure_returns_immediate_error
TEST-FC-002: test_validation_failure_not_stored_in_dlq
TEST-FC-003: test_permanent_failure_no_retry_attempt
TEST-FC-004: test_permanent_failure_logged_for_audit
TEST-FC-005: test_technical_failure_triggers_retry
TEST-FC-006: test_technical_failure_goes_to_dlq_after_max_retries
TEST-FC-007: test_security_violation_never_retried
TEST-FC-008: test_security_violation_not_stored_in_dlq
TEST-FC-009: test_security_violation_creates_security_incident
TEST-FC-010: test_security_violation_blocks_further_processing
```

---

## 2. Idempotency Guarantee Tests

### Purpose

Ensure that retry operations are safe and do not cause duplicate side effects.

### Test Scenarios

| Scenario | Expected Behavior |
|----------|-------------------|
| Same operation retried | Must NOT cause side effect |
| Duplicate PG call | Must return previous result |
| Webhook duplicate event | Must skip processing |
| Missing idempotency key | Retry blocked |

### Test Goals

| Goal | Validation |
|------|------------|
| Duplicate payment request returns existing result | Same `payment_id` returned |
| Retry without idempotency is denied | Error raised, no processing |
| Idempotency persists after system restart | DB-backed, not in-memory only |

### Required Test Cases

```
TEST-ID-001: test_duplicate_payment_request_returns_existing_payment
TEST-ID-002: test_duplicate_webhook_event_is_skipped
TEST-ID-003: test_retry_without_idempotency_key_is_denied
TEST-ID-004: test_idempotency_key_persists_in_database
TEST-ID-005: test_concurrent_same_key_creates_single_record
TEST-ID-006: test_idempotency_key_format_by_domain
TEST-ID-007: test_point_operation_idempotency
TEST-ID-008: test_inventory_operation_idempotency
```

---

## 3. Retry Strategy & Backoff Tests

### Purpose

Validate exponential backoff, jitter distribution, and retry count persistence.

### Test Conditions

| Condition | Expected Behavior |
|-----------|-------------------|
| 1st failure | Retry in ~4s ± jitter |
| 2nd failure | Retry in ~16s ± jitter |
| Exceeds MAX | DLQ entry created |
| Jitter distribution | Non-identical retry timestamps |

### Test Goals

| Goal | Validation |
|------|------------|
| Exponential growth validated | 4 → 16 → 64 sequence |
| Jitter statistically distributed | Multiple runs show variance |
| Retry count persists across task restart | DB-stored, not task-local |
| MaxAttempts leads to DLQ | Entry created after exhaustion |

### Required Test Cases

```
TEST-RT-001: test_backoff_sequence_is_exponential
TEST-RT-002: test_backoff_respects_max_delay
TEST-RT-003: test_jitter_produces_varied_delays
TEST-RT-004: test_jitter_distribution_is_random
TEST-RT-005: test_max_attempts_triggers_dlq_entry
TEST-RT-006: test_retry_count_persists_after_worker_restart
TEST-RT-007: test_retry_count_increments_correctly
TEST-RT-008: test_non_retryable_exception_skips_retry
```

---

## 4. DLQ Processing & Replay Tests

### Purpose

Validate Dead Letter Queue storage, retrieval, and replay operations.

### Test Scenarios

| Scenario | Expected |
|----------|----------|
| DLQ created after max retry | status = `pending` |
| Manual replay success | status = `resolved` |
| Manual replay fail < 2 | Back to `pending` |
| Manual replay fail >= 2 | status = `rejected` |
| Replay after circuit close | `resolved` OR `requires_review` |
| Replay not allowed for security | Blocked |

### Additional Assertions

| Assertion | Description |
|-----------|-------------|
| Forensic metadata recorded | All context fields populated |
| Snapshot sufficient to reconstruct | Order, payment, user data present |
| DLQ respects retention & auto archive | Soft-delete after retention period |

### Required Test Cases

```
TEST-DLQ-001: test_dlq_entry_created_after_max_retries
TEST-DLQ-002: test_dlq_entry_contains_forensic_metadata
TEST-DLQ-003: test_manual_replay_success_marks_resolved
TEST-DLQ-004: test_manual_replay_failure_reverts_to_pending
TEST-DLQ-005: test_max_replay_attempts_marks_rejected
TEST-DLQ-006: test_replay_after_circuit_close_escalates_if_failed
TEST-DLQ-007: test_security_violation_replay_blocked
TEST-DLQ-008: test_dlq_auto_archive_after_retention
TEST-DLQ-009: test_archived_entries_excluded_from_pending_queries
TEST-DLQ-010: test_snapshot_data_enables_operation_reconstruction
```

---

## 5. Circuit Breaker Behavior Tests

### Purpose

Validate circuit breaker state management and operator controls.

### Test Scenarios (from Architecture CB Policy)

| Scenario | Expected |
|----------|----------|
| `force_open` | Blocks new operations |
| `force_close` | Allows operations |
| TTL expiration | Auto-close + notification |
| Replay after `force_close` failure | Escalated (not reopen circuit) |

### Test Goals

| Goal | Validation |
|------|------------|
| Runnable even across distributed workers | State persists in DB |
| CB state stored & resilient | Survives service restart |
| No silent reopen side effects | Failed replay doesn't reopen |

### Required Test Cases

```
TEST-CB-001: test_force_open_blocks_new_operations
TEST-CB-002: test_force_close_allows_operations
TEST-CB-003: test_automatic_open_after_failure_threshold
TEST-CB-004: test_half_open_transition_after_timeout
TEST-CB-005: test_ttl_expiration_auto_closes_circuit
TEST-CB-006: test_ttl_expiration_sends_notification
TEST-CB-007: test_replay_failure_does_not_reopen_circuit
TEST-CB-008: test_circuit_state_persists_across_restart
TEST-CB-009: test_multiple_circuits_operate_independently
TEST-CB-010: test_operator_action_recorded_with_user_id
```

---

## 6. SLA & Escalation Tests

### Purpose

Validate SLA threshold detection and domain-specific escalation rules.

### SLA Thresholds (from Operations – Recovery SLA)

| SLA Type | Expected Test |
|----------|---------------|
| Payment exceeded SLA | Escalated |
| Inventory SLA violated | Escalated |
| Notification domain SLA | No escalation (low priority) |

### Test Goals

| Goal | Validation |
|------|------------|
| SLA detection | Entries older than threshold detected |
| Escalation routing by domain | Correct channels notified |
| Archived after SLA expiry | Status transitions correctly |

### Required Test Cases

```
TEST-SLA-001: test_payment_sla_threshold_is_1_hour
TEST-SLA-002: test_point_sla_threshold_is_4_hours
TEST-SLA-003: test_inventory_sla_threshold_is_2_hours
TEST-SLA-004: test_webhook_sla_threshold_is_8_hours
TEST-SLA-005: test_notification_sla_threshold_is_24_hours
TEST-SLA-006: test_sla_breach_detected_for_old_entries
TEST-SLA-007: test_sla_breach_triggers_escalation
TEST-SLA-008: test_notification_domain_no_escalation
TEST-SLA-009: test_requires_review_after_repeated_sla_breach
```

---

## 7. Notification Routing Tests

### Purpose

Validate multi-channel notification delivery based on severity.

### Channel Routing

| Severity | Channel |
|----------|---------|
| CRITICAL | Slack + Email + SMS + Pager |
| HIGH | Slack + Email |
| MEDIUM | Slack |
| LOW | Email digest |

### Test Goals

| Goal | Validation |
|------|------------|
| Differentiated channels delivered | Correct channels called per severity |
| Graceful fallback when channel fails | No exception propagation |
| Truncation for Slack API size limit | Messages ≤ 3000 chars |

### Required Test Cases

```
TEST-NTF-001: test_critical_severity_sends_all_channels
TEST-NTF-002: test_high_severity_sends_slack_and_email
TEST-NTF-003: test_medium_severity_sends_slack_only
TEST-NTF-004: test_low_severity_sends_email_digest
TEST-NTF-005: test_slack_message_truncated_at_limit
TEST-NTF-006: test_channel_failure_does_not_block_others
TEST-NTF-007: test_notification_disabled_returns_empty_result
TEST-NTF-008: test_dry_run_mode_logs_without_sending
```

---

## 8. Observability & Metrics Tests

### Purpose

Validate Prometheus metrics recording and alerting rule correctness.

### Metrics to Test

| Metric | Test |
|--------|------|
| `dlq.pending` | Increments on failure |
| `retry.success_rate` | Updates correctly |
| `circuit.state` | Reflects status |
| `recovery.time` | Recorded accurately |

### Test Goals

| Goal | Validation |
|------|------------|
| Prometheus counters and gauges updated | Values change on events |
| Replaying reduces pending metric | Gauge decreases |
| Circuit open increments metric | State change tracked |

### Required Test Cases

```
TEST-OBS-001: test_dlq_pending_increments_on_failure
TEST-OBS-002: test_dlq_pending_decrements_on_replay
TEST-OBS-003: test_retry_success_rate_updates
TEST-OBS-004: test_circuit_breaker_state_metric_changes
TEST-OBS-005: test_recovery_time_histogram_recorded
TEST-OBS-006: test_sla_breach_counter_increments
TEST-OBS-007: test_all_domains_have_metrics_labels
```

---

## 9. Forensic Context Completeness Tests

### Purpose

Validate that captured metadata enables failure investigation without runtime logs.

### Required Forensic Data

| Data | Example |
|------|---------|
| Attempt history | `[{attempt: 1, error: "TIMEOUT", backoff: 4}, ...]` |
| Snapshots | before/after state |
| Request & response | JSON payloads |
| Task execution | worker, task_id, queue |
| External system ID | PG reference ID |

### Test Goals

| Goal | Validation |
|------|------------|
| Reproducing failure does NOT require runtime logs | All context in DLQ entry |
| DLQ entry contains sufficient info for human review | Snapshot + metadata complete |

### Required Test Cases

```
TEST-FOR-001: test_forensic_context_captures_state_before
TEST-FOR-002: test_forensic_context_captures_state_after
TEST-FOR-003: test_retry_history_recorded_in_metadata
TEST-FOR-004: test_request_data_stored_in_dlq
TEST-FOR-005: test_response_data_stored_in_dlq
TEST-FOR-006: test_task_execution_context_captured
TEST-FOR-007: test_external_system_reference_recorded
TEST-FOR-008: test_snapshot_enables_operation_replay
TEST-FOR-009: test_forensic_builder_fluent_api
TEST-FOR-010: test_sensitive_data_redacted_in_forensic
```

---

## 10. Domain-Specific Test Requirements

### Format

For each domain, tests should validate:

| Field | Description |
|-------|-------------|
| **Preconditions** | Required state before failure |
| **Failure injection mechanism** | How to simulate the failure |
| **Expected routing** | Retry / DLQ / Block |
| **Expected final state** | Status after processing |
| **SLA deadline** | Domain-specific threshold |
| **Required audit fields** | Forensic data requirements |
| **Forbidden behaviors** | Actions that must NOT occur |

### Payment Domain

```yaml
domain: payment
preconditions:
  - Order exists with status "confirmed"
  - Payment in "in_progress" status
failure_types:
  - PG_TIMEOUT: retry with backoff → DLQ after 3 attempts
  - INVALID_CARD: permanent fail, no retry
  - AMOUNT_MISMATCH: human approval required
  - SIGNATURE_INVALID: security incident, never retry
expected_routing:
  PG_TIMEOUT: retry → dlq
  INVALID_CARD: immediate_fail
  AMOUNT_MISMATCH: human_approval
  SIGNATURE_INVALID: security_incident
sla_deadline: 1 hour
required_audit_fields:
  - order_id
  - payment_key
  - amount
  - pg_response
forbidden_behaviors:
  - Double charge on retry
  - Retry after payment completed
  - Store security violation in DLQ
```

### Point Domain

```yaml
domain: point
preconditions:
  - User exists with points balance
  - Order linked to point operation
failure_types:
  - DB_SAVE_AFTER_PG_SUCCESS: critical retry
  - INSUFFICIENT_POINTS: validation fail
  - DUPLICATE_DEDUCTION: human approval + rollback
  - NEGATIVE_BALANCE: DLQ + critical alert
expected_routing:
  DB_SAVE_AFTER_PG_SUCCESS: retry_critical
  INSUFFICIENT_POINTS: validation_fail
  DUPLICATE_DEDUCTION: human_approval
  NEGATIVE_BALANCE: dlq_alert
sla_deadline: 4 hours
required_audit_fields:
  - user_id
  - point_amount
  - operation_type
  - balance_before
  - balance_after
forbidden_behaviors:
  - Double point deduction
  - Negative balance allowed
```

### Inventory Domain

```yaml
domain: inventory
preconditions:
  - Product exists with stock > 0
  - Order item linked to product
failure_types:
  - DB_LOCK_CONFLICT: short retry
  - STOCK_NEGATIVE: DLQ + alert
  - STOCK_MISMATCH: human review
expected_routing:
  DB_LOCK_CONFLICT: retry_short
  STOCK_NEGATIVE: dlq_alert
  STOCK_MISMATCH: human_review
sla_deadline: 2 hours
required_audit_fields:
  - product_id
  - quantity
  - stock_before
  - stock_after
forbidden_behaviors:
  - Overselling (stock < 0)
  - Stock not restored on order cancel
```

### Webhook Domain

```yaml
domain: webhook
preconditions:
  - Webhook endpoint accessible
  - Valid signature expected
failure_types:
  - SIGNATURE_INVALID: security incident
  - PAYLOAD_MALFORMED: permanent fail
  - DUPLICATE_EVENT: skip (idempotent)
  - ORDER_NOT_FOUND: DLQ + alert
expected_routing:
  SIGNATURE_INVALID: security_incident
  PAYLOAD_MALFORMED: permanent_fail
  DUPLICATE_EVENT: skip
  ORDER_NOT_FOUND: dlq_alert
sla_deadline: 8 hours
required_audit_fields:
  - event_id
  - event_type
  - payload_hash
  - source_ip
forbidden_behaviors:
  - Process invalid signature
  - Duplicate event processing
```

### Notification Domain

```yaml
domain: notification
preconditions:
  - User has valid email/phone
  - Template exists
failure_types:
  - SMTP_TIMEOUT: retry with long backoff
  - FCM_ERROR: retry with backoff
  - INVALID_EMAIL: validation fail
  - UNSUBSCRIBED: skip + log
expected_routing:
  SMTP_TIMEOUT: retry_long
  FCM_ERROR: retry
  INVALID_EMAIL: validation_fail
  UNSUBSCRIBED: skip_log
sla_deadline: 24 hours (lowest priority)
required_audit_fields:
  - user_id
  - notification_type
  - channel
  - template_id
forbidden_behaviors:
  - Send to unsubscribed user
  - No escalation for notification failures
```

---

## Document Purpose

| This Document IS | This Document is NOT |
|------------------|----------------------|
| Test coverage filter | Full test implementation |
| Gap detection list | Backend specification |
| AI refinement input | Test traceability matrix |
| Regression protection spec | Non-actionable theory |

---

## Related Documents

- [L3 Self-Healing Architecture](../L3_SELF_HEALING_ARCHITECTURE.md)
- [L3 Self-Healing Operations](../L3_SELF_HEALING_OPERATIONS.md)
- [Test Coverage Analysis](./L3_TEST_COVERAGE_ANALYSIS.md)
- [Test Gap Report](./L3_TEST_GAP_REPORT.md)
