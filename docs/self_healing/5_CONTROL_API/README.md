# Self-Healing Control API Documentation

> **Version**: 1.0
> **Created**: 2025-12-09
> **Status**: Active — Production-Ready Specification

---

## Overview

The **Self-Healing Control API** provides a unified, auditable, reversible, and governed control surface to manage reliability behaviors across testing, chaos experimentation, and real production operations.

---

## Document Index

| Document | Purpose | Audience |
|----------|---------|----------|
| [CONTROL_API_INTERFACE.md](./CONTROL_API_INTERFACE.md) | API request/response specification | All Engineers |
| [CONTROL_API_SECURITY_GOVERNANCE.md](./CONTROL_API_SECURITY_GOVERNANCE.md) | Authorization, risk classification, audit | Ops, Security |
| [CONTROL_API_EXECUTION.md](./CONTROL_API_EXECUTION.md) | Execution behavior, TTL management | Backend Engineers |
| [CONTROL_API_TEST_REQUIREMENTS.md](./CONTROL_API_TEST_REQUIREMENTS.md) | Test specifications | QA, Developers |

---

## Quick Reference

### API Actions

| Action | Purpose | Ops Allowed |
|--------|---------|-------------|
| `allow` | Enable service operations | ✅ |
| `block` | Block service operations | ✅ |
| `override` | Temporarily bypass rules | ✅ (TTL required) |
| `reset` | Revert to defaults | ✅ |
| `inject_failure` | Simulate failures | ❌ Forbidden |

### Environments

| Environment | Purpose | Restrictions |
|-------------|---------|--------------|
| `test` | CI/CD validation | Minimal |
| `chaos` | Resilience testing | TTL recommended |
| `ops` | Production control | Strict governance |

### Terminology Mapping

| Control API | Circuit Breaker | Effect |
|-------------|-----------------|--------|
| `allow` | `force_close` | Operations proceed (CB CLOSED) |
| `block` | `force_open` | Operations blocked (CB OPEN) |

---

## Key Principles

### Governance

- ✅ **Audited**: Every action recorded with who, what, why, when
- ✅ **TTL-bounded**: Operations expire automatically
- ✅ **Role-authorized**: Based on authority level
- ✅ **Evidence-backed**: Metrics captured as proof
- ✅ **Reversible**: No permanent overrides without review

### Security

- ❌ `inject_failure` forbidden in production
- ✅ TTL required for override in ops (max 60 min)
- ✅ Reason required for all actions
- ✅ CRITICAL risk requires human approval

---

## Request Example

```json
{
  "service_name": "payment",
  "action": "override",
  "environment": "ops",
  "reason": "external-api-latency-breach",
  "ttl_minutes": 45,
  "request_id": "41e7ca58-981a-4f29-b523-abcd"
}
```

## Response Example

```json
{
  "status": "success",
  "action_applied": "override",
  "system_state": "allow",
  "effective_until": "2025-12-09T10:25:00Z",
  "reason_classification": "external-dependency-failure",
  "evidence": {
    "recent_latency_avg_ms": 1840,
    "error_rate": 0.12
  },
  "correlation_id": "abcd-1234-dcba"
}
```

---

## Document Suite Position

```
Self-Healing Documentation
├── 0_OVERVIEW/
│   ├── SELF_HEALING_ARCHITECTURE.md  ← Links to Control API
│   └── SELF_HEALING_OPERATIONS.md    ← Links to Control API
│
├── 5_CONTROL_API/                    ← YOU ARE HERE
│   ├── README.md                     ← This file
│   ├── CONTROL_API_INTERFACE.md      ← What (Contract)
│   ├── CONTROL_API_SECURITY_GOVERNANCE.md ← Who/Why (Authority)
│   ├── CONTROL_API_EXECUTION.md      ← How (Execution)
│   └── CONTROL_API_TEST_REQUIREMENTS.md ← Tests
```

---

## Related Documents

- [Architecture](../0_OVERVIEW/SELF_HEALING_ARCHITECTURE.md)
- [Operations](../0_OVERVIEW/SELF_HEALING_OPERATIONS.md)
- [Test Strategy](../2_STRATEGY/SELF_HEALING_TEST_STRATEGY.md)

---

## Strategic Value

This API is **not a toggle**.

It is a **risk control interface** that:

- ✅ Transfers operational burden to automated systems
- ✅ Enables predictable recovery patterns
- ✅ Allows controlled chaos testing
- ✅ Supports AI-based assistance
- ✅ Provides audit-grade accountability

> **"Fail intentionally, recover predictably."**
