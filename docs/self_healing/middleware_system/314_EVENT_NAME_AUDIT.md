# 312 Event Name Convention Audit & Prometheus Cardinality Analysis

> **Date**: 2026-03-07
> **Related**: `312_EXCEPTION_HIERARCHY_LOGGING_STANDARDIZATION.md` §2.2, `docs/laws/LOGGING_STANDARDS.md` §1
> **Trigger**: Code review findings #1 (regex rejects valid names) and #4 (unbounded Prometheus label cardinality)

---

## 1. Executive Summary

The `event_name_validator` processor introduced in 312 (Q5) enforces the pattern `^[a-z_]+\.[a-z_]+$`.
A full-codebase audit reveals **82 out of 794 unique event names (10.3%) violate this pattern**,
totaling **150 out of 919 log call sites (16.3%)**. This means:

- **Strict mode** (`SELFHEALING_STRICT_LOG_VALIDATION=true`) cannot be safely enabled until migration
- **Production mode** will generate 82 distinct Prometheus label values on `selfhealing_log_convention_violations_total`

Neither issue is critical today, but both must be addressed before the validator delivers its intended value.

---

## 2. Audit Results

### 2.1 Overall Compliance

| Metric | Count | Percentage |
|--------|-------|------------|
| Total unique event names | 794 | 100% |
| Valid (match pattern) | 712 | 89.7% |
| **Violations** | **82** | **10.3%** |
| Total log call sites | 919 | 100% |
| Valid call sites | 769 | 83.7% |
| **Violating call sites** | **150** | **16.3%** |

### 2.2 Violation Categories

| Category | Unique Names | Description | Example |
|----------|-------------|-------------|---------|
| No dot (single word) | 32 | Missing `component.` prefix | `"started"`, `"scheduler"`, `"watchdog"` |
| Non-ASCII (Korean) | 25 | Korean characters in event name | `"dlq_consumer.소비_루프_시작"` |
| Multi-dot (triple segment) | 9 | `component.entity.action` instead of `component.entity_action` | `"primitive.config_set.failed"` |
| Has digits | 6 | Digit characters not matched by `[a-z_]` | `"watchdog.redis_recovery_stage1"` |
| Spaces/brackets/special | 6 | Human-readable sentences as event names | `"Config changed, cache invalidated"` |
| Format strings (`%s`/`{}`) | 4 | Dynamic format patterns | `"Guard '%s' failed (fail-open): %s"` |

### 2.3 Violation by Module

Top modules ranked by number of violating call sites:

| Module | Violating Sites | Primary Issue |
|--------|----------------|---------------|
| `services/chaos/base/experiment.py` | 9 | Single-word `"chaos"` |
| `multiregion/failover.py` | 10 | Single-word `"failover"`, `"failover_started"` etc. |
| `services/runbook/primitives.py` | 9 | Triple-dot `"primitive.X.Y"` |
| `coordination/scheduler.py` | 5 | Single-word `"scheduler"` |
| `adapters/kafka/consumer.py` | 5 | Korean characters |
| `coordination/dlq_consumer.py` | 5 | Korean characters |
| `coordination/shutdown_integration.py` | 4 | Korean characters |
| `core/runtime_feedback.py` | 4 | Single-word `"started"`, `"stopped"` etc. |
| `services/coordination/recovery_coordinator/` | 5 | Single-word `"recovery"` |
| `audit/wal/_disk_manager.py` | 3 | Single-word `"wal"` |

### 2.4 Detailed Violation Inventory — No Dot (Single Word)

These are the most common violations. The event name has no `component.` prefix:

```
available          (3 sites: audit/integrity, services/error_budget, services/postmortem)
chaos              (9 sites: services/chaos/base/experiment.py)
compliance         (1 site:  services/compliance/service.py)
consumer           (1 site:  adapters/audit/kafka_consumer.py)
coordinator        (2 sites: services/coordination/coordinator.py)
disabled           (2 sites: scaling/rate_controller.py, services/event_bus)
enabled            (1 site:  services/event_bus)
error              (1 site:  services/execution_services/chaos_service.py)
failover           (7 sites: multiregion/failover.py)
failover_disabled  (1 site:  multiregion/failover.py)
failover_started   (1 site:  multiregion/failover.py)
failover_stopped   (1 site:  multiregion/failover.py)
governance         (5 sites: services/governance/)
initialized        (6 sites: core/runtime_feedback, services/circuit_breaker, etc.)
metrics            (3 sites: metrics/prometheus.py, celery_tasks/metrics_tasks.py)
migration          (2 sites: services/postmortem/revision.py)
provider           (2 sites: services/error_budget/provider.py)
quorum             (2 sites: multiregion/quorum.py)
reauth             (1 site:  api/django/reauthentication.py)
reconciler         (1 site:  audit/integrity/reconciler.py)
recovery           (5 sites: services/coordination/recovery_coordinator/)
resumed            (1 site:  core/runtime_feedback.py)
scheduler          (5 sites: coordination/scheduler.py)
snapshot           (1 site:  metrics/snapshot_storage.py)
started            (8 sites: audit/reconciler, scaling/, multiregion/ etc.)
stopped            (8 sites: audit/reconciler, scaling/, multiregion/ etc.)
wal                (3 sites: audit/wal/_disk_manager.py)
watchdog           (4 sites: tasks/canary_watchdog.py, api/django/, services/chaos/)
```

### 2.5 Detailed Violation Inventory — Triple Dot

`services/runbook/primitives.py` uses `component.entity.action` format:

```
primitive.assert_metric.failed
primitive.config_set.failed
primitive.emergency_activate.failed
primitive.emergency_deactivate.failed
primitive.notify_send.failed
primitive.query_metric.unavailable
primitive.recovery_start.failed
primitive.recovery_start.rejected
primitive.wait_stabilize.failed
```

These are semantically well-structured but use two dots instead of one.
**Fix**: Convert to `primitive.config_set_failed` (underscore instead of second dot).

### 2.6 Detailed Violation Inventory — Has Digits

```
k8s_ingress_traffic_router.kubernetes_package_installed
s3_worm_backend.cannot_place_legal_hold
s3_worm_backend.enable_called_configured_interface
s3_worm_backend.initialized_interface_only_enable
watchdog.redis_recovery_stage1
watchdog.redis_recovery_stage1_succeeded
```

These follow the convention structurally (one dot, lowercase) but contain digits.
The regex `[a-z_]` rejects them. `k8s` and `s3` are industry-standard abbreviations.

---

## 3. Prometheus Cardinality Analysis

### 3.1 Current Implementation

```python
# settings/log_processors.py:112
counter.labels(event_name=event_name).inc()
```

Each distinct violating event name creates a unique Prometheus time series:
`selfhealing_log_convention_violations_total{event_name="<value>"}`

### 3.2 Cardinality Assessment

| Scenario | Distinct Labels | Time Series | Risk Level |
|----------|----------------|-------------|------------|
| Current code (pre-migration) | 82 | 82 | **Low** — bounded by hardcoded call sites |
| Post-migration (ideal) | 0 | 0 | None |
| Worst case with dynamic names | Unbounded | Unbounded | **High** — if convention is violated by dynamic f-strings |

**Current risk is LOW** because:
1. All 82 violating names are **string literals** hardcoded in source
2. LOGGING_STANDARDS.md §1.2 prohibits dynamic event names (`logger.debug(variable)`)
3. 82 time series is well within Prometheus safe thresholds (<10K per metric)

**Future risk is MEDIUM** because:
1. No code-level enforcement prevents `logger.info(f"task_{task_id}.completed")`
2. Third-party structlog loggers in the same process would also be validated
3. Once invalid names are migrated, any new violations are bugs — a single `event_name` label
   per violation would suffice rather than recording the full name

### 3.3 Cardinality Mitigation Options

| Option | Approach | Pros | Cons |
|--------|----------|------|------|
| A. Keep as-is | No change | Simple, 82 labels is safe | No protection against future unbounded growth |
| B. Cap labels | Track only first N distinct names, then use `"_other"` | Bounded cardinality | Loses detail after cap |
| C. Remove label | `counter.inc()` (no label) | Zero cardinality risk | Lose per-event visibility |
| D. Hash + truncate | `counter.labels(event_name=name[:50])` | Bounded by max length | Still potentially many labels |
| **E. Fixed bucket** | `counter.labels(violation_type="no_dot\|multi_dot\|non_ascii\|digits\|other")` | 5 fixed labels, meaningful | Must categorize in code |

**Recommendation: Option A now, Option E after migration.**

Pre-migration, 82 labels are safe and provide useful visibility into which violations exist.
Post-migration (when violations should be near-zero), switch to Option E to prevent future unbounded growth
while still distinguishing violation types.

---

## 4. Regex Pattern Revision Proposal

### 4.1 Current Pattern

```python
_EVENT_NAME_PATTERN = re.compile(r"^[a-z_]+\.[a-z_]+$")
```

### 4.2 Issues

1. **Rejects digits**: `k8s_ingress`, `s3_worm`, `stage1` — industry-standard identifiers
2. **Rejects multi-segment**: `primitive.config_set.failed` — semantically valid but triple-dot
3. **Allows leading underscore**: `_private.event_name` — likely unintended
4. **Allows double underscore**: `registry.__internal` — likely unintended

### 4.3 Proposed Revised Pattern

```python
_EVENT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
```

Changes:
- **Allow digits** (`0-9`) after the first character of each segment
- **Require alphabetic start** (`[a-z]`) for each segment — prevents `_foo.bar` or `123.abc`
- **Keep single dot** — triple-dot names should use underscore (`primitive.config_set_failed`)

### 4.4 Impact of Revised Pattern

| Category | Current (82 violations) | After regex fix | Remaining |
|----------|------------------------|-----------------|-----------|
| Has digits | 6 | 0 (fixed by regex) | 0 |
| No dot | 32 | 32 (still invalid) | 32 |
| Non-ASCII | 25 | 25 (still invalid) | 25 |
| Multi-dot | 9 | 9 (still invalid) | 9 |
| Spaces/special | 6 | 6 (still invalid) | 6 |
| Format strings | 4 | 4 (still invalid) | 4 |
| **Total** | **82** | **76** | **76** |

The regex fix only resolves 6 violations. The remaining 76 require code changes.

---

## 5. Migration Plan

### 5.1 Priority Tiers

| Tier | Category | Count | Effort | Approach |
|------|----------|-------|--------|----------|
| **P0** | Regex fix (digits) | 6 | Trivial | Change `_EVENT_NAME_PATTERN` only |
| **P1** | No-dot (single word) | 32 (91 sites) | Medium | Add `component.` prefix to each call |
| **P2** | Multi-dot (triple segment) | 9 | Easy | Replace second `.` with `_` |
| **P3** | Non-ASCII (Korean) | 25 | Medium | Translate to English event names |
| **P4** | Sentences/format strings | 10 | Easy | Replace with structured event names |

### 5.2 P1 Migration Template (Single Word → Prefixed)

```python
# Before
logger.info("started")
logger.info("stopped")
logger.info("initialized")

# After — use the module/component as prefix
logger.info("rate_controller.started")
logger.info("rate_controller.stopped")
logger.info("runtime_feedback.initialized")
```

### 5.3 P2 Migration Template (Triple Dot → Single Dot)

```python
# Before
logger.error("primitive.config_set.failed", error=str(exc))

# After — merge last two segments with underscore
logger.error("primitive.config_set_failed", error=str(exc))
```

### 5.4 P3 Migration Template (Korean → English)

```python
# Before
logger.info("dlq_consumer.소비_루프_시작")

# After
logger.info("dlq_consumer.consume_loop_started")
```

### 5.5 Test Impact

- `test_factory_logging_events.py`: `test_all_log_events_follow_convention` fixture reads `factory.py` only — unaffected
- `test_event_name_validator.py`: `test_invalid_event_names_do_not_match_pattern` needs update if regex changes (remove `"123.abc"` from invalid list if digits are allowed after first char)
- New test: verify all event names in codebase match convention (source-scan test)

---

## 6. Recommended Actions

### Immediate (this PR or follow-up)

1. **Update regex** to allow digits: `^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$`
   - File: `settings/log_processors.py:48`
   - Resolves 6 violations (P0)
   - Update test: `test_event_name_validator.py`

### Short-term (separate issue)

2. **Migrate P1 + P2 violations** (41 unique names, ~100 call sites)
   - Single-word events: add component prefix
   - Triple-dot events: merge to single dot
   - Update affected tests and monitoring dashboards

3. **Migrate P3 + P4 violations** (35 unique names, ~50 call sites)
   - Korean event names: translate to English
   - Sentence-style events: convert to structured names

### Medium-term

4. **Add source-scan test** — CI test that greps all `logger.*("` calls and validates against pattern
   - Prevents regression after migration
   - More reliable than runtime validation for catching violations early

5. **Switch Prometheus counter to fixed buckets** (Option E) after migration completes
   - `counter.labels(violation_type="...")` with 5 fixed categories
   - Prevents unbounded cardinality from future violations

---

## 7. Decision Log

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Allow digits in regex | `k8s`, `s3`, `stage1` are industry standard; rejecting them creates unnecessary friction |
| D2 | Keep single-dot requirement | Two dots create ambiguity between `component.entity.action` and nested components; underscore is sufficient |
| D3 | Keep Prometheus label as-is for now | 82 labels is safe; provides useful visibility during migration period |
| D4 | Do NOT enable strict mode until P1-P4 complete | 150 call sites would raise ValueError, breaking all log output |
| D5 | Add source-scan CI test after migration | Static analysis catches violations at build time, more reliable than runtime |
