# ADR-005: Dual Audit Path Strategy

> **Status**: Accepted
> **Date**: 2026-03-07
> **Reference**: `docs/self_healing/middleware_system/312_EXCEPTION_HIERARCHY_LOGGING_STANDARDIZATION.md` Section 2.3

---

## Context

`DLQServiceBase` uses two distinct audit logging paths depending on whether
a Django HTTP request context is available:

1. **Request context present** (`request` argument is not `None`):
   Audit entries are appended to `RequestAuditBuffer`, then flushed by
   `AuditMiddleware` after the response completes (batch write).

2. **No request context** (Celery tasks, management commands, etc.):
   The `AuditLogAdapter` is called directly for immediate write.

## Decision

Maintain both audit paths as-is. No unification.

### Rationale

1. **Batch performance**: Within HTTP request handling, batching audit writes
   until response completion reduces per-request I/O overhead.
2. **Data loss prevention**: In async contexts (Celery, cron), immediate writes
   ensure audit records are persisted even if the process crashes before a
   hypothetical batch flush.
3. **Complexity vs. benefit**: Unifying the two paths would require either
   always-immediate writes (losing batch benefits) or a universal buffer with
   flush guarantees across all execution contexts (high complexity).

## Consequences

- Audit log **timing** differs by context: batch (post-response) vs. immediate.
- Duplicate prevention relies on **idempotency keys** in audit entries.
- Contributors must be aware of both paths when modifying audit behavior in
  `services/dlq/base.py`.
