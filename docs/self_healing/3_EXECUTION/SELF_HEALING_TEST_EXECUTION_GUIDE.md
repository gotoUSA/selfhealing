# L3 Self-Healing Test Execution Guide

> **Version**: 1.0
> **Created**: 2025-12-09
> **Purpose**: Step-by-step guide for running and validating self-healing tests

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Test Categories](#2-test-categories)
3. [Running Tests](#3-running-tests)
4. [Test Markers](#4-test-markers)
5. [Coverage Reports](#5-coverage-reports)
6. [Continuous Integration](#6-continuous-integration)
7. [Troubleshooting](#7-troubleshooting)

---

## 1. Prerequisites

### Environment Setup

```bash
# Activate virtual environment
source venv/bin/activate  # Linux/Mac
# or
.\venv\Scripts\activate   # Windows

# Install test dependencies
pip install -r requirements-dev.txt
```

### Required Services

| Service | Purpose | Required For |
|---------|---------|--------------|
| PostgreSQL | Database | All DB tests |
| Redis | Cache/Queue | Idempotency tests |
| Celery | Task queue | Task tests |

### Database Setup

```bash
# Run migrations
python manage.py migrate

# Create test database
python manage.py migrate --database=test
```

---

## 2. Test Categories

### Unit Tests

Location: `shopping/tests/unit/self_healing/`

| File | Tests | Focus |
|------|-------|-------|
| `test_self_healing_policy.py` | ~25 | Backoff, retry handler |
| `test_retry_decision_table.py` | ~20 | Decision logic |
| `test_security_violation_service.py` | 34 | Security handling |
| `test_security_notification_service.py` | 33 | Notifications |
| `test_self_healing_metrics.py` | ~15 | Prometheus metrics |
| `test_sla_timer_policy.py` | ~12 | SLA thresholds |
| `test_circuit_breaker_service.py` | ~15 | CB service |

### Integration Tests

Location: `shopping/tests/integration/self_healing/`

| File | Tests | Focus |
|------|-------|-------|
| `test_dlq_storage_and_replay.py` | ~50 | Full DLQ lifecycle |
| `test_circuit_breaker.py` | ~20 | CB workflow |
| `test_chaos_engineering.py` | ~20 | Fault injection |
| `test_observability_metrics.py` | ~15 | Metrics emission |
| `test_architectural_resilience_e2e.py` | ~25 | E2E flows |
| `test_celery_async_mode.py` | 9 | Celery async behavior simulation |
| `test_redis_failure_scenarios.py` | 13 | Redis failure graceful degradation |
| `test_transaction_task_timing.py` | ~10 | Transaction-task coordination |

### Other Related Tests

| File | Location | Focus |
|------|----------|-------|
| `test_idempotency_key.py` | `integration/` | Payment idempotency |
| `test_celery_idempotency.py` | `unit/` | Celery task idempotency |
| `test_retry_configuration.py` | `tasks/` | Celery retry settings |

---

## 3. Running Tests

### Quick Commands

```bash
# All self-healing tests
pytest shopping/tests/unit/self_healing/ shopping/tests/integration/self_healing/ -v

# Unit tests only
pytest shopping/tests/unit/self_healing/ -v

# Integration tests only
pytest shopping/tests/integration/self_healing/ -v

# Single file
pytest shopping/tests/integration/self_healing/test_dlq_storage_and_replay.py -v

# Single test class
pytest shopping/tests/unit/self_healing/test_security_violation_service.py::TestSecurityViolationServiceIntegration -v

# Single test method
pytest shopping/tests/unit/self_healing/test_self_healing_policy.py::TestBackoffCalculatorUnit::test_calculate_without_jitter_is_deterministic -v
```

### With Coverage

```bash
# Coverage with HTML report
pytest shopping/tests/unit/self_healing/ \
    --cov=shopping/services/self_healing \
    --cov-report=html \
    --cov-report=term-missing

# View coverage report
open htmlcov/index.html  # Mac
start htmlcov/index.html # Windows
```

### Parallel Execution

```bash
# Run in parallel (requires pytest-xdist)
pytest shopping/tests/unit/self_healing/ -n auto -v

# Specify number of workers
pytest shopping/tests/unit/self_healing/ -n 4 -v
```

### Docker Environment

```bash
# Run tests in Docker
docker-compose exec web pytest shopping/tests/unit/self_healing/ -v --no-cov

# With coverage
docker-compose exec web pytest shopping/tests/unit/self_healing/ \
    --cov=shopping/services/self_healing \
    --cov-report=term-missing
```

---

## 4. Test Markers

### Available Markers

| Marker | Purpose | Usage |
|--------|---------|-------|
| `@pytest.mark.tier1` | Critical path tests | `pytest -m tier1` |
| `@pytest.mark.tier2` | Important tests | `pytest -m tier2` |
| `@pytest.mark.chaos` | Chaos engineering | `pytest -m chaos` |
| `@pytest.mark.concurrency` | Concurrency tests | `pytest -m concurrency` |
| `@pytest.mark.django_db` | Database tests | Auto-applied |

### Running by Marker

```bash
# Critical path only
pytest shopping/tests/ -m tier1 -v

# Chaos engineering tests
pytest shopping/tests/ -m chaos -v

# Concurrency tests
pytest shopping/tests/ -m concurrency -v

# Exclude slow tests
pytest shopping/tests/ -m "not slow" -v

# Combine markers
pytest shopping/tests/ -m "tier1 and not slow" -v
```

---

## 5. Coverage Reports

### Generate Coverage

```bash
# Full coverage report
pytest shopping/tests/unit/self_healing/ shopping/tests/integration/self_healing/ \
    --cov=shopping/services/self_healing \
    --cov=shopping/models/failed_operation \
    --cov=shopping/models/security_incident \
    --cov-report=html \
    --cov-report=xml \
    --cov-report=term-missing
```

### Coverage Thresholds

| Component | Target | Current |
|-----------|--------|---------|
| `dlq_service.py` | ≥90% | ~95% |
| `replay_service.py` | ≥90% | ~92% |
| `circuit_breaker_service.py` | ≥85% | ~88% |
| `security_violation_service.py` | ≥90% | ~94% |
| `backoff_calculator.py` | ≥95% | ~98% |
| `retry_handler.py` | ≥90% | ~91% |

### CI Coverage Gate

```yaml
# .github/workflows/test.yml
- name: Check coverage
  run: |
    pytest shopping/tests/unit/self_healing/ \
        --cov=shopping/services/self_healing \
        --cov-fail-under=85
```

---

## 6. Continuous Integration

### GitHub Actions Workflow

```yaml
name: Self-Healing Tests

on:
  push:
    paths:
      - 'shopping/services/self_healing/**'
      - 'shopping/tests/**/test_*self_healing*.py'
      - 'shopping/tests/**/test_*dlq*.py'
      - 'shopping/tests/**/test_*circuit*.py'

jobs:
  test:
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgres:14
        env:
          POSTGRES_PASSWORD: postgres
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install -r requirements-dev.txt

      - name: Run unit tests
        run: |
          pytest shopping/tests/unit/self_healing/ \
              --cov=shopping/services/self_healing \
              --cov-report=xml \
              -v

      - name: Run integration tests
        run: |
          pytest shopping/tests/integration/self_healing/ \
              --cov=shopping/services/self_healing \
              --cov-append \
              --cov-report=xml \
              -v

      - name: Upload coverage
        uses: codecov/codecov-action@v3
        with:
          file: ./coverage.xml
```

### Pre-commit Hook

```bash
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: self-healing-tests
        name: Self-Healing Tests
        entry: pytest shopping/tests/unit/self_healing/ -q --no-cov
        language: system
        pass_filenames: false
        stages: [commit]
```

---

## 7. Troubleshooting

### Common Issues

#### Database Connection Errors

```bash
# Error: connection refused
# Solution: Ensure PostgreSQL is running
docker-compose up -d db

# Or use SQLite for quick tests
export DATABASE_URL=sqlite:///test.db
pytest shopping/tests/unit/self_healing/ -v
```

#### Import Errors

```bash
# Error: ModuleNotFoundError
# Solution: Install in development mode
pip install -e .

# Or set PYTHONPATH
export PYTHONPATH=$PWD:$PYTHONPATH
```

#### Celery Task Errors

```bash
# Error: Task not registered
# Solution: Use eager mode for tests
# In conftest.py:
@pytest.fixture(autouse=True)
def celery_eager_mode(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True
```

#### Slow Tests

```bash
# Skip slow tests
pytest shopping/tests/ -m "not slow" -v

# Use parallel execution
pytest shopping/tests/ -n auto -v

# Profile slow tests
pytest shopping/tests/ --durations=10
```

### Debug Mode

```bash
# Run with verbose output
pytest shopping/tests/unit/self_healing/test_security_violation_service.py -v -s

# Drop into debugger on failure
pytest shopping/tests/ --pdb

# Stop on first failure
pytest shopping/tests/ -x

# Show local variables in tracebacks
pytest shopping/tests/ -l
```

### Fixture Issues

```bash
# List available fixtures
pytest shopping/tests/unit/self_healing/ --fixtures

# Show fixture setup/teardown
pytest shopping/tests/ --setup-show
```

---

## Appendix: Test Configuration

### pytest.ini

```ini
[pytest]
DJANGO_SETTINGS_MODULE = myproject.settings.test
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = --strict-markers -ra
markers =
    tier1: Critical path tests (deselect with '-m "not tier1"')
    tier2: Important but non-critical tests
    chaos: Chaos engineering tests
    concurrency: Concurrency and threading tests
    slow: Slow running tests
filterwarnings =
    ignore::DeprecationWarning
    ignore::PendingDeprecationWarning
```

### conftest.py Fixtures

```python
# shopping/tests/conftest.py

import pytest
from django.test import RequestFactory

@pytest.fixture
def api_client():
    from rest_framework.test import APIClient
    return APIClient()

@pytest.fixture
def category():
    from shopping.tests.factories import CategoryFactory
    return CategoryFactory()

@pytest.fixture
def authenticated_user(api_client):
    from shopping.tests.factories import UserFactory
    user = UserFactory(is_email_verified=True)
    api_client.force_authenticate(user=user)
    return user
```

---

## Related Documents

- [Test Requirements Specification](./L3_TEST_REQUIREMENTS_SPECIFICATION.md)
- [Test Coverage Analysis](./L3_TEST_COVERAGE_ANALYSIS.md)
- [Test Gap Report](./L3_TEST_GAP_REPORT.md)

---

## Appendix: Test Gap Resolutions (Added 2025-12-10)

This section documents test gaps identified in the self-healing system and their resolutions.

### A.1 SELF_HEALING Configuration

**Issue**: `SELF_HEALING` settings were not defined in any settings file, causing the system to rely on implicit default values.

**Resolution**: Added explicit `SELF_HEALING` configuration to:
- `myproject/settings/production.py` - Production-optimized values
- `myproject/settings/local.py` - Development-friendly values
- `myproject/settings/test.py` - Test-optimized values with minimal delays

**Configuration Structure**:
```python
SELF_HEALING = {
    "SLA": {...},            # Service Level Agreement thresholds
    "RETRY": {...},          # Retry policy with exponential backoff
    "CIRCUIT_BREAKER": {...}, # Circuit breaker settings
    "DLQ": {...},            # Dead Letter Queue settings
    "IDEMPOTENCY": {...},    # Idempotency cache TTLs
}
```

### A.2 Celery Eager vs Async Mode Gap

**Issue**: Tests run with `CELERY_TASK_ALWAYS_EAGER=True`, which masks critical differences from production async behavior:

| Aspect | Eager (Test) | Async (Production) |
|--------|--------------|-------------------|
| Execution | Synchronous | Asynchronous via broker |
| Error propagation | Immediate exception | Task-internal only |
| Retry behavior | Often ignored | Actual backoff applied |
| Transaction | Same transaction | Separate connection |

**Resolution**: Created two new test files:

1. **`test_celery_async_mode.py`** - Tests that simulate async behavior:
   - `TestCeleryAsyncBehavior`: Task queuing verification
   - `TestCeleryRetrySimulation`: Retry configuration validation
   - `TestBrokerFailureRecovery`: Broker failure handling

2. **`test_transaction_task_timing.py`** - Transaction-task coordination:
   - `TestTransactionTaskCoordination`: on_commit pattern testing
   - `TestDLQTransactionPatterns`: DLQ-specific patterns
   - `TestEagerVsAsyncDifferences`: Documentation of mode differences

**Key Pattern for Production Safety**:
```python
# CORRECT: Use on_commit to ensure task sees committed data
with transaction.atomic():
    obj = Model.objects.create(...)
    transaction.on_commit(
        lambda: my_task.apply_async(args=[obj.id])
    )

# WRONG: Task may execute before commit in production
with transaction.atomic():
    obj = Model.objects.create(...)
    my_task.delay(obj.id)  # BAD! Race condition in production
```

### A.3 Running New Tests

```bash
# Run Celery async mode tests
pytest shopping/tests/integration/self_healing/test_celery_async_mode.py -v

# Run transaction timing tests
pytest shopping/tests/integration/self_healing/test_transaction_task_timing.py -v

# Run both with coverage
pytest shopping/tests/integration/self_healing/test_celery_async_mode.py \
       shopping/tests/integration/self_healing/test_transaction_task_timing.py \
       --cov=shopping/tasks \
       --cov-report=term-missing -v
```

### A.4 Redis Failure Scenarios (Added 2025-12-10)

**Issue**: Redis is used as both Celery broker and cache backend. Failures in Redis can cascade to critical operations if not handled properly.

**Resolution**: Created `test_redis_failure_scenarios.py` to verify graceful degradation:

1. **`TestCircuitBreakerRedisFailure`** - Circuit Breaker cache resilience:
   - Defaults to CLOSED (available) on cache miss
   - Falls back to database state when cache is unavailable
   - `force_open` persists to DB even when cache fails

2. **`TestIdempotencyServiceRedisFailure`** - Idempotency service gaps:
   - **GAP IDENTIFIED**: Currently raises `RedisConnectionError` instead of fallback
   - Tests document current behavior for future improvement
   - Happy path with working cache verified

3. **`TestDLQServiceRedisFailure`** - DLQ database-first design:
   - Store operations succeed without cache
   - Retrieve works with database-only fallback
   - Pending count uses DB query on cache miss

4. **`TestRateLimitTrackerCacheIndependence`** - Memory-based tracking:
   - Rate limit tracking uses in-memory storage, not Redis
   - Continues working during Redis outages
   - Backoff level management is Redis-independent

5. **`TestCascadePreventionDuringRedisOutage`** - Critical path protection:
   - Payment flow continues during Redis outage (verified)
   - Multiple Redis error types handled gracefully

**Running Redis Failure Tests**:
```bash
# Run Redis failure scenarios
pytest shopping/tests/integration/self_healing/test_redis_failure_scenarios.py -v

# Run with verbose output for debugging
pytest shopping/tests/integration/self_healing/test_redis_failure_scenarios.py -v -s
```

**Key Findings**:

| Component | Redis Failure Handling | Status |
|-----------|----------------------|--------|
| CircuitBreakerService | ✅ Graceful DB fallback | Implemented |
| DLQService | ✅ DB-first design | Implemented |
| RateLimitTracker | ✅ Memory-based, no Redis | Implemented |
| IdempotencyService | 🔴 Exception raised | **Needs Improvement** |
| PaymentService | ✅ Continues operation | Implemented |

**Recommended Improvement for IdempotencyService**:
```python
# Current (problematic):
cached_payment_id = cache.get(key.cache_key)  # Raises on Redis failure

# Recommended (resilient):
try:
    cached_payment_id = cache.get(key.cache_key)
except (RedisConnectionError, RedisTimeoutError):
    logger.warning(f"Cache unavailable, falling back to DB: {key.cache_key}")
    cached_payment_id = None  # Fall through to DB check
```

### A.5 Future Work

The following test gaps are planned for future resolution:

| Priority | Test File | Focus |
|----------|-----------|-------|
| 🟠 Medium | `test_time_based_behaviors.py` | Time-dependent logic with freezegun |
| 🟠 Medium | `test_external_api_failures.py` | External API timeout/failure recovery |
| 🟡 Low | `IdempotencyService` refactor | Add Redis failure graceful degradation |

See `docs/SELF_HEALING_TEST_GAP_ANALYSIS.md` for full details.
