# Self-Healing Test Onboarding Guide

> **Document Type**: Developer Onboarding & Quick Reference
> **Version**: 1.0
> **Last Updated**: 2025-12-08
> **Target Audience**: New Engineers, QA, DevOps

---

## Quick Start (5 Minutes)

### 1. Prerequisites

Ensure you have the following installed:

```bash
# Verify Docker is running
docker --version
docker-compose --version

# Verify Python environment
python --version  # Python 3.12+
```

### 2. Run All Tests via Docker Compose

```bash
# From project root (myproject/)
docker-compose up -d db redis

# Wait for services to be healthy
docker-compose ps

# Run tests
docker-compose run --rm web python -m pytest shopping/tests/ -v
```

### 3. Run Self-Healing Unit Tests Only (Fastest)

```bash
# Run Tier 1 tests (no external dependencies)
docker-compose run --rm web python -m pytest -m "tier1" -v

# Or run specific test file
docker-compose run --rm web python -m pytest shopping/tests/unit/self_healing/ -v
```

---

## Test Tier System

| Tier | Name | Trigger | Duration | Dependencies |
|------|------|---------|----------|--------------|
| **Tier 1** | Fast | Every PR | < 2 min | None |
| **Tier 2** | Integration | Merge to main | < 10 min | DB, Redis |
| **Tier 3** | Chaos | Nightly | < 30 min | DB, Redis, Celery |
| **Tier 4** | Load | Weekly/Release | < 2 hours | Full stack |

### Running Each Tier

```bash
# Tier 1: Fast unit tests (no Docker required)
python -m pytest -m "tier1" -v

# Tier 2: Integration tests (requires Docker)
docker-compose up -d db redis
docker-compose run --rm web python -m pytest -m "tier2" -v

# Tier 3: Chaos tests (requires full stack)
docker-compose up -d
docker-compose run --rm web python -m pytest -m "tier3_chaos" -v --timeout=1800

# Tier 4: Load tests (dedicated runner recommended)
docker-compose up -d
docker-compose run --rm web python -m pytest -m "tier4_load" -v --timeout=7200
```

---

## Directory Structure

```
shopping/tests/
├── conftest.py                          # Global fixtures
├── factories.py                         # Test factories
│
├── unit/
│   ├── self_healing/                    # ← NEW: Self-healing unit tests
│   │   ├── __init__.py
│   │   ├── test_backoff_policy.py       # Backoff calculation tests
│   │   ├── test_sla_timer_policy.py     # SLA threshold tests
│   │   ├── test_retry_decision_table.py # Retry logic tests
│   │   ├── test_failure_classification.py # Error classification
│   │   └── test_audit_record_schema.py  # Audit trail schema
│   │
│   ├── services/
│   │   ├── test_self_healing_cost_policy.py   # Cost-aware tests
│   │   └── test_self_healing_tenant_policy.py # Tenant isolation
│   │
│   └── test_self_healing_policy.py      # Existing policy tests
│
├── integration/
│   ├── self_healing/                    # Integration tests
│   └── chaos/                           # Chaos engineering
│
├── e2e/                                 # End-to-end tests
│
└── load/                                # Load/stress tests
```

---

## Test Naming Conventions

### File Naming
```
test_{domain}_{specific_area}.py
# Example: test_multi_tenancy_isolation.py
```

### Class Naming
```python
class Test{Feature}{Scenario}:
# Example: class TestMultiTenancyCircuitBreakerIsolation:
```

### Method Naming
```python
def test_{scenario}_{expected_behavior}(self):
# Example: def test_tenant_a_circuit_open_does_not_affect_tenant_b(self):
```

### Docstring Format (Required)
```python
"""
Purpose:
    {What this test validates}

Scenario:
    1. {Step 1}
    2. {Step 2}

Expected:
    - {Expected outcome}

Risk Covered:
    {Risk ID from Risk Matrix}

Compliance:
    {Relevant compliance control, if applicable}
"""
```

---

## Common Test Commands

### Development Workflow

```bash
# Run tests in watch mode (requires pytest-watch)
ptw shopping/tests/unit/self_healing/

# Run with verbose output
pytest shopping/tests/unit/self_healing/ -v --tb=long

# Run single test
pytest shopping/tests/unit/self_healing/test_backoff_policy.py::TestBackoffPolicyDefaults::test_default_base_is_4 -v

# Run with coverage
pytest shopping/tests/unit/self_healing/ --cov=shopping.services.self_healing --cov-report=html
```

### Debugging Failed Tests

```bash
# Show full traceback
pytest -v --tb=long

# Drop into debugger on failure
pytest --pdb

# Show local variables in traceback
pytest --showlocals

# Run failed tests from last run
pytest --lf

# Run only failed tests, then all
pytest --ff
```

### Filtering Tests

```bash
# By marker
pytest -m "tier1"
pytest -m "tier1 and not slow"
pytest -m "tenant_aware or cost_sensitive"

# By keyword in name
pytest -k "backoff"
pytest -k "sla and not load"

# Exclude markers
pytest -m "not tier4_load and not flaky"
```

---

## Understanding Test Markers

| Marker | Description | When to Use |
|--------|-------------|-------------|
| `@pytest.mark.tier1` | Fast unit tests | PR validation |
| `@pytest.mark.tier2` | Integration tests | Merge validation |
| `@pytest.mark.tier3_chaos` | Chaos tests | Nightly/Manual |
| `@pytest.mark.tier4_load` | Load tests | Release validation |
| `@pytest.mark.tenant_aware` | Multi-tenancy tests | Isolation verification |
| `@pytest.mark.cost_sensitive` | Cost-aware tests | Cost policy validation |
| `@pytest.mark.requires_redis` | Needs Redis | Skip if Redis unavailable |
| `@pytest.mark.requires_celery` | Needs Celery | Skip if Celery unavailable |
| `@pytest.mark.flaky` | Known flaky | Quarantined from CI |

### Adding Markers to Tests

```python
import pytest

@pytest.mark.tier1
@pytest.mark.tenant_aware
class TestTenantIsolation:
    def test_example(self):
        ...
```

---

## Risk Matrix Quick Reference

| Risk ID | Description | Test Files |
|---------|-------------|------------|
| R-001 | Cross-tenant data leakage | `test_multi_tenancy_isolation.py` |
| R-002 | Unbounded retry costs | `test_cost_aware_recovery.py` |
| R-006 | Unaccountable decisions | `test_audit_accountability.py` |
| R-012 | SLA breach undetected | `test_sla_timer_policy.py` |
| R-015 | Retry storm | `test_backoff_policy.py`, `test_retry_decision_table.py` |

---

## Docker Compose Testing

### Full Test Suite

```bash
# Start all services
docker-compose up -d

# Run complete test suite
docker-compose run --rm web python -m pytest shopping/tests/ -v \
  -m "not load_test and not manual and not heavy"

# View logs if tests fail
docker-compose logs web
docker-compose logs celery_worker
```

### Service-Specific Testing

```bash
# Test with only database
docker-compose up -d db
docker-compose run --rm web python -m pytest -m "requires_db and tier1" -v

# Test with database and Redis
docker-compose up -d db redis
docker-compose run --rm web python -m pytest -m "tier2" -v

# Full integration with Celery
docker-compose up -d
docker-compose run --rm web python -m pytest -m "requires_celery" -v
```

### Cleanup

```bash
# Stop all services
docker-compose down

# Stop and remove volumes (fresh database)
docker-compose down -v

# Rebuild images
docker-compose build --no-cache
```

---

## Troubleshooting

### Tests Fail with Database Errors

```bash
# Ensure database is healthy
docker-compose ps
docker-compose logs db

# Reset database
docker-compose down -v
docker-compose up -d db
docker-compose run --rm web python manage.py migrate
```

### Tests Fail with Redis Errors

```bash
# Check Redis health
docker-compose exec redis redis-cli ping

# Restart Redis
docker-compose restart redis
```

### Tests Timeout

```bash
# Increase timeout
pytest --timeout=600

# Disable timeout for debugging
pytest --timeout=0
```

### Import Errors

```bash
# Ensure you're in the correct directory
cd myproject

# Verify Python path
python -c "import shopping; print(shopping.__file__)"
```

---

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Self-Healing Tests

on: [push, pull_request]

jobs:
  tier1:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements-dev.txt
      - run: pytest -m "tier1" --timeout=120

  tier2:
    needs: tier1
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_PASSWORD: postgres
      redis:
        image: redis:7
    steps:
      - uses: actions/checkout@v4
      - run: pytest -m "tier2" --timeout=600
```

---

## Related Documents

| Document | Purpose |
|----------|---------|
| `SELF_HEALING_TEST_STRATEGY.md` | High-level strategy and compliance |
| `SELF_HEALING_TEST_SPECIFICATIONS.md` | Detailed test case specs |
| `SELF_HEALING_TEST_MATRICES.md` | Risk matrix and CI rules |
| `L3_SELF_HEALING_ARCHITECTURE.md` | System architecture |

---

## Getting Help

1. **Slack**: #dev-testing channel
2. **Wiki**: Internal testing guidelines
3. **On-call**: DevOps team for CI issues

---

**Document Control**

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2025-12-08 | AI Test Engineer | Initial release |
