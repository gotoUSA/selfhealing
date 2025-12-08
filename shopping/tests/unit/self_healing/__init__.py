# Self-Healing Unit Tests
# Reference: docs/testing/SELF_HEALING_TEST_STRATEGY.md

"""
Self-healing unit tests for policy-based recovery logic.

This package contains fast, isolated unit tests for:
- Backoff policy calculations
- SLA timer policy decisions
- Retry decision table validation
- Failure classification logic
- Audit record schema validation

All tests in this package are marked with 'tier1' for fast CI execution.
"""
