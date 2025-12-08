"""
Load & Stress Tests for Self-Healing System

This package contains load and stress tests that validate
system behavior under high concurrency and volume.

Test Files:
- test_concurrent_failures.py: Many simultaneous failures
- test_queue_buildup.py: DLQ/retry queue capacity testing
- test_sla_under_load.py: SLA compliance under stress

Reference: docs/testing/SELF_HEALING_TEST_SPECIFICATIONS.md §10
"""
