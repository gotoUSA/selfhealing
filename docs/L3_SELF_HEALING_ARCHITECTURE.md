# L3 Self-Healing Reliability Layer — Architecture

> **Version**: 1.1
> **Last Updated**: 2025-12-08
> **Status**: Production Ready

---

## Table of Contents

1. [Overview](#1-overview)
2. [Reliability Layers Context](#2-reliability-layers-context)
3. [Core Philosophy](#3-core-philosophy)
4. [Responsibility Split](#4-responsibility-split)
5. [Architecture Diagram](#5-architecture-diagram)
6. [Failure State Machine](#6-failure-state-machine)
7. [Idempotency Guarantees](#7-idempotency-guarantees)
8. [Retry Strategy](#8-retry-strategy)
9. [Decision Table](#9-decision-table)
10. [Circuit Breaker Policy](#10-circuit-breaker-policy)
11. [Failure Classification](#11-failure-classification)
12. [DLQ & Replay Service Architecture](#12-dlq--replay-service-architecture)
13. [AI Integration Roadmap](#13-ai-integration-roadmap)

---

## 1. Overview

### What is L3 Self-Healing?

Most systems treat failures as **unpredictable incidents** requiring human reaction.
The **Self-Healing Reliability Layer** provides a structured, automated, AI-compatible failure recovery plane.

This layer:
- ✅ Detects failures automatically
- ✅ Classifies failures by policy
- ✅ Retries when recovery is probable
- ✅ Records forensic context for debugging
- ✅ Routes to auto-recovery or Dead Letter Queue (DLQ)
- ✅ Escalates with human approval when needed
- ✅ Transforms failures into **controllable workflows**

### Design Goals

| Goal | Description |
|------|-------------|
| **Cost Reduction** | Fewer manual interventions, automated recovery |
| **Operational Predictability** | Known failure paths, structured responses |
| **Automation Readiness** | AI-operable, structured decision data |
| **No Heavy Infrastructure** | Works with Celery + Django ORM (no Kafka required) |

---

## 2. Reliability Layers Context

L3 operates as the **top layer** of our reliability stack:

```
┌─────────────────────────────────────────────────────────────┐
│  L3: Self-Healing Recovery                                  │
│  ├─ Retry / Backoff / DLQ                                   │
│  ├─ Human Approval Workflows                                │
│  └─ Failure → Workflow Transformation                       │
├─────────────────────────────────────────────────────────────┤
│  L2: Transactional Safety                                   │
│  ├─ Atomic operations (select_for_update)                   │
│  ├─ Crash recovery (partial commit handling)                │
│  └─ Data consistency (FIFO, idempotency)                    │
├─────────────────────────────────────────────────────────────┤
│  L1: Code-Level Correctness                                 │
│  ├─ Unit / Integration tests                                │
│  ├─ Validation rules                                        │
│  └─ Happy path + edge case coverage                         │
└─────────────────────────────────────────────────────────────┘
```

**Important**: L3 **assumes L1 and L2 are working correctly**.
Self-Healing does not fix bugs — it recovers from **technical failures**.

---

## 3. Core Philosophy

> **"Failures are not incidents. Failures are workflows."**

### Traditional Approach (Reactive)
```
Failure → Alert → Human investigates → Manual fix → Hope it doesn't happen again
```

### Self-Healing Approach (Proactive)
```
Failure → Classify → Auto-retry OR DLQ → Structured review → Resolved
         └─ Every step is tracked, measured, and auditable
```

### Key Principles

1. **Never hide failures** — Every failure is recorded with full context
2. **Retry only what's safe** — Idempotent operations only
3. **Escalate what's dangerous** — Human approval for irreversible actions
4. **Structured data everywhere** — Enables AI analysis and automation
5. **No infinite loops** — Clear retry limits and circuit breakers

---

## 4. Responsibility Split

### Clear Separation of Concerns

| Responsibility | Layer | Example |
|----------------|-------|---------|
| Validation rules | Business Logic | "Not enough points" |
| Security/Access control | Business Logic | "Token expired" |
| Business rule violations | Business Logic | "Cannot cancel delivered order" |
| **Retryable technical errors** | **Self-Healing** | "PG timeout" |
| **Automated recovery** | **Self-Healing** | "Retry after backoff" |
| **Dangerous/ambiguous cases** | **Human Approval** | "Amount mismatch" |
| **Permanent unrecoverable** | **Archive** | "Invalid card number" |

### What Self-Healing Does NOT Do

❌ Fix business logic validation failures
❌ Bypass security policies
❌ Auto-recover without idempotency guarantees
❌ Execute without audit trail

---

## 5. Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                            Application Layer                              │
│                      (Domain Business Logic)                              │
└─────────────────────────────────┬────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                        Self-Healing Layer                                 │
├──────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│  │   Retry     │  │  Circuit    │  │    DLQ      │  │   Human     │     │
│  │  Handler    │  │  Breaker    │  │  Manager    │  │  Approval   │     │
│  │             │  │  (Toggle)   │  │             │  │  Workflow   │     │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘     │
│         │                │                │                │            │
│         └────────────────┴────────────────┴────────────────┘            │
│                                  │                                       │
│                          Policy Engine                                   │
│                    (Decision Table Lookup)                               │
└──────────────────────────────────┬───────────────────────────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
     ┌─────────────┐      ┌─────────────┐      ┌─────────────┐
     │   SUCCESS   │      │    DLQ      │      │  ESCALATED  │
     │  (Auto-     │      │  (Pending   │      │  (Human     │
     │  Recovered) │      │   Review)   │      │   Approval) │
     └─────────────┘      └─────────────┘      └─────────────┘
```

### Infrastructure Requirements

| Component | Technology | Purpose |
|-----------|------------|---------|
| Task Queue | Celery | Async retry, scheduled jobs |
| DLQ Storage | Django ORM (PostgreSQL) | Failure records, forensic data |
| State Management | Django ORM | Circuit breaker state |
| Notifications | Slack/Email/SMS | Escalation alerts |

**No Kafka/Kinesis required** — This design works for SME-scale operations.

---

## 6. Failure State Machine

Every failure follows a predictable state machine:

```
                              ┌─────────────────┐
                              │      NEW        │
                              │  (Just Failed)  │
                              └────────┬────────┘
                                       │
                    ┌──────────────────┼──────────────────┐
                    ▼                  ▼                  ▼
           ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
           │  RETRYING   │    │ NON_RETRY   │    │  SECURITY   │
           │ (Auto-retry │    │ (Direct to  │    │ VIOLATION   │
           │  in backoff)│    │    DLQ)     │    │ (Blocked)   │
           └──────┬──────┘    └──────┬──────┘    └──────┬──────┘
                  │                  │                  │
      ┌───────────┼───────────┐      │                  │
      ▼           ▼           ▼      ▼                  ▼
┌──────────┐ ┌──────────┐ ┌──────────────┐      ┌─────────────┐
│ RESOLVED │ │  MAX     │ │PENDING_REVIEW│      │  SECURITY   │
│ (Success)│ │ RETRIES  │ │   (DLQ)      │      │  INCIDENT   │
└──────────┘ │ EXCEEDED │ └──────┬───────┘      └─────────────┘
             └────┬─────┘        │
                  │              │
                  ▼              ▼
           ┌─────────────────────────┐
           │     PENDING_REVIEW      │
           │        (DLQ)            │
           └───────────┬─────────────┘
                       │
          ┌────────────┼────────────┬────────────┐
          ▼            ▼            ▼            ▼
   ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌────────────┐
   │  REPLAYED │ │ RESOLVED  │ │ REJECTED  │ │ REQUIRES   │
   │(Re-queued)│ │ (Manual   │ │(Permanent │ │ _REVIEW    │
   │           │ │  Fix)     │ │ Archive)  │ │(Escalated) │
   └─────┬─────┘ └─────┬─────┘ └─────┬─────┘ └────────────┘
         │             │             │
         │             ▼             ▼
         │      ┌───────────────────────┐
         │      │      ARCHIVED         │
         │      │   (Soft-Deleted)      │
         │      └───────────────────────┘
         │
         └──► Back to RETRYING (max 2 replays)
```

### State Definitions

| State | Description | Next Actions |
|-------|-------------|--------------|
| `NEW` | Just failed, awaiting classification | Classify → route |
| `RETRYING` | Auto-retry in progress with backoff | Success or exhaust retries |
| `PENDING_REVIEW` | In DLQ, awaiting human review | Replay, Resolve, or Reject |
| `REQUIRES_REVIEW` | Escalated after 3+ failures or handler crash | Senior review required |
| `RESOLVED` | Successfully recovered | Archive after retention |
| `REJECTED` | Permanently unrecoverable | Archive for audit |
| `ARCHIVED` | Soft-deleted, retained for audit | No action (read-only) |
| `SECURITY_INCIDENT` | Security violation detected | Security team handles |

---

## 7. Idempotency Guarantees

> **Critical**: Self-Healing Layer **only retries idempotent operations**.

### Why Idempotency Matters

```
Without Idempotency:
  Retry → Double charge → Customer complaint → Refund → Lost trust

With Idempotency:
  Retry → Same result as first attempt → Customer happy
```

### Idempotency Keys by Domain

| Domain | Idempotency Key | Implementation |
|--------|-----------------|----------------|
| **Payment** | `toss_order_id` + `payment_key` | PG rejects duplicate requests |
| **Webhook** | `event_id` from PG | DB unique constraint |
| **Points** | `order_id` + `type` + `timestamp` | History record lookup |
| **Inventory** | `order_item_id` + `action` | Stock transaction log |
| **Notification** | `user_id` + `type` + `reference_id` | Dedup within time window |

### Idempotency Implementation Pattern

```python
# Example: Payment retry with idempotency
def process_payment_with_retry(order_id: int, amount: Decimal) -> PaymentResult:
    idempotency_key = f"payment:{order_id}:{amount}"

    # Check if already processed
    existing = Payment.objects.filter(
        order_id=order_id,
        amount=amount,
        status__in=['done', 'in_progress']
    ).first()

    if existing:
        return PaymentResult(
            success=True,
            payment=existing,
            was_duplicate=True
        )

    # Proceed with new payment
    return execute_payment(order_id, amount, idempotency_key)
```

### Self-Healing Retry Rules

1. ✅ **Retry allowed**: Operation has idempotency key
2. ✅ **Retry allowed**: Failure is in `RETRYABLE_ERRORS` list
3. ❌ **Retry blocked**: No idempotency guarantee
4. ❌ **Retry blocked**: Error is in `NON_RETRYABLE_ERRORS` list
5. ❌ **Retry blocked**: Security violation detected

---

## 8. Retry Strategy

### Exponential Backoff with Jitter

```
Attempt 1 failed → Wait 4s (±1s jitter) → Retry
Attempt 2 failed → Wait 16s (±4s jitter) → Retry
Attempt 3 failed → Wait 64s (±16s jitter) → Retry
Attempt 4 failed → Move to DLQ
```

### Configuration

```python
RETRY_CONFIG = {
    "MAX_ATTEMPTS": 3,           # Maximum retry attempts
    "BACKOFF_BASE": 4,           # Base for exponential (4^n seconds)
    "BACKOFF_MAX": 180,          # Maximum wait time (3 minutes)
    "JITTER_PERCENT": 25,        # ±25% random jitter
}
```

### Why Jitter?

**Without Jitter (Thundering Herd Problem)**:
```
10,000 requests fail at 10:00:00
10,000 requests retry at 10:00:04  ← Server overwhelmed again
```

**With Jitter**:
```
10,000 requests fail at 10:00:00
Retries spread between 10:00:03 ~ 10:00:05  ← Gradual recovery
```

### Per-Domain Override

| Domain | Max Attempts | Backoff Base | Max Delay | Reason |
|--------|--------------|--------------|-----------|--------|
| Payment | 3 | 4s | 180s | Balance between speed and reliability |
| Webhook | 5 | 2s | 300s | External systems may be slow |
| Notification | 3 | 60s | 600s | Non-critical, can wait longer |
| Inventory | 2 | 1s | 30s | Must resolve quickly for UX |

---

## 9. Decision Table

### Domain-Agnostic Failure Policies

This table maps **specific failure scenarios** to **recovery policies**.

#### Payment Domain

| Failure Type | Condition | Policy | Reason |
|--------------|-----------|--------|--------|
| `PG_TIMEOUT` | No response within 30s | Retry + Backoff | Temporary network issue |
| `PG_5XX_ERROR` | HTTP 500/502/503/504 | Retry + Backoff | PG server issue |
| `PG_NETWORK_ERROR` | Connection refused/reset | Retry + Backoff | Network instability |
| `AMOUNT_MISMATCH_PG_RESPONSE` | `pg_response.amount != request.amount` | Human Approval | Risk of double charge |
| `AMOUNT_MISMATCH_WEBHOOK` | `webhook.amount != payment.amount` | DLQ + High Alert | Data tampering or PG bug |
| `ALREADY_PROCESSED` | PG returns "already processed" | Log + Skip | Idempotency working |
| `INVALID_CARD` | Card number/expiry invalid | Permanent Fail | User responsibility |
| `INSUFFICIENT_FUNDS` | Not enough balance | Validation Fail | User responsibility |
| `CANCEL_ALREADY_CANCELLED` | Cancel request on cancelled payment | Permanent Fail | Invalid state transition |

#### Points Domain

| Failure Type | Condition | Policy | Reason |
|--------------|-----------|--------|--------|
| `DB_SAVE_AFTER_PG_SUCCESS` | Point save failed after payment | Retry (Critical) | Must recover to match PG |
| `INSUFFICIENT_POINTS` | `user.points < required` | Validation Fail | User responsibility |
| `DUPLICATE_DEDUCTION` | Same order deducted twice | Human Approval + Rollback | Data integrity risk |
| `NEGATIVE_BALANCE_DETECTED` | `user.points < 0` after operation | DLQ + Critical Alert | Data corruption |

#### Inventory Domain

| Failure Type | Condition | Policy | Reason |
|--------------|-----------|--------|--------|
| `DB_LOCK_CONFLICT` | `select_for_update` timeout | Retry (short delay) | Lock contention |
| `STOCK_NEGATIVE` | Stock would become negative | DLQ + Alert | Oversell prevention |
| `STOCK_MISMATCH` | Physical != DB stock | Human Review | Requires manual check |

#### Webhook Domain

| Failure Type | Condition | Policy | Reason |
|--------------|-----------|--------|--------|
| `SIGNATURE_INVALID` | HMAC verification failed | Security Incident | Possible attack |
| `PAYLOAD_MALFORMED` | JSON parse error | Permanent Fail | PG bug or attack |
| `DUPLICATE_EVENT` | `event_id` already processed | Log + Skip | Idempotency working |
| `ORDER_NOT_FOUND` | Referenced order doesn't exist | DLQ + Alert | Data inconsistency |

#### Notification Domain

| Failure Type | Condition | Policy | Reason |
|--------------|-----------|--------|--------|
| `SMTP_TIMEOUT` | Email server timeout | Retry + Backoff | Transient issue |
| `FCM_ERROR` | Push notification failed | Retry + Backoff | Transient issue |
| `INVALID_EMAIL` | Email format invalid | Validation Fail | User input error |
| `UNSUBSCRIBED` | User opted out | Skip + Log | Respect preferences |

---

## 10. Circuit Breaker Policy

### Toggle-Based Design (Manual First)

```
┌──────────┐     Operator: force_open()      ┌──────────┐
│  CLOSED  │ ─────────────────────────────► │   OPEN   │
│ (Normal) │                                 │(Blocking)│
└────┬─────┘                                 └────┬─────┘
     │                                            │
     │  ◄─── Operator: force_close() ─────────────┘
     │
     └──► All requests allowed
```

### Why Manual First?

| Approach | Pros | Cons |
|----------|------|------|
| **Auto CB** | Fast response | High false-positive rate |
| **Manual CB** | No false positives | Requires operator attention |
| **Hybrid** (Future) | Best of both | Complex to tune |

### Current Implementation

```python
CIRCUIT_BREAKER = {
    "ENABLED": False,                    # Manual toggle mode
    "FAILURE_THRESHOLD": 5,              # Open after 5 consecutive failures
    "RECOVERY_TIMEOUT": 60,              # Wait 60s before half-open
    "SUCCESS_THRESHOLD": 2,              # Close after 2 successes in half-open
}
```

### Operator Commands

```python
# When PG outage detected
CircuitBreaker.force_open(
    service="toss_payment",
    reason="PG maintenance announced",
    operator_id=admin.id
)

# When PG recovered
CircuitBreaker.force_close(
    service="toss_payment",
    reason="PG confirmed operational",
    operator_id=admin.id
)
```

### Future: Automatic Mode Criteria

Automatic CB opening will be considered when:
- 5-minute failure rate > 80%
- DLQ growth rate > 10 items/minute
- Multiple domains affected simultaneously

---

### Manual Control Semantics: "Code Freeze for Recovery System"

**Core Concept**: `force_open` is not just "request blocking" - it's an operator command that **pauses the entire recovery system**.

#### What force_open Means

| Action | Description | Reason |
|--------|-------------|--------|
| Block new requests | Payment requests immediately return failure | Prevent additional failures during outage |
| **Pause Replay** | DLQ replay is suspended | Retries will also fail if external system is down |
| **Stop Auto-Recovery** | Automatic recovery logic disabled | Operator is controlling the situation |
| Pause Pending → DLQ transition | In-progress operations remain as-is | Decide after situation assessment |

#### What force_close Means

| Action | Description | Reason |
|--------|-------------|--------|
| Allow new requests | Resume normal payment processing | Outage recovery complete |
| **Execute Conditional Replay** | Automatically replay DLQ entries | Process backlogged operations |
| **Escalate failures to REQUIRES_REVIEW** | Failed replays need human attention | Failure after forced close = needs investigation |

```python
# Conditional Replay on Circuit Close
def replay_on_circuit_close(service_name: str, escalate_failures: bool = True):
    """
    Replay backlogged DLQ entries when circuit closes.
    
    Args:
        service_name: Target service name
        escalate_failures: If True, escalate replay failures to REQUIRES_REVIEW
    """
    pending_entries = FailedOperation.objects.filter(
        context__service=service_name,
        status="PENDING"
    )
    
    for entry in pending_entries:
        try:
            replay_single(entry.id)
            entry.status = "RESOLVED"
        except Exception as e:
            if escalate_failures:
                # Failure after forced close = needs human review
                entry.status = "REQUIRES_REVIEW"
                entry.resolution_notes = f"Conditional replay failed after circuit close: {e}"
            else:
                # Normal retry failure handling
                entry.retry_count += 1
        entry.save()
```

#### Critical Behavior Guarantees

The following behaviors are **guaranteed** and have integration tests:

| Scenario | Guaranteed Behavior | Test Reference |
|----------|---------------------|----------------|
| Replay failure after force_close | Circuit stays **CLOSED** (no auto-reopen) | `test_replay_failure_after_circuit_close_does_not_reopen_circuit` |
| Replay failure with escalate_failures=True | Entry moves to **REQUIRES_REVIEW** | `test_replay_on_circuit_close_escalates_failed_replays` |
| Security violation replay attempt | Replay **blocked**, never auto-retry | `test_security_violation_during_replay_creates_incident` |
| Successful replay | Entry moves to **RESOLVED** | `test_replay_on_circuit_close_successful_replays_not_affected` |

**Why these guarantees matter:**
- **No auto-reopen**: Operators expect force_close to persist. If replays fail, that's a DLQ problem, not a circuit breaker problem.
- **Escalation**: Failed replays after operator intervention need human review, not more automation.
- **Security isolation**: Security violations go to SecurityIncident table, never DLQ auto-replay.

---

### Governance Policy: Manual Override TTL & SLA

**Principle**: Manual operator intervention has an **explicit TTL** to prevent indefinite blocking.

#### TTL (Time-To-Live) Policy

| Parameter | Default | Description |
|-----------|---------|-------------|
| `manual_override_ttl_minutes` | 90 min | Auto-expiration time after force_open |
| `half_open_request_limit` | 3 | Test requests allowed in Half-Open state |
| `max_pending_duration_hours` | 72 hours | Maximum PENDING state duration |
| `max_retry_lifetime_hours` | 168 hours | Total retry lifetime (7 days) |

#### TTL Workflow

```
Operator: force_open("toss_payment", ttl_minutes=90)
    ↓
[Circuit OPEN - All requests blocked]
    ↓
+90 minutes elapsed
    ↓
[Periodic Task detects expiration]
    ↓
Auto-transition to CLOSED state + Alert sent
```

```python
# Governance Configuration
SELF_HEALING = {
    "CIRCUIT_BREAKER": {
        "ENABLED": True,
        "FAILURE_THRESHOLD": 5,
        "RECOVERY_TIMEOUT_SECONDS": 60,
        "SUCCESS_THRESHOLD": 2,
        "HALF_OPEN_REQUEST_LIMIT": 3,
        
        # Governance: TTL & SLA
        "MANUAL_OVERRIDE_TTL_MINUTES": 90,      # Prevent indefinite blocking
        "MAX_PENDING_DURATION_HOURS": 72,        # PENDING state SLA
        "MAX_RETRY_LIFETIME_HOURS": 168,         # Total lifetime SLA (7 days)
    }
}
```

#### Periodic Task: TTL Expiration Check

```python
@shared_task(name="expire_manual_overrides")
@app.task(bind=True)
def expire_manual_overrides(self):
    """
    Runs every 5 minutes to release expired Manual Overrides.
    
    Returns:
        dict: Processing result {"expired_count": N, "services": [...]}
    """
    cb_service = CircuitBreakerService()
    expired = cb_service.check_and_expire_manual_overrides()
    
    if expired:
        # Alert: Notify operators of auto-release
        for service in expired:
            notify_ops(f"Circuit Breaker for {service} expired - auto-closed")
    
    return {"expired_count": len(expired), "services": expired}
```

#### SLA Checklist

| SLA Item | Threshold | Action on Violation |
|----------|-----------|---------------------|
| Manual Override Duration | > 90 min | Auto-release + Alert |
| PENDING State Duration | > 72 hours | Transition to REQUIRES_REVIEW + Alert |
| Total Retry Lifetime | > 7 days | Auto-archive + Report |
| DLQ Backlog | > 1000 entries | Critical Alert |
| Replay Failure Rate | > 50% | Circuit Review Alert |

---

## 11. Failure Classification

### Three Categories of Failures

Understanding failure types determines the correct response:

| Type | Definition | Self-Heal? | Alert Level | Example |
|------|------------|------------|-------------|---------|
| **Validation Fail** | User input or business rule violation | ❌ No | None/Low | "Not enough points" |
| **Permanent Fail** | System determined retry is meaningless | ❌ No | Dev/QA | "Invalid card number" |
| **Technical Fail** | Temporary issue, retry may succeed | ✅ Yes | Varies | "PG timeout" |
| **Security Violation** | Potential attack or tampering | ❌ Never | Critical | "Signature mismatch" |

### Detailed Classification

#### Validation Failures (User Responsibility)
- Insufficient points/balance
- Invalid address format
- Missing required fields
- Business rule violations (e.g., minimum order amount)

**Response**: Return error to user, no DLQ, no retry.

#### Permanent Failures (System Boundary)
- Invalid card number (PG rejected)
- Already cancelled order
- Expired token (by design)
- Invalid state transition

**Response**: Log, notify dev if unexpected, archive.

#### Technical Failures (Retry Candidates)
- Network timeout
- HTTP 5xx errors
- Database connection errors
- External API temporary issues

**Response**: Retry with backoff, DLQ if exhausted, alert ops.

#### Security Violations (Never Self-Heal)
- Webhook signature invalid
- Token tampering detected
- Amount manipulation attempt
- Unauthorized access attempts

**Response**:
1. Block immediately
2. Create `SecurityIncident` record
3. Invalidate related sessions/tokens
4. Alert security team (Slack + Email + SMS)
5. Consider account suspension

---

## 12. DLQ & Replay Service Architecture

### Service Layer Design

The DLQ and Replay services provide a clean separation of concerns:

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         DLQ Service Layer                                 │
├──────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐      │
│  │   DLQService    │    │  ReplayService  │    │ Domain Handlers │      │
│  │                 │    │                 │    │                 │      │
│  │ • store_failure │    │ • replay_single │    │ • Payment       │      │
│  │ • get_pending   │    │ • replay_batch  │    │ • Point         │      │
│  │ • get_stats     │    │ • validate      │    │ • Webhook       │      │
│  └────────┬────────┘    └────────┬────────┘    └────────┬────────┘      │
│           │                      │                      │               │
│           └──────────────────────┴──────────────────────┘               │
│                                  │                                       │
│                          Celery Tasks Layer                              │
│           ┌──────────────────────┴──────────────────────┐               │
│           │ • replay_single_dlq_entry                   │               │
│           │ • replay_batch_by_failure_type              │               │
│           │ • replay_on_circuit_breaker_close           │               │
│           │ • cleanup_resolved_dlq_entries              │               │
│           └─────────────────────────────────────────────┘               │
└──────────────────────────────────────────────────────────────────────────┘
```

### DLQService

Central service for DLQ storage operations:

```python
from shopping.services.self_healing.dlq_service import DLQService, store_to_dlq

# Store a failure with forensic context
entry = DLQService.store_failure(
    domain="payment",
    failure_type="PG_TIMEOUT",
    error_code="TIMEOUT_001",
    error_message="Connection timeout after 30s",
    order=order,
    payment=payment,
    snapshot_data={"amount": 50000, "method": "card"},
    request_data=original_request,
    response_data=pg_response,
)

# Query pending entries
pending = DLQService.get_pending_entries(domain="payment")
replayable = DLQService.get_replayable_entries(limit=100)
sla_breached = DLQService.get_sla_breached_entries()
```

### ReplayService

Orchestrates replay operations with domain-specific handlers:

```python
from shopping.services.self_healing.replay_service import ReplayService

# Replay single entry
result = ReplayService.replay_single(dlq_entry_id=123, operator_id=admin.id)

# Batch replay by failure type
results = ReplayService.replay_batch_by_failure_type(
    failure_type="PG_TIMEOUT",
    limit=50
)

# Batch replay by domain
results = ReplayService.replay_batch_by_domain(
    domain="payment",
    limit=100
)
```

### Domain Replay Handlers

Each domain has a specialized handler implementing the replay logic:

| Handler | Domain | Key Validations |
|---------|--------|-----------------|
| `PaymentReplayHandler` | payment | Payment status, order status, security flags |
| `PointReplayHandler` | point | User existence, duplicate check |
| `WebhookReplayHandler` | webhook | Event deduplication, signature re-verify |

### REQUIRES_REVIEW Escalation

Entries are escalated to `REQUIRES_REVIEW` status when:

1. **Repeated failures**: 3+ consecutive replay failures
2. **Handler crash**: Replay handler throws unexpected exception
3. **Manual escalation**: Operator marks for senior review

```python
# Auto-escalation after 3 failures
if entry.retry_count >= 3:
    entry.status = FailedOperation.Status.REQUIRES_REVIEW

# Handler crash detection
try:
    result = handler.replay(entry)
except Exception as e:
    entry.mark_as_requires_review(
        note=f"Handler crashed: {type(e).__name__}: {str(e)}"
    )
```

### Soft-Delete Pattern

Resolved entries are never hard-deleted. Instead, they are archived:

```python
# Cleanup task uses soft-delete
def cleanup_resolved_dlq_entries():
    entries = FailedOperation.objects.filter(
        status=FailedOperation.Status.RESOLVED,
        resolved_at__lt=cutoff_date
    )
    for entry in entries:
        entry.mark_as_archived()  # Sets status=ARCHIVED, not delete()
```

**Benefits**:
- Audit trail preserved for compliance
- Forensic data available for pattern analysis
- No orphaned references

---

## 13. AI Integration Roadmap

### Why Self-Healing Enables AI

Structured failure data makes AI assistance possible:

```
Traditional: "Payment failed" → Manual investigation
Self-Healing: {
    failure_type: "PG_TIMEOUT",
    retry_count: 3,
    domain: "payment",
    context: {...}
} → AI can analyze, suggest, automate
```

### AI Integration Opportunities

| Capability | AI Role | Implementation Phase |
|------------|---------|----------------------|
| **Failure Classification** | Suggest policy for unknown errors | Phase 1 |
| **Root Cause Analysis** | Summarize patterns from DLQ data | Phase 1 |
| **Fix Proposal** | Draft code patches for recurring issues | Phase 2 |
| **Auto-PR Generation** | Create PRs for approved fixes | Phase 2 |
| **Policy Tuning** | Recommend retry/backoff adjustments | Phase 3 |
| **Documentation** | Auto-generate runbooks from incidents | Phase 3 |

### Enabling Requirements

1. **Structured Data**: All failures use consistent schema
2. **Full Context**: Forensic data stored with each failure
3. **Clear States**: State machine makes decisions traceable
4. **API Access**: DLQ queryable via REST API
5. **Feedback Loop**: Human decisions train AI suggestions

---

## Summary

L3 Self-Healing Layer transforms failures from **unpredictable incidents** into **manageable workflows**.

### Key Takeaways

1. **Separation**: Business logic validates, Self-Healing recovers
2. **Idempotency**: Only retry what's safe to retry
3. **State Machine**: Every failure follows a known path
4. **Decision Table**: Policy lookup, not ad-hoc decisions
5. **Manual First**: Circuit breaker starts manual, evolves to auto
6. **AI Ready**: Structured data enables future automation

---

## Related Documents

- [L3 Self-Healing Operations Guide](./L3_SELF_HEALING_OPERATIONS.md)
- [L2 Crash Recovery Test Plan](./L2_CRASH_RECOVERY_TEST_PLAN.md)
- [Celery Retry Guide](./CELERY_RETRY_GUIDE.md)

---

*This document defines the architecture. For operational procedures, DLQ management, metrics, and testing, see the [Operations Guide](./L3_SELF_HEALING_OPERATIONS.md).*
