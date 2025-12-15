# Core Self-Healing Capabilities

This document details the fundamental self-healing capabilities implemented in the system.

---

## Capability 1: Dead Letter Queue (DLQ) Management

### 1.1 Purpose

The DLQ provides durable storage for failed operations that cannot be immediately recovered. It serves as a safety net ensuring no failure is silently lost.

### 1.2 Trigger Conditions

- Retry exhaustion (max attempts exceeded)
- Non-retryable exception encountered
- Explicit DLQ routing by application code
- Timeout with no recovery path

### 1.3 Behavioral Flow

```
┌─────────────────┐
│ Operation Fails │
└────────┬────────┘
         │
         ▼
┌─────────────────────┐
│ Retry Attempts      │
│ (max_attempts = 3)  │
└────────┬────────────┘
         │ Exhausted
         ▼
┌─────────────────────────────────────────────┐
│ DLQ Entry Created                           │
│ - Domain classification                     │
│ - Failure type                              │
│ - Full forensic context                     │
│ - Entity references (order_id, etc.)        │
│ - Snapshot data for replay                  │
│ - Recommended action hint                   │
└────────┬────────────────────────────────────┘
         │
         ▼
┌──────────────────────────┐
│ Status: PENDING          │
│ Awaiting: Replay/Review  │
└──────────────────────────┘
```

### 1.4 DLQ Entry Lifecycle (State Machine)

```
PENDING ──┬──► REVIEWING ──┬──► RESOLVED
          │                │
          │                └──► REJECTED
          │
          ├──► REPLAYED ──────► RESOLVED
          │
          ├──► REQUIRES_REVIEW
          │
          ├──► ARCHIVED
          │
          └──► EXPIRED
```

### 1.5 Safety & Guardrails

| Guarantee | Implementation |
|-----------|----------------|
| **No data loss** | Atomic create operation with DB transaction |
| **Retention policy** | Configurable `retention_days` (default: 30) |
| **Expiration** | `expires_at` field with automatic archival |
| **Max retries** | `max_replay_attempts` limit (default: 2) |
| **Idempotency** | Entity reference keys for duplicate detection |

### 1.6 Code References

| Component | Location |
|-----------|----------|
| Service | `services/dlq_service.py` |
| Data Types | `interfaces/repositories.py::FailedOperationData` |
| Configuration | `core/config.py::DLQConfig` |
| Repository Interface | `interfaces/repositories.py::FailedOperationRepository` |

---

## Capability 2: Retry with Exponential Backoff

### 2.1 Purpose

Provides resilient retry logic for transient failures with mathematically-controlled spacing to prevent thundering herd and allow external systems to recover.

### 2.2 Trigger Conditions

- Retryable exception (configurable exception types)
- Explicit retry request
- Rate limit with Retry-After header

### 2.3 Behavioral Flow

```
┌────────────────────────────────┐
│ Execute Operation              │
└─────────────┬──────────────────┘
              │ Exception
              ▼
┌─────────────────────────────────────────────┐
│ is_retryable(exception) ?                   │
│ - Not in non_retryable_exceptions           │
│ - Is in retryable_exceptions                │
│ - attempt < max_attempts                    │
└─────────────┬───────────────────────────────┘
              │ Yes
              ▼
┌─────────────────────────────────────────────┐
│ Calculate Backoff                           │
│ delay = min(base^attempt, max_delay)        │
│ delay = delay * (1 ± jitter_percent)        │
└─────────────┬───────────────────────────────┘
              │
              ▼
┌──────────────────────────────┐
│ Wait(delay) → Retry          │
└──────────────────────────────┘
```

### 2.4 Backoff Formula

```
delay = min(base^attempt, max_delay) * (1 ± jitter/100)

Default configuration:
- base = 4 seconds
- max_delay = 180 seconds (3 minutes)
- jitter = 25%

Example progression:
- Attempt 1: 4s (3-5s with jitter)
- Attempt 2: 16s (12-20s with jitter)
- Attempt 3: 64s (48-80s with jitter)
- Attempt 4+: 180s (capped)
```

### 2.5 Backoff Strategy Variants

The system implements multiple backoff strategies:

| Strategy | Description | Use Case |
|----------|-------------|----------|
| `ExponentialBackoff` | Base^n with optional jitter | Default for external APIs |
| `LinearBackoff` | Base + (n * increment) | Predictable delays |
| `ConstantBackoff` | Fixed delay | Polling scenarios |
| `DecorrelatedJitterBackoff` | AWS-style random in [base, prev*3] | Distributed systems |

### 2.6 Safety & Guardrails

| Guarantee | Implementation |
|-----------|----------------|
| **Max attempts** | `max_attempts` config (default: 3) |
| **Max delay cap** | `max_delay` prevents infinite waits |
| **Jitter** | Prevents synchronized retry storms |
| **DLQ fallback** | Automatic DLQ routing on exhaustion |
| **Context preservation** | Retry history captured in forensic context |

### 2.7 Code References

| Component | Location |
|-----------|----------|
| Retry Handler | `services/retry_handler.py` |
| Backoff Calculator | `services/backoff_calculator.py` |
| Backoff Strategies | `core/backoff.py` |
| Configuration | `core/config.py::RetryConfig` |

---

## Capability 3: Idempotency Checking

### 3.1 Purpose

Ensures that retry and replay operations produce the same result as the original, preventing duplicate side effects (e.g., double payments, duplicate notifications).

### 3.2 Trigger Conditions

- Before executing any replay operation
- Before processing any DLQ item
- As a guard in retry loops

### 3.3 Behavioral Flow

```
┌─────────────────────────────────┐
│ Generate Idempotency Key        │
│ Format: domain:entity:operation │
│ Example: order:123:process      │
└─────────────┬───────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│ Check Cache (if available)              │
│ cache_key = "idempotency:{domain}:{key}"|
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│ Check Database (authoritative)          │
│ lookup_fn(key) → existing_record?       │
└─────────────┬───────────────────────────┘
              │
    ┌─────────┴─────────┐
    │                   │
    ▼                   ▼
┌───────────────┐   ┌─────────────────────┐
│ is_duplicate  │   │ not_duplicate       │
│ Return cached │   │ Proceed, cache key  │
└───────────────┘   └─────────────────────┘
```

### 3.4 Idempotency Key Types

```python
# Operation-based key
IdempotencyKey.for_operation("order", 123, "process")
# → "order:123:process"

# Event-based key
IdempotencyKey.for_event("evt_abc123")
# → "evt_abc123"

# Resource action key
IdempotencyKey.for_resource_action("points", 456, "deduct", amount=100)
# → "points:456:deduct:100"

# Custom key
IdempotencyKey.custom("my-operation-xyz")
```

### 3.5 Safety & Guardrails

| Guarantee | Implementation |
|-----------|----------------|
| **Cache TTL** | Configurable TTL to prevent memory bloat |
| **Clock skew tolerance** | `clock_skew_tolerance_seconds` (default: 5s) |
| **Two-layer check** | Cache (fast) + DB (authoritative) |
| **Collision resistance** | SHA256 hash for key normalization |

### 3.6 Code References

| Component | Location |
|-----------|----------|
| Service | `services/idempotency_service.py` |
| Key Types | `services/idempotency_service.py::IdempotencyKey` |
| Configuration | `core/config.py::IdempotencyConfig` |

---

## Capability 4: Forensic Context Capture

### 4.1 Purpose

Captures comprehensive debugging information at failure time, enabling post-mortem analysis and informed replay decisions without accessing original systems.

### 4.2 Trigger Conditions

- Any failure routed to DLQ
- Security incident creation
- Circuit breaker trip
- Retry attempt logging

### 4.3 Context Components Captured

| Category | Fields Captured |
|----------|-----------------|
| **Timing** | `request_timestamp`, `response_timestamp`, `latency_ms` |
| **Retry History** | List of `{attempt, error_code, error_message, backoff_seconds}` |
| **State Snapshots** | `state_before`, `state_after` (entity statuses, counts) |
| **Request Context** | `client_ip`, `user_agent`, `session_id` |
| **Task Context** | `task_name`, `task_id`, `queue_name`, `worker_id` |
| **External System** | `external_request_id`, `external_response_code`, `external_response_body` |
| **Extra** | Arbitrary key-value pairs |

### 4.4 Sensitive Data Handling

```python
# Sensitive field masking (configurable)
sensitive_field_patterns = (
    "password", "secret", "token", "api_key",
    "apikey", "authorization", "auth", "credential"
)
```

### 4.5 Size Limits

| Field | Limit | Purpose |
|-------|-------|---------|
| `error_message` | 500 chars | Prevent blob storage |
| `response_body` | 5000 chars | Capture enough for debug |
| `max_stack_frames` | 50 | Truncate deep stacks |
| `max_context_size` | 64KB | Total context limit |

### 4.6 Code References

| Component | Location |
|-----------|----------|
| Core Forensic | `core/forensic.py` |
| Service Wrapper | `services/forensic_context.py` |
| Configuration | `config.py::ForensicSettings` |

---

## Capability 5: Replay Service

### 5.1 Purpose

Orchestrates re-execution of failed operations stored in the DLQ, with proper idempotency checking, handler dispatch, and result tracking.

### 5.2 Replay Types

| Type | Trigger | Behavior |
|------|---------|----------|
| **Manual Replay** | Operator selects item | Single item replay |
| **Batch Replay** | Operator selects filter | Multiple items by domain/failure_type |
| **Conditional Replay** | Circuit breaker closes | Auto-replay related failures |

### 5.3 Behavioral Flow (Single Replay)

```
┌─────────────────────────────────────────┐
│ replay_single(dlq_id)                   │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│ Atomic Acquisition                      │
│ try_acquire_for_replay(dlq_id)          │
│ - Check status == PENDING               │
│ - Check retry_count < max_retries       │
│ - Set status = PROCESSING (lock)        │
└─────────────┬───────────────────────────┘
              │ Success
              ▼
┌─────────────────────────────────────────┐
│ Get Handler for Domain                  │
│ handler = get_replay_handler(domain)    │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│ Execute Handler                         │
│ handler.can_replay(failed_op)?          │
│ result = handler.replay(failed_op)      │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│ Complete Replay                         │
│ repository.complete_replay(             │
│   success=result.success,               │
│   resolution_type="auto_replay"         │
│ )                                       │
└─────────────────────────────────────────┘
```

### 5.4 Conditional Replay (Circuit Breaker Recovery)

When a circuit breaker closes (service recovers), the system can automatically replay related DLQ entries:

```python
def replay_on_circuit_close(service_name, max_items=50):
    # Maps service to failure types
    failure_types = service_failure_type_map.get(service_name, [])
    
    # Replay matching pending entries
    for entry in get_pending_by_failure_types(failure_types):
        result = replay_single(entry.id)
        
        # Escalate failures to REQUIRES_REVIEW
        if not result.success and escalate_failures:
            mark_as_requires_review(entry.id)
```

### 5.5 Handler Registration

```python
# Handlers must be registered by adapters
from selfhealing.services.replay_service import register_replay_handler

class MyDomainHandler(ReplayHandler):
    @property
    def domain(self) -> str:
        return "my_domain"
    
    def can_replay(self, failed_op) -> tuple[bool, str]:
        return True, ""
    
    def replay(self, failed_op) -> ReplayResult:
        # Domain-specific replay logic
        return ReplayResult.succeeded(failed_op.id, "Done")

register_replay_handler(MyDomainHandler())
```

### 5.6 Safety & Guardrails

| Guarantee | Implementation |
|-----------|----------------|
| **Atomic acquisition** | Prevents race conditions between workers |
| **Max replay attempts** | `max_replay_attempts` config (default: 2) |
| **Handler isolation** | Exceptions caught and escalated |
| **Escalation** | Failed replays → REQUIRES_REVIEW status |
| **Audit trail** | `resolution_type`, `resolution_note` recorded |

### 5.7 Code References

| Component | Location |
|-----------|----------|
| Service | `services/replay_service.py` |
| Handler ABC | `services/replay_service.py::ReplayHandler` |
| Default Handler | `services/replay_service.py::DefaultReplayHandler` |
