# CLAUDE.md — SelfHealing Project Instructions

## Project Overview

Enterprise-grade Self-Healing framework built on Django 5.2 + DRF + PostgreSQL 15 + Redis 7 + Celery + Kafka + Prometheus + OTEL + K8s.

- **Core package**: `packages/selfhealing-python/src/selfhealing/`
- **Testbed**: `shopping/` (orders/payments/points) — a demo app for integration testing. Do NOT use as basis for architectural decisions.

## Module Structure

| Module | Responsibility |
|--------|---------------|
| `core/` | Backoff, Circuit Breaker state, execution engine, TLS, pool monitor, Graceful Shutdown |
| `services/` | 60+ services — CB, DLQ, Replay, Retry, Saga, Chaos, Governance, Canary, error budget, forensics |
| `adapters/` | 23 adapters — Django/Redis/Celery/Kafka/Postgres/Memory/Cache, etc. |
| `interfaces/` | Repository/Cache/Queue/Framework abstract interfaces |
| `audit/` | GDPR/CCPA-compliant audit logging, Hash Chain, WAL, multi-backend |
| `coordination/` | Redis/etcd leader election, DLQ Consumer coordination |
| `multiregion/` | Active-Active, CRDT, failover, Quorum Witness |
| `metrics/` | Prometheus metrics, drift detection, Reliability Manager |
| `scaling/` | Token Bucket, Load Shedding, HPA metrics, Graceful Degradation |
| `meta/` | Meta-Watchdog — self-monitoring |
| `resilience/` | Bulkhead, Hedging, Policy Composer |
| `settings/` | 50+ Pydantic settings (SELFHEALING_ env vars) |
| `factory.py` | ProviderRegistry — central adapter registry |
| `observability/` | OpenTelemetry initialization |

## Code Rules

- **No guessing**: If uncertain, read existing code first
- **Consistency**: Follow existing naming conventions, import styles, and error handling patterns
- **Code over docs**: When docs/ and code conflict, code is the source of truth
- **Duplication check**: Before implementing new features, check `services/`, `core/`, `adapters/` for existing similar implementations
- **Code citation**: Reference relevant file paths and code snippets as `filepath:line_number`

## Restrictions

- Never answer with generalities without reading actual code
- Do not use `shopping/` app as basis for architectural decisions (it is a testbed)
- Do not suggest refactoring unless the user explicitly requests it
- Do not write line numbers in code comments
- Do not use document-reference terms (`phase`, `reference`, etc.) in class/function/file names

## Key Reference Documents

- `docs/laws/UNIT_TEST_GUIDELINES.md` — unit test rules and verification techniques
- `docs/laws/INTEGRATION_TEST_GUIDELINES.md` — integration test rules and infra markers
- `docs/laws/LOGGING_STANDARDS.md` — logging event name conventions, exception chaining rules, log level guidelines
- `docs/self_healing/FEATURE_CATALOG.md` — authoritative feature list (38 features, tier classification, settings mapping). When features are added, changed, or removed, update this catalog.
- `docs/self_healing/` — implementation plan documents (feature specs and design docs)

## Test Location Rules

- Pure unit tests for `packages/selfhealing-python/` code → `packages/selfhealing-python/tests/unit/`
- Global `tests/` folder is for integration/infra tests only
- `services/`, `core/`, `audit/` folders under `packages/selfhealing-python/tests/` are legacy — place new tests under `unit/`

## Custom Skills

| Skill | Description |
|-------|-------------|
| `/advisor` | Project Q&A, idea evaluation, and counter-proposals |
| `/execute` | Read implementation plan document and implement code |
| `/test` | Write unit/integration tests for specified target |
| `/review` | Checklist-based code review (quality, design, security, etc.) |
| `/verify` | Verify consistency between document, code, and tests, then resolve discrepancies |
