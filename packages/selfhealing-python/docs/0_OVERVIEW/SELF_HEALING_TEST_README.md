# L3 Self-Healing Testing Documentation Index

> **Version**: 1.0
> **Created**: 2025-12-09
> **Status**: Active

---

## Overview

This directory contains comprehensive testing documentation for the L3 Self-Healing Reliability Layer. These documents serve as the foundation for test planning, coverage analysis, and gap identification.

---

## Document Index

| Document | Purpose | Audience |
|----------|---------|----------|
| [Test Requirements Specification](./L3_TEST_REQUIREMENTS_SPECIFICATION.md) | Defines all test requirements derived from architecture | QA, Developers |
| [Test Coverage Analysis](./L3_TEST_COVERAGE_ANALYSIS.md) | Analysis of existing test coverage | Tech Lead, QA |
| [Test Gap Report](./L3_TEST_GAP_REPORT.md) | Actionable list of missing tests with specifications | Developers |
| [Test Execution Guide](./L3_TEST_EXECUTION_GUIDE.md) | How to run and validate tests | All Engineers |

---

## Quick Reference

### Current Coverage Summary

| Category | Coverage | Status |
|----------|----------|--------|
| Failure Classification | 90% | ⚠️ Minor gaps |
| Idempotency | 85% | ⚠️ Gap identified |
| Retry Strategy | 80% | ⚠️ Gaps identified |
| DLQ Processing | 95% | ✅ Excellent |
| Circuit Breaker | 80% | ⚠️ Gap identified |
| SLA & Escalation | 85% | ⚠️ Minor gaps |
| Notification | 95% | ✅ Excellent |
| Observability | 90% | ⚠️ Minor gaps |
| Forensic Context | 90% | ⚠️ Minor gaps |
| **Overall** | **88%** | ⚠️ Good with gaps |

### Priority Gaps (Immediate Action)

| Gap ID | Description | Priority |
|--------|-------------|----------|
| G-01 | Retry without idempotency key denied | 🔴 HIGH |
| G-03 | Circuit breaker TTL expiration | 🔴 HIGH |
| G-06 | Retry count persistence across restart | 🔴 HIGH |
| G-07 | Security violation never in DLQ | 🔴 HIGH |

---

## Test Execution Commands

```bash
# Run all self-healing unit tests
pytest shopping/tests/unit/self_healing/ -v

# Run all self-healing integration tests
pytest shopping/tests/integration/self_healing/ -v

# Run with coverage report
pytest shopping/tests/unit/self_healing/ \
    --cov=shopping/services/self_healing \
    --cov-report=html

# Run chaos engineering tests
pytest shopping/tests/integration/self_healing/test_chaos_engineering.py -v -m chaos

# Run specific test file
pytest shopping/tests/integration/self_healing/test_dlq_storage_and_replay.py -v
```

---

## Related Architecture Documents

- [L3 Self-Healing Architecture](../L3_SELF_HEALING_ARCHITECTURE.md)
- [L3 Self-Healing Operations](../L3_SELF_HEALING_OPERATIONS.md)
- [Celery Retry Guide](../../CELERY_RETRY_GUIDE.md)

---

## Contribution Guidelines

When adding new tests:

1. **Follow naming convention**: `test_<category>_<scenario>.py`
2. **Add docstrings**: Include Purpose, Scenario, Expected sections
3. **Reference requirements**: Link to specification document
4. **Use appropriate markers**: `@pytest.mark.tier1`, `@pytest.mark.chaos`, etc.
5. **Update coverage analysis**: Reflect new coverage in documentation
