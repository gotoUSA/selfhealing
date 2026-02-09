"""
Common type definitions for the self-healing system.

This module previously contained early-design types (FailureType, OperationStatus,
RetryContext, MetricsSnapshot) that were superseded by:
- interfaces/repositories.py: FailedOperationStatus, FailedOperationDomain
- services/retry_handler/models.py: RetryConfig, RetryResult
- interfaces/statistics.py: StatusCounts, CircuitBreakerSummary
- services/metrics/definitions.py: Prometheus label-based domain metrics

All dead types removed per 194_DEAD_CODE_REMOVAL_PLAN.md.
"""
