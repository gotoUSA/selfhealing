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
| [CONTROL_API_REFERENCE.md](./CONTROL_API_REFERENCE.md) | Complete REST API reference | All Engineers |
| [CONTROL_API_INTERFACE.md](./CONTROL_API_INTERFACE.md) | Request/response schema specification | All Engineers |
| [CONTROL_API_SECURITY_GOVERNANCE.md](./CONTROL_API_SECURITY_GOVERNANCE.md) | Authorization, risk classification, audit | Ops, Security |
| [CONTROL_API_EXECUTION.md](./CONTROL_API_EXECUTION.md) | Execution behavior, TTL management | Backend Engineers |
| [CONTROL_API_TEST_REQUIREMENTS.md](./CONTROL_API_TEST_REQUIREMENTS.md) | Test specifications | QA, Developers |

> **Interactive Documentation:** Swagger UI (`/api/docs/swagger/`) | ReDoc (`/api/docs/redoc/`)

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

### API Endpoints

| Method | Endpoint | Purpose | Auth |
|--------|----------|---------|------|
| POST | `/api/self-healing/control/` | Execute control action | Admin |
| GET | `/api/self-healing/status/` | Get all services status | Auth |
| GET | `/api/self-healing/status/{service_name}/` | Get specific service status | Auth |
| GET | `/api/self-healing/audit/` | Get audit logs | Admin |
| POST | `/api/self-healing/allow/{service_name}/` | Quick allow (shortcut) | Admin |
| POST | `/api/self-healing/block/{service_name}/` | Quick block (shortcut) | Admin |
| POST | `/api/self-healing/reset/{service_name}/` | Quick reset (shortcut) | Admin |
| GET | `/api/self-healing/health/` | Health check | Public |
| GET | `/api/self-healing/metrics/` | Metrics for monitoring | Auth |
| POST | `/api/self-healing/dlq/replay/` | Trigger DLQ batch replay | Admin |

> **For detailed request/response examples, see [CONTROL_API_REFERENCE.md](./CONTROL_API_REFERENCE.md)**

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
│   ├── README.md                     ← This file (Overview)
│   ├── CONTROL_API_REFERENCE.md      ← REST API Reference
│   ├── CONTROL_API_INTERFACE.md      ← What (Schema Contract)
│   ├── CONTROL_API_SECURITY_GOVERNANCE.md ← Who/Why (Authority)
│   ├── CONTROL_API_EXECUTION.md      ← How (Execution)
│   └── CONTROL_API_TEST_REQUIREMENTS.md ← Tests
```

---

## Related Documents

- [API Reference](./CONTROL_API_REFERENCE.md) — Complete REST API documentation
- [Architecture](../0_OVERVIEW/SELF_HEALING_ARCHITECTURE.md)
- [Operations](../0_OVERVIEW/SELF_HEALING_OPERATIONS.md)
- [Test Strategy](../2_STRATEGY/SELF_HEALING_TEST_STRATEGY.md)

---

## Maintenance Guide

### Document Ownership

| Document | Update When | Owner |
|----------|-------------|-------|
| `README.md` | Overview changes, new endpoints added | All |
| `CONTROL_API_REFERENCE.md` | API behavior changes, new endpoints | Backend |
| `CONTROL_API_INTERFACE.md` | Schema/contract changes | Backend |
| `CONTROL_API_SECURITY_GOVERNANCE.md` | Auth/permission changes | Security/Ops |
| `CONTROL_API_EXECUTION.md` | Execution logic changes | Backend |
| `CONTROL_API_TEST_REQUIREMENTS.md` | Test scope changes | QA |

### Update Workflow

```
┌─────────────────────────────────────────────────────────────────┐
│                    Code Change Workflow                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. Code changes (views, serializers)                            │
│     └─> Update @extend_schema decorators                         │
│         └─> Swagger/ReDoc auto-updated ✓                         │
│                                                                  │
│  2. Schema/field changes                                         │
│     └─> Update CONTROL_API_REFERENCE.md                          │
│     └─> Update CONTROL_API_INTERFACE.md                          │
│                                                                  │
│  3. New endpoint added                                           │
│     └─> Add to README.md API Endpoints table                     │
│     └─> Add section to CONTROL_API_REFERENCE.md                  │
│                                                                  │
│  4. Permission/security changes                                  │
│     └─> Update CONTROL_API_SECURITY_GOVERNANCE.md                │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Single Source of Truth (SSOT)

| Information Type | Source of Truth | Derived Documents |
|------------------|-----------------|-------------------|
| API behavior | Python code (`self_healing_views.py`) | Swagger, REFERENCE.md |
| Schema | Serializers (`self_healing_serializers.py`) | INTERFACE.md |
| Permission rules | Code + SECURITY_GOVERNANCE.md | - |

### Checklist: Before Merging API Changes

- [ ] `@extend_schema` decorators updated
- [ ] `CONTROL_API_REFERENCE.md` endpoint section updated
- [ ] New endpoints added to `README.md` table
- [ ] Schema changes reflected in `CONTROL_API_INTERFACE.md`
- [ ] Permission changes updated in `CONTROL_API_SECURITY_GOVERNANCE.md`
- [ ] Changes verified in Swagger UI (`/api/docs/swagger/`)

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
