"""
Standard Hook Factories for Core Retry Primitive.

Optional helpers — no hard dependency from core/retry.py.
Provides standardized Audit + Prometheus hooks to eliminate
metric/logging fragmentation across retry consumers.

Usage:
    from selfhealing.core.retry import retry_with_backoff, RetryConfig
    from selfhealing.core.retry_hooks import make_standard_on_retry, make_standard_on_exhausted

    config = RetryConfig(
        max_retries=5,
        context_name="retry_handler",
        on_retry=make_standard_on_retry("payment"),
        on_exhausted=make_standard_on_exhausted("payment"),
    )
    outcome = retry_with_backoff(func, config, *args)
"""

from __future__ import annotations

from collections.abc import Callable

from selfhealing.core.retry import RetryContext


def make_standard_on_retry(
    audit_domain: str,
) -> Callable[[RetryContext, Exception], None]:
    """AuditHook + MetricsHook combined standard on_retry factory."""

    def _on_retry(ctx: RetryContext, exc: Exception) -> None:
        # 1. Audit logging (Fail-Open)
        try:
            from selfhealing.services.audit.retry_audit import log_retry_audit

            log_retry_audit(
                domain=audit_domain,
                attempt=ctx.attempt,
                max_attempts=ctx.attempt + 1,
                success=False,
                wait_time=ctx.wait_time,
            )
        except Exception:
            pass

        # 2. Prometheus metrics (Fail-Open)
        try:
            from selfhealing.services.metrics.definitions import (
                retry_attempts_histogram,
            )

            retry_attempts_histogram.labels(
                domain=audit_domain,
                **ctx.metric_labels,
            ).observe(ctx.attempt + 1)
        except Exception:
            pass

    return _on_retry


def make_standard_on_exhausted(
    audit_domain: str,
) -> Callable[[RetryContext, Exception], None]:
    """Standard factory for final failure audit + metrics recording."""

    def _on_exhausted(ctx: RetryContext, exc: Exception) -> None:
        try:
            from selfhealing.services.audit.retry_audit import log_retry_audit

            log_retry_audit(
                domain=audit_domain,
                attempt=ctx.attempt,
                max_attempts=ctx.attempt + 1,
                success=False,
                error_type=type(exc).__name__,
                error_message=str(exc)[:500],
            )
        except Exception:
            pass

    return _on_exhausted
