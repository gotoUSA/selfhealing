# Self-Healing Load Test Plan

> A Self-Healing system is not tested for "working without errors"
> but for "always terminating correctly when errors occur."

---

## 📋 Table of Contents

1. [Current State Analysis](#current-state-analysis)
2. [Test Architecture Overview](#test-architecture-overview)
3. [Load-Induced Tests (Stage 11-13)](#load-induced-tests-stage-11-13)
4. [Recovery Mechanism Tests (Stage 14-15)](#recovery-mechanism-tests-stage-14-15)
5. [Fault-Oriented Tests (Stage 16-22)](#fault-oriented-tests-stage-16-22)
6. [Implementation Priority](#implementation-priority)
7. [Verification Checklist](#verification-checklist)
8. [References](#references)

---

## Current State Analysis

### ✅ Existing Tests

| Stage | Test | Self-Healing Aspect |
|-------|------|---------------------|
| 2 | Idempotency | Idempotent key validation ✓ |
| 3 | Latency | Retry on delay ✓ |
| 5 | Rollback | Stock recovery on failure ✓ |
| 6 | Chaos | Random failure resilience ✓ |
| 9 | Soak | Memory/resource leak detection ✓ |
| 10 | Self-Healing | Control API functionality ✓ |

### ❌ Missing Scenarios (Original)

| Pattern | Purpose | Status |
|---------|---------|--------|
| Ramp-up | Threshold discovery | ❌ Missing |
| Spike → Recovery | Recovery stability | ❌ Missing |
| Repeated Spike | Backoff tuning | ❌ Missing |
| DLQ Verification | Reprocessing consistency | ❌ Missing |
| Circuit Breaker Transitions | Auto open/close | ❌ Missing |

### 🔴 Critical Missing Scenarios (Fault-Oriented)

| Category | Risk | Test Status |
|----------|------|-------------|
| Stateful Failures (DB Lock, Deadlock, Long Tx) | Recovery failure, stock inconsistency, order stuck | ❌ Missing |
| Cache Sync / TTL Strategy | Stale read, write-then-cache-hit race | ❌ Missing |
| Service Chain Failover | Auth → Payment → Order coupling | ❌ Missing |
| Rollback Failure (Double Failure) | Unclear "secondary failure" handling | ❌ Missing |
| Delayed Success / Late Webhook | PG success but webhook 10min delayed | ❌ Missing |
| Self-Healing False Positive | Normal but circuit opens | ❌ Missing |
| Rate-Limited Environment Backoff | Retry worsens rate-limit | ❌ Missing |

---

## Test Architecture Overview

```
Self-Healing Test Suite
│
├── Load-Induced Tests (Stage 11-13)
│   ├── Stage 11: Ramp-up Threshold Discovery
│   ├── Stage 12: Spike & Recovery
│   └── Stage 13: Repeated Spike (Backoff Tuning)
│
├── Recovery Mechanism Tests (Stage 14-15)
│   ├── Stage 14: DLQ Replay Verification
│   └── Stage 15: Circuit Breaker Auto Transitions
│
└── Fault-Oriented Tests (Stage 16-22) ★ NEW
    │
    ├── State Faults (16-17)
    │   ├── Stage 16: DB Lock / Deadlock Recovery
    │   └── Stage 17: Cache Invalidation TTL Race
    │
    ├── Event Faults (18-20)
    │   ├── Stage 18: Chain Failure Propagation
    │   ├── Stage 19: Rollback Failure (Secondary Action)
    │   └── Stage 20: Delayed Webhook Out-of-Order
    │
    └── Self-Healing Faults (21-22)
        ├── Stage 21: False Positive Detection
        └── Stage 22: Self-Healing + Rate Limit Conflict
```

---

## Load-Induced Tests (Stage 11-13)

### Stage 11: Ramp-up Threshold Discovery

```python
# load_tests/scenarios/stage11_ramp_threshold.py

Purpose: Discover system threshold and Self-Healing trigger points

LoadShape:
  - LinearRamp: 10 → 300 users over 20 minutes

Metrics to Collect:
  - retry_count (per minute)
  - circuit_breaker_state
  - dlq_count
  - avg_response_time (per minute)
  - error_rate (per minute)

Output:
  - Threshold graph (users vs metrics)
  - User count when retry starts
  - User count when CB transitions
```

### Stage 12: Spike & Recovery

```python
# load_tests/scenarios/stage12_spike_recovery.py

Purpose: Verify recovery stability after sudden load spike

LoadShape:
  Phase 1 (0-30s): 0 → 500 users (spike)
  Phase 2 (30s-2m30s): 500 users (sustain)
  Phase 3 (2m30s-5m30s): 500 → 50 users (ramp-down)
  Phase 4 (5m30s-10m30s): 50 users (stabilize)

Metrics to Collect:
  - circuit_breaker_open_time
  - circuit_breaker_close_time
  - dlq_max_count
  - dlq_replay_success_rate
  - data_consistency (before vs after)
```

### Stage 13: Repeated Spike (Backoff Tuning)

```python
# load_tests/scenarios/stage13_repeated_spike.py

Purpose: Verify backoff doesn't over-accumulate on repeated failures

LoadShape:
  3 cycles of:
    Spike: 0 → 500 users (30s)
    Sustain: 500 users (1m)
    Recovery: 500 → 50 users (1m)
    Cool: 50 users (2m)

Metrics to Collect:
  - recovery_time_per_cycle
  - backoff_duration_cumulative
  - circuit_breaker_transitions
  - final_state (normalized confirmation)
```

---

## Recovery Mechanism Tests (Stage 14-15)

### Stage 14: DLQ Replay Verification

```python
# load_tests/scenarios/stage14_dlq_replay.py

Purpose: Verify DLQ reprocessing accuracy and data consistency

Scenario:
  Step 1: Attempt 100 payments (50% forced failure)
  Step 2: Verify DLQ insertion (expect 50 entries)
  Step 3: Remove forced failure
  Step 4: Trigger DLQ replay
  Step 5: Verify results

Verification:
  - dlq_replayed == dlq_inserted
  - duplicate_payment == 0
  - idempotent_key_violations == 0
  - stock_after == stock_expected
  - point_after == point_expected
```

### Stage 15: Circuit Breaker Auto Transitions

```python
# load_tests/scenarios/stage15_cb_transitions.py

Purpose: Verify automatic Circuit Breaker state transitions

Scenario:
  Phase 1: Normal requests → confirm closed
  Phase 2: Inject 5 consecutive failures → confirm open transition
  Phase 3: Wait recovery_timeout (60s) → confirm half_open transition
  Phase 4: 2 successful requests → confirm closed return

Verification:
  - Each transition accuracy
  - Transition timing (config compliance)
  - half_open request limiting
  - Transition audit log
```

---

## Fault-Oriented Tests (Stage 16-22)

> **Key Insight**: "Load breaks systems predictably. Timing breaks systems silently after deployment."

These tests validate self-healing behavior for **non-load-induced failures** that are often more dangerous in production.

### Stage 16: DB Lock / Deadlock Recovery

```python
# load_tests/scenarios/stage16_db_lock_recovery.py

Purpose: Verify recovery from database lock contention and deadlocks

Scenario:
  Step 1: Start Transaction A (SELECT FOR UPDATE on product stock)
  Step 2: Hold lock for 30 seconds (simulate long transaction)
  Step 3: Concurrent requests try to update same stock
  Step 4: Observe timeout handling and retry behavior
  Step 5: Release lock, verify recovery

Fault Injection:
  - Long-running transaction (30s lock hold)
  - Concurrent conflicting transactions
  - Simulated deadlock (circular dependency)

Verification:
  - [ ] Lock timeout triggers retry (not crash)
  - [ ] Deadlock detection and resolution
  - [ ] No permanent order "pending" state
  - [ ] Stock consistency after recovery
  - [ ] Transaction audit log

Real-World Case:
  "Flash sale: 1000 users hit same product, SELECT FOR UPDATE
   causes lock queue → timeout cascade → stock becomes negative"
```

### Stage 17: Cache Invalidation TTL Race

```python
# load_tests/scenarios/stage17_cache_ttl_race.py

Purpose: Verify cache/DB consistency under TTL race conditions

Scenario:
  Step 1: Set product price = 10000 (cached, TTL=60s)
  Step 2: Update price to 15000 in DB
  Step 3: Immediately request product (before invalidation)
  Step 4: Verify which price is used for validation
  Step 5: Wait for TTL expiry, verify consistency

Fault Injection:
  - Stale cache read after DB write
  - Cache invalidation delay
  - Write-through vs write-behind race

Verification:
  - [ ] Stale price detection mechanism
  - [ ] Order validation uses correct price
  - [ ] Cache invalidation on critical updates
  - [ ] No "price changed" errors on valid orders
  - [ ] Eventual consistency achieved

Real-World Case:
  "Admin changes price, customer orders with old cached price,
   order fails validation → customer complaint"
```

### Stage 18: Chain Failure Propagation

```python
# load_tests/scenarios/stage18_chain_failure.py

Purpose: Verify failure handling across service chain (Auth → Order → Payment)

Scenario:
  Step 1: Auth success → Order creation success → Payment FAILS
  Step 2: Verify order rollback initiated
  Step 3: Verify auth session state
  Step 4: Verify stock restoration
  Step 5: Test partial success scenarios

Failure Points:
  Point A: Auth success → Order FAILS
  Point B: Auth success → Order success → Payment FAILS
  Point C: Auth success → Order success → Payment success → Webhook FAILS

Verification:
  - [ ] Each failure point has defined rollback
  - [ ] No orphaned orders (created but unpaid)
  - [ ] No orphaned payments (paid but no order)
  - [ ] Stock correctly restored at each point
  - [ ] User notification for each failure type

Real-World Case:
  "Payment succeeded at PG, but internal order creation failed.
   Customer charged, no order record. Refund required."
```

### Stage 19: Rollback Failure (Secondary Action)

```python
# load_tests/scenarios/stage19_rollback_failure.py

Purpose: Verify handling when rollback itself fails (double failure)

Scenario:
  Step 1: Payment fails → trigger stock rollback
  Step 2: Inject failure during rollback (DB connection lost)
  Step 3: Observe secondary failure handling
  Step 4: Verify recovery mechanism (retry, DLQ, manual queue)
  Step 5: Final consistency check

Fault Injection:
  - DB connection failure during rollback
  - Cache invalidation failure during rollback
  - Timeout during compensating transaction

Verification:
  - [ ] Rollback failure is logged/alerted
  - [ ] Secondary retry mechanism exists
  - [ ] DLQ captures failed rollback for manual review
  - [ ] No double-decrement of stock
  - [ ] Consistency reconciliation job exists

Real-World Case:
  "Stock -1 (order) → payment fails → rollback (+1) fails
   → stock shows -1 but no order exists → inventory mismatch"
```

### Stage 20: Delayed Webhook Out-of-Order

```python
# load_tests/scenarios/stage20_delayed_webhook.py

Purpose: Verify handling of delayed/out-of-order payment webhooks

Scenario:
  Step 1: Initiate payment (order status = PENDING)
  Step 2: PG processes successfully
  Step 3: Delay webhook delivery by 10 minutes
  Step 4: Meanwhile, order times out → marked FAILED
  Step 5: Late webhook arrives with SUCCESS status
  Step 6: Verify conflict resolution

Timing Variations:
  - Normal: Payment → Webhook (5s) → Order confirmed
  - Delayed: Payment → Webhook (10min) → Order already failed
  - Out-of-order: Cancel webhook arrives before success webhook

Verification:
  - [ ] Delayed webhook doesn't resurrect failed order
  - [ ] Idempotent key prevents duplicate processing
  - [ ] Out-of-order events handled (state machine)
  - [ ] Customer refund triggered for conflicts
  - [ ] Alert for webhook delay > threshold

Real-World Case:
  "Customer pays, webhook delayed, order timeout → customer 
   sees payment success in bank, order shows failed. 
   Double payment on retry attempt."
```

### Stage 21: False Positive Detection

```python
# load_tests/scenarios/stage21_false_positive.py

Purpose: Verify Self-Healing doesn't trigger on transient issues (false positive)

Scenario:
  Step 1: Normal traffic with 99.5% success
  Step 2: Inject latency spike (500ms → 2000ms) but all succeed
  Step 3: Observe if Circuit Breaker opens (should NOT)
  Step 4: Inject 1 real failure among slow requests
  Step 5: Verify proportional response

False Positive Cases:
  - High latency but 100% success → CB should stay closed
  - Single timeout among many successes → should not open CB
  - Slow external dependency but internal system healthy

Verification:
  - [ ] Latency alone doesn't trigger CB open
  - [ ] Error rate threshold is respected
  - [ ] No unnecessary service degradation
  - [ ] Alert distinguishes slow vs broken
  - [ ] Revenue impact of false positive logged

Real-World Case:
  "Slow network day, all requests succeed but take 3s.
   CB opens → 50% requests rejected → revenue loss
   when system was actually working fine."
```

### Stage 22: Self-Healing + Rate Limit Conflict

```python
# load_tests/scenarios/stage22_rate_limit_conflict.py

Purpose: Verify retry mechanism doesn't trigger rate limiting (self-DDoS)

Scenario:
  Step 1: External API has 100 req/min rate limit
  Step 2: 50 requests fail → retry triggered
  Step 3: Retries + new requests exceed rate limit
  Step 4: Rate limit (429) triggers more retries
  Step 5: Observe cascade effect

Rate Limit Scenarios:
  - PG rate limit: Too many payment attempts
  - SMS rate limit: OTP resend spam
  - External API: Third-party service limits

Verification:
  - [ ] Retry respects external rate limits
  - [ ] Backoff increases on 429 response
  - [ ] Circuit breaker opens on rate limit cascade
  - [ ] No self-inflicted DDoS
  - [ ] Rate limit headers parsed and respected

Real-World Case:
  "Payment retries hit PG rate limit → 429 responses
   → more retries → entire payment service blocked
   → all customers affected, not just original failures."
```

---

## Implementation Priority

### Phase 1: Critical (Week 1)

| Priority | Stage | Reason |
|----------|-------|--------|
| 1 | Stage 20 | Delayed Webhook - Most common PG integration issue |
| 2 | Stage 16 | DB Lock - Most difficult to debug in production |
| 3 | Stage 21 | False Positive - Direct revenue impact |
| 4 | Stage 12 | Spike & Recovery - Basic Self-Healing validation |

### Phase 2: High (Week 2)

| Priority | Stage | Reason |
|----------|-------|--------|
| 5 | Stage 22 | Rate Limit Conflict - Prevents self-DDoS |
| 6 | Stage 18 | Chain Failure - E-commerce critical path |
| 7 | Stage 15 | CB Auto Transitions - Core self-healing |
| 8 | Stage 14 | DLQ Replay - Data consistency priority |

### Phase 3: Medium (Week 3)

| Priority | Stage | Reason |
|----------|-------|--------|
| 9 | Stage 19 | Rollback Failure - Edge case but critical |
| 10 | Stage 17 | Cache TTL Race - Common but manageable |
| 11 | Stage 11 | Ramp-up - Operational threshold |
| 12 | Stage 13 | Repeated Spike - Backoff tuning |

### Phase 4: config.yaml Update

```yaml
# New profile additions
profiles:
  self_healing_quick:
    stages:
      - stage10_self_healing
      - stage12_spike_recovery
      - stage15_cb_transitions
    description: "Quick Self-Healing validation (~15min)"

  self_healing_load:
    stages:
      - stage11_ramp_threshold
      - stage12_spike_recovery
      - stage13_repeated_spike
      - stage14_dlq_replay
      - stage15_cb_transitions
    description: "Load-induced Self-Healing full test (~60min)"

  self_healing_fault:
    stages:
      - stage16_db_lock_recovery
      - stage17_cache_ttl_race
      - stage18_chain_failure
      - stage19_rollback_failure
      - stage20_delayed_webhook
      - stage21_false_positive
      - stage22_rate_limit_conflict
    description: "Fault-oriented Self-Healing validation (~45min)"

  self_healing_complete:
    stages:
      - stage11_ramp_threshold
      - stage12_spike_recovery
      - stage13_repeated_spike
      - stage14_dlq_replay
      - stage15_cb_transitions
      - stage16_db_lock_recovery
      - stage17_cache_ttl_race
      - stage18_chain_failure
      - stage19_rollback_failure
      - stage20_delayed_webhook
      - stage21_false_positive
      - stage22_rate_limit_conflict
    description: "Complete Self-Healing validation (~90min)"
```

---

## Verification Checklist

### 📋 Self-Healing Test Verification Items

#### 1. Retry Mechanism
- [ ] Retry count logged
- [ ] Retry interval (exponential backoff) verified
- [ ] DLQ insertion on max_retry exceeded
- [ ] Rate limit awareness in retry logic

#### 2. DLQ (Dead Letter Queue)
- [ ] DLQ insertion count
- [ ] DLQ replay success rate
- [ ] No duplicate processing after replay
- [ ] Idempotent key working correctly

#### 3. Circuit Breaker
- [ ] Open transition timing (failure_threshold)
- [ ] Half-open transition timing (recovery_timeout)
- [ ] Closed return condition (success_threshold)
- [ ] Manual override working
- [ ] No false positive triggers

#### 4. Data Consistency
- [ ] stock_before == stock_after (on failure)
- [ ] points_before == points_after (on failure)
- [ ] Duplicate payments == 0
- [ ] Order status consistency
- [ ] No orphaned records

#### 5. State Management
- [ ] DB lock timeout handling
- [ ] Deadlock detection and recovery
- [ ] Cache invalidation on writes
- [ ] Transaction rollback completeness

#### 6. Event Handling
- [ ] Delayed event tolerance
- [ ] Out-of-order event handling
- [ ] Duplicate event idempotency
- [ ] Event ordering state machine

#### 7. Monitoring/Logging
- [ ] State transition audit log
- [ ] Alert notification sent
- [ ] Metrics collection working
- [ ] False positive alerts distinguishable

#### 8. Recovery Stability
- [ ] Time to normalize after failure
- [ ] No cumulative degradation on repeated failures
- [ ] Continuation of recovery after reboot
- [ ] Rollback failure secondary action

---

## References

### Industry Standard Chaos Engineering

- [Netflix Chaos Monkey](https://netflix.github.io/chaosmonkey/)
- [Principles of Chaos Engineering](https://principlesofchaos.org/)
- [Gremlin Chaos Engineering](https://www.gremlin.com/)
- [AWS Fault Injection Simulator](https://aws.amazon.com/fis/)

### Failure Mode Classifications (Google SRE)

```
Load-Induced (Stage 11-13)
├── Traffic Spike
├── Resource Exhaustion
└── Cascade Failure

State-Induced (Stage 16-17)
├── Lock Contention
├── Stale Cache
├── Transaction Deadlock
└── Race Condition

Event-Induced (Stage 18-20)
├── Out-of-Order Events
├── Delayed Callbacks
├── Duplicate Events
└── Missing Events

Self-Inflicted (Stage 21-22)
├── Retry Storm
├── False Positive Circuit Break
├── Backoff Misconfiguration
└── Rate Limit Self-DDoS
```

### Payment System Testing (Industry Standard)

| Scenario | Description | Stage |
|----------|-------------|-------|
| Delayed Webhook | Payment success, notification 10min late | Stage 20 |
| Duplicate Webhook | Same payment webhook sent twice | Stage 14 (covered) |
| Out-of-Order | Cancel arrives before success | Stage 20 |
| Partial Failure | Card approved, capture failed | Stage 18 |

### Core Principles

> "A Self-Healing system is not about pass/fail functional testing,
> but about detect/act/result situational testing."

- Single-method testing cannot validate healing system fundamentals
- "Bomb tests" only check "alive or dead"
- Self-Healing tests check "doesn't die, recovers if it does"

> "Load breaks systems predictably.
> Timing breaks systems silently after deployment."

---

## Next Steps

1. **Stage 20 Implementation** - Delayed Webhook Out-of-Order Test
2. **Stage 16 Implementation** - DB Lock / Deadlock Recovery Test
3. **Stage 21 Implementation** - False Positive Detection Test
4. **Stage 12 Implementation** - Spike & Recovery Test
5. **config.yaml Update** - Add new profiles
6. **CI/CD Integration** - Auto-run self_healing_quick profile

---

*Created: 2025-12-09*
*Last Updated: 2025-12-09*
*Author: Self-Healing Load Test Analysis*
