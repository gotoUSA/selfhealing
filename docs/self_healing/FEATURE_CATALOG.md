# Self-Healing Feature Catalog

> **Total: 38 features** (OSS 10 / PRO 14 / ENT 14) + 5 tier upgrades
>
> Each feature passed at least 2 of 3 criteria: independent activation, customer value perception, standalone operation.
> For tier classification rationale, see `memory/commercialization-strategy-2026-03-10.md` §4.

---

## Tier Classification Criteria

Features are classified into tiers based on three axes:

| Axis | OSS | PRO | ENT |
|------|-----|-----|-----|
| Operational Maturity | Reactive — respond to failures | Proactive — prevent failures, optimize | Autonomous — self-operate at scale |
| Infrastructure Requirement | Redis (or in-memory fallback) | + Celery, Prometheus | + Kafka, etcd, Multi-cluster |
| Feature Nature | Failure response basics | Prevention, optimization, visibility | Compliance, scale, automation |

A feature belongs to the **lowest tier where all three axes align**. When axes conflict, infrastructure requirement is the tiebreaker (higher infra = higher tier).

---

## Categories

| Category | Description |
|----------|-------------|
| Resilience | Failure isolation and recovery patterns |
| Recovery | Failed operation preservation and retry |
| Observability | Monitoring, metrics, audit, and reporting |
| Operations | System control, deployment, and automation |
| Intelligence | ML-based prediction and auto-optimization |
| Security & Compliance | Security enforcement and regulatory compliance |
| Scaling | Traffic management and auto-scaling |
| Configuration | Runtime config management and simulation |
| Multi-Region | Cross-region replication and failover |

---

## OSS Tier (10 features) — Reactive Failure Handling

Minimum infrastructure: Redis (or in-memory fallback)

### 1. Circuit Breaker

- **Tier**: OSS | **Category**: Resilience | **Module**: `services/circuit_breaker/`
- **What**: Detects consecutive failures and automatically blocks requests to prevent cascading failure across services. Supports half-open state for gradual traffic recovery, rate limit cascade detection (auto-open on 429 storms), self-DDoS protection, and adaptive threshold linking to Emergency Level. Operators can manually force-open or force-close circuits.
- **Key Config**: `SELFHEALING_CIRCUIT_BREAKER_ENABLED`, `SELFHEALING_CB_FAILURE_THRESHOLD`, `SELFHEALING_CB_RECOVERY_TIMEOUT`
- **ENT Upgrade**: + Circuit Mesh (multi-service CB coordination)
- **See also**: `docs/self_healing/03_CIRCUIT_BREAKER.md`

### 2. DLQ + Replay

- **Tier**: OSS | **Category**: Recovery | **Module**: `services/dlq/`, `services/replay_service/`
- **What**: Captures failed operations into a Dead Letter Queue with full forensic context (stack trace, request data, failure reason) so no work is lost. Provides manual replay, batch replay by failure type, and conditional replay when circuit breaker recovers. Uses Repository pattern for pluggable storage backends.
- **Key Config**: `SELFHEALING_DLQ_ENABLED`, `SELFHEALING_DLQ_MAX_RETRY_COUNT`, `SELFHEALING_REPLAY_AUTOMATION_ENABLED`
- **Includes**: Adaptive Replay (dynamic batch sizing based on success rate)
- **See also**: `docs/self_healing/04_DEAD_LETTER_QUEUE.md`, `docs/self_healing/06_REPLAY_SYSTEM.md`

### 3. Retry (w/ Backoff)

- **Tier**: OSS | **Category**: Resilience | **Module**: `services/retry_handler/`, `services/backoff_calculator/`
- **What**: Reusable retry mechanism with configurable max attempts and multiple backoff strategies (exponential, linear, constant, decorrelated jitter). Includes idempotency checking before each attempt, DLQ routing on exhaustion, forensic context capture, and rate limit awareness to prevent self-DDoS during retry storms.
- **Key Config**: `SELFHEALING_RETRY_MAX_RETRIES`, `SELFHEALING_RETRY_MAX_DELAY`, `SELFHEALING_BACKOFF_STRATEGY`
- **Includes**: Backoff Calculator (exponential, linear, constant, decorrelated jitter strategies)
- **See also**: `docs/self_healing/05_RETRY_BACKOFF.md`

### 4. Idempotency

- **Tier**: OSS | **Category**: Resilience | **Module**: `services/idempotency/`
- **What**: Manages idempotency keys to ensure retried operations produce exactly the same result as the original execution. Prevents duplicate side effects across failure boundaries with domain-scoped key management and anti-flapping window to avoid rapid state oscillations.
- **Key Config**: `SELFHEALING_IDEMPOTENCY_ENABLED`, `SELFHEALING_IDEMPOTENCY_TTL`, `SELFHEALING_ANTI_FLAPPING_WINDOW`

### 5. Metrics (basic)

- **Tier**: OSS | **Category**: Observability | **Module**: `metrics/`
- **What**: Prometheus metrics collection and export for the self-healing layer with 100+ metric types covering DLQ depth, retry rates, circuit breaker state transitions, and replay success rates. Includes event handlers that automatically record metrics on service events and decorator-based instrumentation (`@track_counter`, `@track_execution_time`).
- **Key Config**: `SELFHEALING_METRICS_ENABLED`
- **See also**: `docs/self_healing/08_OBSERVABILITY.md`, `docs/self_healing/14_METRIC_COLLECTION_CORE.md`

### 6. Audit (basic, file)

- **Tier**: OSS | **Category**: Security & Compliance | **Module**: `audit/`
- **What**: GDPR/CCPA-compliant audit logging for all configuration changes and healing decisions. Includes privacy-safe IP masking (hashed), trace ID correlation for distributed tracing, and pluggable storage backends (file-based in OSS). Records who changed what, when, and why with tamper-evident formatting.
- **Key Config**: `SELFHEALING_AUDIT_ENABLED`, `SELFHEALING_AUDIT_BACKEND`
- **ENT Upgrade**: + WORM storage, Hash Chain integrity, Signed Manifest (RFC 3161)
- **See also**: `docs/self_healing/15_METRIC_COLLECTION_ADVANCED.md`

### 7. Health Check

- **Tier**: OSS | **Category**: Operations | **Module**: `services/health_check.py`
- **What**: L3 self-healing health endpoint providing structured health status for Kubernetes liveness and readiness probes. Checks DB connectivity, connection pool status, and self-healing subsystem health. Returns latency metrics and detailed error information for each checked component.
- **Key Config**: Exposed via `HealthBridgeMiddleware` at `/health/l3`
- **Settings Gap**: No dedicated settings file — configured via middleware registration

### 8. Graceful Shutdown

- **Tier**: OSS | **Category**: Operations | **Module**: `core/shutdown_coordinator.py`
- **What**: Coordinates zero-downtime shutdown across all self-healing subsystems during deployment or scaling events. Tracks in-flight requests, drains queues, and ensures no operations are lost during pod termination. Integrates with Kubernetes SIGTERM handling and pre-stop hooks.
- **Key Config**: Configured via `core/shutdown_coordinator.py` (no dedicated settings file)
- **Settings Gap**: No dedicated settings file — shutdown timeout/drain params are hardcoded in `core/shutdown_coordinator.py`

### 9. System Control

- **Tier**: OSS | **Category**: Operations | **Module**: `services/system_control.py`
- **What**: Global kill switch for the entire self-healing system with thread-safe operations. Supports pluggable state backends (File, Redis, Memory) enabling multi-server state sharing via Redis. Allows operators to instantly disable all automation during incidents and automatically restores state on server restart.
- **Key Config**: State persisted in configured backend
- **Settings Gap**: No dedicated settings file — backend selection and state config are service-internal

### 10. Emergency Mode

- **Tier**: OSS | **Category**: Operations | **Module**: `services/emergency_mode/`
- **What**: Step-wise graceful degradation manager with 4 levels (NORMAL → LEVEL_1 → LEVEL_2 → LEVEL_3). Each level progressively sheds non-critical operations to preserve core functionality under stress. Includes RecoveryGate for controlled exit from emergency state, preventing premature recovery that could cause re-failure.
- **Key Config**: Emergency level thresholds configured in service (no dedicated settings file; ENT namespace settings in `settings/namespace_emergency.py`)
- **Settings Gap**: No dedicated `settings/emergency.py` — level thresholds are service-internal (ENT namespace variant has `settings/namespace_emergency.py`)
- **ENT Upgrade**: + Namespace Emergency (region-level isolation)

---

## PRO Tier (14 features) — Proactive Prevention & Intelligence

Minimum infrastructure: Redis + Celery + Prometheus (recommended)

### 11. Bulkhead

- **Tier**: PRO | **Category**: Resilience | **Module**: `resilience/bulkhead/`
- **What**: Resource isolation to prevent cascading failures across service boundaries. Provides three isolation strategies: SemaphoreBulkhead (I/O bound), ThreadPoolBulkhead (CPU bound), and AsyncSemaphoreBulkhead (async workloads). Includes BulkheadRegistry for domain-based management and `@bulkhead` decorator for easy annotation.
- **Key Config**: `SELFHEALING_BULKHEAD_ENABLED`, `SELFHEALING_BULKHEAD_MAX_CONCURRENT`, `SELFHEALING_BULKHEAD_MAX_WAIT`

### 12. Hedging

- **Tier**: PRO | **Category**: Resilience | **Module**: `resilience/policies/hedging.py`
- **What**: Tail latency reduction via parallel competitive execution. Sends identical requests to multiple candidates simultaneously and adopts the fastest response, significantly reducing P99 latency. Includes backpressure logic that adjusts hedging behavior based on system load to prevent resource waste under stress.
- **Key Config**: `SELFHEALING_HEDGING_ENABLED`, `SELFHEALING_HEDGING_MAX_PARALLEL`, `SELFHEALING_HEDGING_DELAY`

### 13. Error Budget

- **Tier**: PRO | **Category**: Observability | **Module**: `services/error_budget/`
- **What**: SRE Error Budget calculator and deployment policy advisor based on SLO targets. Computes remaining error budget, fast/slow burn rates, and recommends deployment freezes when budget is low. Design principle: "system advises, humans decide" — all recommendations are advisory, not enforcement. Includes crisis multiplier and shadow budget for safe experimentation.
- **Key Config**: `SELFHEALING_ERROR_BUDGET_ENABLED`, `SELFHEALING_ERROR_BUDGET_SLO_TARGET`, `SELFHEALING_BURN_RATE_THRESHOLDS_*`
- **See also**: `docs/self_healing/12_ERROR_BUDGET.md`

### 14. Pool Monitor

- **Tier**: PRO | **Category**: Operations | **Module**: `core/pool_monitor.py`
- **What**: Database connection pool health monitoring with leak detection and exhaustion prediction. Continuously tracks active/available connections, wait queue length, and connection lifecycle. Reports health as HEALTHY/WARNING(70%+)/CRITICAL(90%+)/EXHAUSTED/LEAK_SUSPECTED with actionable alerts.
- **Key Config**: `SELFHEALING_POOL_MONITOR_ENABLED`, `SELFHEALING_POOL_MONITOR_INTERVAL`

### 15. Coordination

- **Tier**: PRO | **Category**: Operations | **Module**: `coordination/`
- **What**: Distributed leader election ensuring single-leader operations in multi-worker environments. Provides factory-based backend selection (Redis in PRO, etcd in ENT) with abstract LeaderElector interface. Includes DLQConsumerCoordinator to prevent duplicate DLQ processing and LeaderScheduler for distributed cron jobs.
- **Key Config**: `SELFHEALING_LEADER_ENABLED`, `SELFHEALING_LEADER_BACKEND`, `SELFHEALING_LEADER_TTL`
- **ENT Upgrade**: + etcd backend, distributed scheduler

### 16. Canary Recovery (w/ Rollback)

- **Tier**: PRO | **Category**: Operations | **Module**: `services/canary/`, `services/rollback/`
- **What**: Gradual configuration deployment with automatic rollback on degradation. Rolls out config changes through progressive stages with health validation at each step. Includes CanaryChaosGuard for conflict detection with running chaos experiments, cross-cluster notification, and safety interlocks. Rollback subsystem provides zero-downtime safe rollback with policy management.
- **Key Config**: `SELFHEALING_CANARY_ENABLED`, `SELFHEALING_CANARY_STAGES`, `SELFHEALING_CANARY_GOVERNANCE_*`
- **Includes**: Rollback Service (zero-downtime safe rollback with policy management)

### 17. Runtime Config (w/ History, Shadow)

- **Tier**: PRO | **Category**: Configuration | **Module**: `services/runtime_config/`, `services/config_history/`, `services/config_shadow/`
- **What**: Runtime configuration management allowing updates without server restart. Supports three apply strategies: IMMEDIATE, DELAYED (with cancellation), and GRACEFUL (waits for in-progress operations). Config History stores last N versions in Redis with version-specific rollback. Config Shadow simulates config changes by replaying past events to predict impact before applying.
- **Key Config**: `SELFHEALING_CONFIG_SHADOW_ENABLED`, `SELFHEALING_APPLY_STRATEGY`
- **Includes**: Config History (versioned change history with rollback), Config Shadow (pre-simulation via event replay)

### 18. Throttle (w/ Rate Limit Coordinator)

- **Tier**: PRO | **Category**: Scaling | **Module**: `services/throttle/`, `services/rate_limit_coordinator/`
- **What**: Framework-agnostic adaptive throttling using Netflix Gradient algorithm that dynamically adjusts admission rates based on real-time system load. Includes sliding window and token bucket implementations, circuit breaker bridge for integrated protection, DLQ routing for rejected requests, and recovery dampening to prevent load spikes during recovery. Rate Limit Coordinator prevents self-DDoS by coordinating retry behavior across workers.
- **Key Config**: `SELFHEALING_THROTTLE_ENABLED`, `SELFHEALING_THROTTLE_GRADIENT_*`
- **Includes**: Rate Limit Coordinator (distributed retry coordination for self-DDoS prevention)
- **ENT Upgrade**: + Distributed Rate Limit (Kafka-based cluster-wide 429 propagation)

### 19. Corruption Shield

- **Tier**: PRO | **Category**: Security & Compliance | **Module**: `services/corruption_shield/`
- **What**: Multi-layer data integrity protection with three defense levels. L1: Schema validation (syntax, format, types). L2: Business rule validation (logic, constraints, consistency). L3: Anomaly detection using Z-Score statistical analysis to catch outliers that pass structural validation.
- **Key Config**: `SELFHEALING_CORRUPTION_SHIELD_ENABLED`, `SELFHEALING_CORRUPTION_SHIELD_*`

### 20. Learning + Auto-Tuning

- **Tier**: PRO | **Category**: Intelligence | **Module**: `services/learning/`, `services/auto_tuning/`
- **What**: Self-learning system that recognizes failure patterns and automatically tunes recovery parameters. Learning service identifies recurring patterns and suggests optimizations. Auto-Tuning service tracks tuning sessions and applies parameter adjustments autonomously with recorded decision history for auditability.
- **Key Config**: `SELFHEALING_LEARNING_ENABLED`, `SELFHEALING_AUTO_TUNING_ENABLED`
- **Includes**: Auto-Tuning Service (autonomous parameter adjustment with decision recording), Decision Engine
- **Settings Gap**: No dedicated `settings/learning.py` or `settings/auto_tuning.py` — config is service-internal

### 21. Predictive Forecaster

- **Tier**: PRO | **Category**: Intelligence | **Module**: `services/predictive_forecaster/`
- **What**: Time-series anomaly forecasting engine that predicts threshold breaches 5-15 minutes ahead using Holt Linear, EWMA, and Holt-Winters models. Includes Z-Score and IQR anomaly detectors, spike classifier, and ProactiveActionTrigger that initiates preventive measures before failures occur.
- **Key Config**: `SELFHEALING_PREDICTIVE_FORECASTER_ENABLED`, `SELFHEALING_PREDICTIVE_FORECASTER_HORIZON`

### 22. Daily Report (w/ Dashboard)

- **Tier**: PRO | **Category**: Observability | **Module**: `services/daily_report/`, `services/dashboard_service/`
- **What**: Automated daily operational report generation with Slack and email formatting. Aggregates daily healing results including recovery counts, MTTR, success rates, and cost savings. Dashboard service provides centralized statistics API for real-time monitoring UIs with multi-tier caching and cache invalidation support.
- **Key Config**: `SELFHEALING_DAILY_REPORT_ENABLED`, `SELFHEALING_DASHBOARD_ENABLED`
- **Includes**: Dashboard Service (centralized statistics API with multi-tier caching)

### 23. Precomputed Cache

- **Tier**: PRO | **Category**: Scaling | **Module**: `services/precomputed_cache/`
- **What**: 3-tier cache optimization for L3 observability endpoints achieving sub-50ms overhead. L1: in-process TTLCache (2s TTL, 0ms). L2: Redis pre-computed JSON (15s TTL, 1-5ms). L3: direct compute fallback (50-200ms). Background worker pre-computes health, error budget, and pool status every 15 seconds with drift detection between cache tiers.
- **Key Config**: `SELFHEALING_PRECOMPUTED_CACHE_ENABLED`, `SELFHEALING_PRECOMPUTED_CACHE_INTERVAL`

### 24. ML Settings Recommendation

- **Tier**: PRO | **Category**: Intelligence | **Module**: *Under development*
- **What**: Machine learning-based configuration recommendation engine. Analyzes historical performance data and workload patterns to suggest optimal settings for retry limits, circuit breaker thresholds, backoff parameters, and other tunable values. Integrates with Config Shadow for safe recommendation validation before applying.
- **Key Config**: TBD
- **Status**: Planned. Infrastructure exists (AutoTuning, ConfigShadow, Learning) but ML algorithms not yet implemented.

---

## Enterprise Tier (14 features) — Autonomous Operation at Scale

Minimum infrastructure: Redis Cluster + Kafka + PostgreSQL + K8s + OTEL Collector

### 25. Chaos Engineering (w/ Blast Radius)

- **Tier**: ENT | **Category**: Operations | **Module**: `services/chaos/`, `services/blast_radius/`
- **What**: Enterprise-grade Continuous Resilience Validation engine with 5 core experiment types, automated scheduling via Celery Beat, and daily resilience reports. BlastRadiusManager controls experiment scope (INSTANCE/SERVICE/REGION) and SafetyGuard performs error budget pre-flight checks before execution. Unified ChaosEngine facade provides single entry point to all subsystems.
- **Key Config**: `SELFHEALING_CHAOS_ENABLED`, `SELFHEALING_CHAOS_BLAST_RADIUS_*`, `SELFHEALING_CHAOS_SAFETY_CAPS_*`
- **Includes**: Blast Radius Manager (scope control: INSTANCE/SERVICE/REGION), Safety Guard (error budget pre-flight), Chaos Scheduler
- **See also**: `docs/self_healing/13_CHAOS_ENGINEERING.md`

### 26. Governance

- **Tier**: ENT | **Category**: Security & Compliance | **Module**: `services/governance/`
- **What**: Emergency mode tracking and governance validation layer that enforces safety checks before any automated action. GovernanceCheckMixin provides decorator-based guard that blocks operations during active emergencies or when governance policies are violated. Includes TTL-based expiry management and API service for external governance queries.
- **Key Config**: `SELFHEALING_GOVERNANCE_ENABLED`, `SELFHEALING_GOVERNANCE_*`
- **See also**: `docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1.md`

### 27. Error Budget Gate

- **Tier**: ENT | **Category**: Operations | **Module**: `services/error_budget_gate/`
- **What**: Automation control gate that blocks all self-healing automation when error budget drops below threshold, forcing manual human intervention. Design philosophy: "crisis situations force human intervention." Fails open on gate failure (allows automation with warning). Provides `@automation_gate` decorator and `check_automation_allowed()` / `require_automation_allowed()` APIs.
- **Key Config**: `SELFHEALING_ERROR_BUDGET_GATE_ENABLED`, `SELFHEALING_ERROR_BUDGET_GATE_THRESHOLD`

### 28. Postmortem (w/ Correlation Engine)

- **Tier**: ENT | **Category**: Observability | **Module**: `services/postmortem/`, `services/correlation_engine/`
- **What**: Automated post-incident analysis system. IncidentGroupManager merges cascading circuit breaker events into correlated incident groups. NotificationAggregator prevents alert storms. IntegritySealer provides hash-chain-based postmortem integrity. Correlation Engine builds DAG from event bus events for root cause ranking and incident timeline reconstruction.
- **Key Config**: `SELFHEALING_POSTMORTEM_ENABLED`, `SELFHEALING_POSTMORTEM_*`
- **Includes**: Correlation Engine (DAG-based root cause ranking), Notification Aggregator (alert storm prevention), Integrity Sealer (hash-chain postmortem sealing)

### 29. FinOps

- **Tier**: ENT | **Category**: Observability | **Module**: `services/finops/`
- **What**: Recovery cost tracking and budget management for financial accountability. Tracks cost per healing operation, manages cost budgets with alerts on budget threshold breaches, and generates cost efficiency reports. Provides CFO dashboard data for executive visibility into self-healing ROI.
- **Key Config**: `SELFHEALING_FINOPS_ENABLED`
- **Settings Gap**: No dedicated `settings/finops.py` — cost rates and budget thresholds are service-internal

### 30. Scaling / HPA

- **Tier**: ENT | **Category**: Scaling | **Module**: `scaling/`
- **What**: Kubernetes auto-scaling and backpressure integration. RateController uses Token Bucket + AIMD for dynamic processing rate adjustment. TrafficGate integrates rate control with load shedding by request priority. GracefulDegradation provides step-wise feature disabling under load. HPAMetricsExporter publishes custom metrics for K8s Horizontal Pod Autoscaler.
- **Key Config**: `SELFHEALING_BACKPRESSURE_*`, `SELFHEALING_GRACEFUL_DEGRADATION_*`

### 31. Saga

- **Tier**: ENT | **Category**: Recovery | **Module**: `services/saga/`
- **What**: Distributed saga orchestrator implementing forward/compensate pattern for multi-step transactions spanning multiple services. Uses Redis for state management with Lua-scripted atomic transitions and CAS (Compare-And-Swap). Includes SagaRegistry for definition management, Celery task integration for async execution, and orphan saga detection.
- **Key Config**: `SELFHEALING_SAGA_ENABLED`
- **Settings Gap**: No dedicated `settings/saga.py` — timeout/retry/concurrency params are service-internal

### 32. Multi-Region (w/ Cell Topology, Isolation Gate)

- **Tier**: ENT | **Category**: Multi-Region | **Module**: `multiregion/`, `services/cell_topology/`, `services/isolation/`
- **What**: Active-Active multi-region architecture enabling service continuity even if an entire region fails. RegionReplicator handles cross-region data sync. ConflictResolver supports CRDT (G-Counter, LWW Register) and Last-Write-Wins strategies. RegionFailover provides automatic failover with QuorumWitness for split-brain prevention. Cell Topology adds logical traffic isolation via consistent hash ring. Isolation Gate controls region-level traffic flow.
- **Key Config**: Multi-region settings in `multiregion/config.py`
- **Includes**: Cell Topology (logical traffic isolation via consistent hash ring), Isolation Gate (region-level traffic control), Quorum Witness (split-brain prevention)

### 33. Meta-Watchdog

- **Tier**: ENT | **Category**: Operations | **Module**: `meta/`
- **What**: Self-monitoring system for the self-healing infrastructure itself. HealthProbeManager collects health status from all subsystems. StuckDetector identifies zero-variance metrics indicating frozen components. EscalationManager triggers human intervention via PagerDuty or Slack when automated recovery fails. "Who watches the watchman?" — this does.
- **Key Config**: `SELFHEALING_META_WATCHDOG_ENABLED`, `SELFHEALING_META_*`

### 34. Compliance

- **Tier**: ENT | **Category**: Security & Compliance | **Module**: `services/compliance/`
- **What**: Regulatory compliance automation supporting DORA and PCI-DSS standards. Tracks compliance status across services, generates audit reports for compliance officers, and detects regulation violations. Provides ComplianceCheck and ComplianceReport models for standardized compliance assessment.
- **Key Config**: `SELFHEALING_COMPLIANCE_ENABLED`
- **Settings Gap**: No dedicated `settings/compliance.py` — regulation rules and check intervals are service-internal

### 35. Security (w/ Security Notification)

- **Tier**: ENT | **Category**: Security & Compliance | **Module**: `services/security/`, `services/security_notification/`
- **What**: Security violation handling that NEVER self-heals — violations are immediately blocked and routed to security team. SecurityViolationService detects and classifies violations by type and severity. ProtectionOrchestrator takes immediate protective actions with ActionPolicy-based rollback support. Security Notification delivers alerts across multiple channels (Slack, Email, SMS, PagerDuty).
- **Key Config**: `SELFHEALING_SECURITY_ENABLED`, `SELFHEALING_SECURITY_*`
- **Includes**: Security Notification (multi-channel alert delivery: Slack, Email, SMS, PagerDuty)

### 36. Runbook

- **Tier**: ENT | **Category**: Operations | **Module**: `services/runbook/`
- **What**: Automated runbook executor providing declarative orchestration of recovery procedures. Pattern matcher maps current symptoms to runbook trigger conditions. Step-by-step execution with mid-flight validation, compensation contracts, and approval gates (risk-based automatic or manual). Playback recorder enables execution replay for post-incident review.
- **Key Config**: `SELFHEALING_RUNBOOK_ENABLED`, `SELFHEALING_RUNBOOK_*`

### 37. Capacity Reservation

- **Tier**: ENT | **Category**: Scaling | **Module**: `services/capacity_reservation/`
- **What**: Pre-emptive capacity allocation based on scheduled events (flash sales, coupon opens, marketing campaigns). EventCalendar registers upcoming events; PreWarmer adjusts existing modules (RateController, PoolWatchdog, Bulkhead, GracefulDegradation) N minutes before event start. Includes warm-up/cool-down orchestration with safety valve metrics.
- **Key Config**: `SELFHEALING_CAPACITY_RESERVATION_ENABLED`

### 38. Unified Notification

- **Tier**: ENT | **Category**: Operations | **Module**: `services/unified_notification/`
- **What**: Central notification hub consolidating all alert sources (AlertAdapter, SecurityNotification, GateAlertManager, GovernanceService) into a single point of control. Provides policy-based channel routing, rate limiting and cooldown to prevent notification fatigue, audit trail integration, and emergency level escalation. Convenience APIs: `notify()`, `notify_security()`, `notify_sla()`.
- **Key Config**: `SELFHEALING_NOTIFICATION_ENABLED`, `SELFHEALING_NOTIFICATION_CHANNEL_*`

---

## Tier Upgrades

These are not separate features but ENT-level enhancements of existing features:

| Base Feature | Base Tier | ENT Enhancement | Module |
|-------------|-----------|----------------|--------|
| Circuit Breaker | OSS | + Circuit Mesh: downstream CB state-based upstream threshold dynamic adjustment | `services/circuit_mesh/` |
| Emergency Mode | OSS | + Namespace Emergency: region-level emergency mode isolation with escalation audit | `services/namespace_emergency/` |
| Audit | OSS | + WORM/Hash Chain: tamper-proof integrity with Merkle tree + RFC 3161 timestamps | `audit/integrity/`, `audit/signed_manifest.py` |
| Coordination | PRO | + etcd backend + distributed scheduler + advanced recovery coordination | `coordination/etcd_elector.py`, `coordination/scheduler.py` |
| Throttle | PRO | + Distributed Rate Limit: Kafka-based cluster-wide 429 event propagation | `services/rate_limit/` |

---

## Cross-Reference: Feature → Settings

| # | Feature | Primary Settings File |
|---|---------|----------------------|
| 1 | Circuit Breaker | `settings/circuit_breaker.py`, `settings/circuit_breaker_advanced.py` |
| 2 | DLQ + Replay | `settings/dlq.py`, `settings/replay_automation.py` |
| 3 | Retry | `settings/retry.py`, `settings/backoff.py` |
| 4 | Idempotency | `settings/idempotency.py` |
| 5 | Metrics | `settings/metrics.py` |
| 6 | Audit | `settings/audit.py`, `settings/audit_integrity.py` |
| 7 | Health Check | (middleware config) |
| 8 | Graceful Shutdown | (service-internal, `core/shutdown_coordinator.py`) |
| 9 | System Control | (backend-specific) |
| 10 | Emergency Mode | (service-internal; ENT: `settings/namespace_emergency.py`) |
| 11 | Bulkhead | `settings/bulkhead.py` |
| 12 | Hedging | `settings/hedging.py` |
| 13 | Error Budget | `settings/error_budget.py` |
| 14 | Pool Monitor | `settings/pool_monitor.py` |
| 15 | Coordination | `settings/leader_election.py` |
| 16 | Canary Recovery | `settings/canary.py`, `settings/canary_governance.py` |
| 17 | Runtime Config | `settings/runtime_feedback.py`, `settings/config_shadow.py`, `settings/apply_strategy.py` |
| 18 | Throttle | `settings/throttle.py` |
| 19 | Corruption Shield | `settings/corruption_shield.py` |
| 20 | Learning + Auto-Tuning | (service-internal config) |
| 21 | Predictive Forecaster | `settings/predictive_forecaster.py` |
| 22 | Daily Report | `settings/daily_report.py`, `settings/dashboard.py` |
| 23 | Precomputed Cache | `settings/precomputed_cache.py` |
| 24 | ML Settings Recommendation | TBD |
| 25 | Chaos Engineering | `settings/chaos.py`, `settings/chaos_blast_radius.py` |
| 26 | Governance | `settings/governance.py` |
| 27 | Error Budget Gate | `settings/error_budget_gate.py` |
| 28 | Postmortem | `settings/postmortem.py` |
| 29 | FinOps | (service-internal config) |
| 30 | Scaling / HPA | `settings/backpressure.py`, `settings/graceful_degradation.py` |
| 31 | Saga | (service-internal config) |
| 32 | Multi-Region | `multiregion/config.py` |
| 33 | Meta-Watchdog | `settings/meta_watchdog.py` |
| 34 | Compliance | (service-internal config) |
| 35 | Security | `settings/security.py` |
| 36 | Runbook | `settings/runbook.py` |
| 37 | Capacity Reservation | `settings/capacity_reservation.py` |
| 38 | Unified Notification | `settings/notification.py` |

---

## Settings Gap Summary

8 features lack dedicated `settings/` files. Configuration is either hardcoded or managed inside the service module itself.

| # | Feature | Tier | Current State | Recommended Action |
|---|---------|------|---------------|--------------------|
| 7 | Health Check | OSS | Middleware registration config | Low priority — simple endpoint, few tunable params |
| 8 | Graceful Shutdown | OSS | Hardcoded in `core/shutdown_coordinator.py` | Medium — drain timeout, grace period should be env-configurable for K8s tuning |
| 9 | System Control | OSS | Backend selection is service-internal | Low — only 3 backends (File/Redis/Memory), rarely changes |
| 10 | Emergency Mode | OSS | Level thresholds in service code | Medium — threshold tuning per environment is common in production |
| 20 | Learning + Auto-Tuning | PRO | Config in service modules | Medium — learning rate, tuning bounds need per-deployment adjustment |
| 29 | FinOps | ENT | Cost rates/budgets in service code | High — cost rates MUST vary per customer/deployment |
| 31 | Saga | ENT | Timeout/retry params in service code | Medium — saga timeouts need env-specific tuning for cross-service latency |
| 34 | Compliance | ENT | Regulation rules in service code | High — regulation standards (DORA/PCI-DSS) and check schedules vary per customer |

**Priority**: FinOps (#29) and Compliance (#34) are highest priority because their configuration is inherently customer-specific. Graceful Shutdown (#8), Emergency Mode (#10), and Learning (#20) are medium priority for operational flexibility.
