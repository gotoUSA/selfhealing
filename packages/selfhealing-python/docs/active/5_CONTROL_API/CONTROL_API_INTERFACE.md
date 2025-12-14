# Self-Healing Control API — Interface Specification

> **Version**: 1.0
> **Created**: 2025-12-09
> **Status**: Active — Production-Ready Specification
> **Part of**: Self-Healing Control API Suite

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture and Concept](#2-architecture-and-concept)
3. [API Categories](#3-api-categories)
4. [Request Model](#4-request-model)
5. [Action Definitions](#5-action-definitions)
6. [Environment Types](#6-environment-types)
7. [Response Models](#7-response-models)
8. [Request Examples](#8-request-examples)
9. [Validation Rules](#9-validation-rules)
10. [Error Codes](#10-error-codes)
11. [Schema Expansion](#11-schema-expansion)
12. [Related Documents](#12-related-documents)

---

## 1. Overview

### Purpose

The Self-Healing Control API provides a **single, auditable, reversible, and governed control surface** to manage reliability behaviors across testing, chaos experimentation, and real production operations.

This API enables:

- ✅ Controlled fault simulation
- ✅ Policy-driven overrides
- ✅ Governed operational interventions
- ✅ Transparent audit and classification of reliability events
- ✅ AI-assisted interpretation, retry, replay, and remediation

### Goal

> "To reduce operational risk, prevent cascading failures, and deliver resilient self-healing behaviors with auditable human or automated decision-making."

### Scope

**Features Included:**

- API-driven reliability control (allow/block/override/inject)
- Environment-based behavioral constraints
- TTL-based expirable interventions
- Audit, evidence, and classification metadata
- JSON request/response schema
- Governance and policy enforcement rules

**Out of Scope:**

- Pricing models
- Legal contract templates
- SLA financial compensation definitions
- Customer-specific policy override workflows

---

## 2. Architecture and Concept

### Self-Healing Layer Components

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         Self-Healing Layer                                │
├──────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐      │
│  │   Control API   │    │   Monitoring    │    │  AI Feedback    │      │
│  │  (this document)│    │ (metrics, logs) │    │     Loop        │      │
│  └────────┬────────┘    └────────┬────────┘    └────────┬────────┘      │
│           │                      │                      │               │
│           └──────────────────────┴──────────────────────┘               │
│                                  │                                       │
│           ┌──────────────────────┴──────────────────────┐               │
│           │              Governance Layer               │               │
│           │        (Policy Enforcement Rules)           │               │
│           └──────────────────────┴──────────────────────┘               │
│                                  │                                       │
│           ┌──────────────────────┴──────────────────────┐               │
│           │         Retry/Replay/DLQ Orchestration      │               │
│           └─────────────────────────────────────────────┘               │
└──────────────────────────────────────────────────────────────────────────┘
```

### Design Principle

The entire layer is **stateless in interface, stateful in governance**, meaning:

- Policies may evolve without API-breaking changes
- State is managed by the underlying services
- API consumers only need to understand the interface

---

## 3. API Categories

### Category Overview

| Category | Environment | Purpose | Restrictions |
|----------|-------------|---------|--------------|
| **Test API** | `test` (local/CI) | Automatic validation | Minimal restrictions |
| **Chaos API** | `chaos` (staging) | Resilience evaluation | TTL required for inject |
| **Ops API** | `ops` (production) | Governed interventions | `inject_failure` **forbidden** |

### Category-Specific Rules

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          API Access Matrix                               │
├─────────────────────────────────────────────────────────────────────────┤
│  Action           │  test   │  chaos  │  ops    │  Notes                │
├───────────────────┼─────────┼─────────┼─────────┼───────────────────────┤
│  allow            │   ✅    │   ✅    │   ✅    │  Enable operations    │
│  block            │   ✅    │   ✅    │   ✅    │  Disable operations   │
│  override         │   ✅    │   ✅    │   ✅*   │  *TTL required in ops │
│  reset            │   ✅    │   ✅    │   ✅    │  Revert to defaults   │
│  inject_failure   │   ✅    │   ✅    │   ❌    │  Forbidden in ops     │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Request Model

### Field Definitions

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `service_name` | string | **Yes** | Target service or module (e.g., `payment`, `inventory`) |
| `action` | enum | **Yes** | `allow`, `block`, `override`, `reset`, `inject_failure` |
| `reason` | string | **Yes** | Business or technical justification (required for all) |
| `environment` | enum | **Yes** | `test`, `chaos`, `ops` |
| `ttl_minutes` | int | Conditional | Required for `override` in `ops`, optional elsewhere |
| `request_id` | uuid | Optional | Correlates retries & auditing |
| `metadata` | object | Optional | Chaos/test parameters (e.g., `simulate_latency_ms`) |

### Auto-Generated Fields (Response)

| Field | Type | Description |
|-------|------|-------------|
| `reason_classification` | string | AI/system assigned classification |
| `correlation_id` | uuid | AI-assisted correlation for retry/replay |
| `evidence` | object | Short measurable proof (metrics, error rates) |

### TTL Rules

| Environment | Action | TTL Requirement |
|-------------|--------|-----------------|
| `test` | Any | Optional |
| `chaos` | `inject_failure` | Recommended |
| `chaos` | `override` | Recommended |
| `ops` | `override` | **Required** (max 60 min) |
| `ops` | `block` | Optional (default: 90 min) |

---

## 5. Action Definitions

### Terminology Mapping

> **Important**: This API uses `allow`/`block` instead of Circuit Breaker terminology to avoid confusion.

| Control API Action | Circuit Breaker Equivalent | Effect |
|--------------------|---------------------------|--------|
| `allow` | `force_close` (CB Closed) | Operations proceed normally |
| `block` | `force_open` (CB Open) | Operations are blocked |
| `override` | Bypass with governance | Temporarily bypass rules |
| `reset` | Return to default | Revert to base ruleset |
| `inject_failure` | Fault injection | Controlled failure simulation |

### Action Details

#### `allow`
- **Purpose**: Enable normal operations for a service
- **Effect**: Circuit Breaker → CLOSED state, requests pass through
- **Use Case**: Recovery from outage, enable after maintenance

#### `block`
- **Purpose**: Block all operations for a service
- **Effect**: Circuit Breaker → OPEN state, requests fail-fast
- **Use Case**: Known outage, maintenance window, protect downstream

#### `override`
- **Purpose**: Temporarily bypass normal rules under governance
- **Effect**: Specific policies suspended for TTL duration
- **Use Case**: Emergency intervention, SLA breach mitigation
- **Governance**: Requires TTL, reason, and evidence in `ops`

#### `reset`
- **Purpose**: Revert service to default configuration
- **Effect**: All manual overrides cleared, return to policy defaults
- **Use Case**: After incident resolution, configuration cleanup

#### `inject_failure`
- **Purpose**: Simulate failures for testing resilience
- **Effect**: Controlled failures at specified rate/pattern
- **Use Case**: Chaos engineering, load testing
- **Restriction**: **Forbidden in `ops` environment**

---

## 6. Environment Types

### Environment Definitions

#### `test` - Development/CI Environment

```yaml
Purpose: Automatic validation in local/CI pipelines
Restrictions: Minimal
TTL: Optional
Authentication: Internal token
Approval: Not required
```

**Allowed Operations:**
- All actions permitted
- Unlimited `inject_failure`
- No TTL enforcement

#### `chaos` - Staging Environment

```yaml
Purpose: Resilience evaluation and chaos experiments
Restrictions: TTL recommended for destructive actions
TTL: Required for inject_failure, recommended for override
Authentication: Token + Role claim
Approval: Conditional (for extended TTL)
```

**Allowed Operations:**
- All actions permitted
- `inject_failure` with TTL
- Extended chaos experiments require approval

#### `ops` - Production Environment

```yaml
Purpose: Governed operational interventions
Restrictions: Strict governance
TTL: Required for override (max 60 min)
Authentication: Auth + Role + Reason + TTL
Approval: Mandatory for CRITICAL risk level
```

**Restrictions:**
- ❌ `inject_failure` is **forbidden**
- ✅ `override` requires TTL + reason
- ✅ All actions require `reason`

---

## 7. Response Models

### Success Response

```json
{
  "status": "success",
  "action_applied": "override",
  "system_state": "allow",
  "effective_until": "2025-12-09T10:25:00Z",
  "reason_received": "external-api-latency-breach",
  "reason_classification": "external-dependency-failure",
  "evidence": {
    "recent_latency_avg_ms": 1840,
    "error_rate": 0.12,
    "dlq_pending_count": 15
  },
  "correlation_id": "abcd-1234-efgh-5678",
  "audit_id": "audit-2025-12-09-001"
}
```

### Response Field Definitions

| Field | Type | Description |
|-------|------|-------------|
| `status` | enum | `success`, `rejected`, `pending_approval` |
| `action_applied` | string | The action that was executed |
| `system_state` | enum | Current system state: `allow`, `block`, `override` |
| `effective_until` | ISO8601 | When the action expires (for TTL actions) |
| `reason_received` | string | Echo of the provided reason |
| `reason_classification` | string | AI/system classification of the reason |
| `evidence` | object | Metrics supporting the decision |
| `correlation_id` | uuid | For tracking related events |
| `audit_id` | string | Reference to audit log entry |

### Rejection Response

```json
{
  "status": "rejected",
  "error_code": "TTL_EXCEEDS_POLICY_LIMIT",
  "error_message": "Ops override TTL must not exceed 60 minutes.",
  "retry_available": false,
  "action_applied": "none",
  "next_action_suggestion": "Reduce TTL to 60 minutes or less, or request approval for extended duration.",
  "validation_errors": [
    {
      "field": "ttl_minutes",
      "value": 120,
      "constraint": "max: 60",
      "message": "TTL exceeds maximum allowed for ops environment"
    }
  ]
}
```

### Pending Approval Response

```json
{
  "status": "pending_approval",
  "request_id": "req-2025-12-09-001",
  "approvers": ["ops-commander@company.com"],
  "expires_at": "2025-12-09T11:00:00Z",
  "reason": "CRITICAL risk level requires human approval",
  "action_requested": "override",
  "estimated_review_time": "15 minutes"
}
```

---

## 8. Request Examples

### Test API Example

```json
{
  "service_name": "payment",
  "action": "allow",
  "environment": "test",
  "reason": "ci-fallback-test",
  "metadata": {
    "simulate_latency_ms": 200
  }
}
```

**Response:**
```json
{
  "status": "success",
  "action_applied": "allow",
  "system_state": "allow",
  "reason_received": "ci-fallback-test",
  "correlation_id": "test-12345"
}
```

### Chaos API Example

```json
{
  "service_name": "inventory",
  "action": "inject_failure",
  "environment": "chaos",
  "reason": "chaos-experiment-stock-update",
  "ttl_minutes": 10,
  "metadata": {
    "failure_rate": 0.3,
    "failure_type": "timeout",
    "affected_operations": ["stock_deduct", "stock_restore"]
  }
}
```

**Response:**
```json
{
  "status": "success",
  "action_applied": "inject_failure",
  "system_state": "chaos_active",
  "effective_until": "2025-12-09T10:40:00Z",
  "reason_received": "chaos-experiment-stock-update",
  "reason_classification": "chaos-experiment",
  "correlation_id": "chaos-67890"
}
```

### Ops API Example — Override Approved

```json
{
  "service_name": "external_gateway",
  "action": "override",
  "environment": "ops",
  "ttl_minutes": 45,
  "reason": "external-api-latency-breach",
  "request_id": "41e7ca58-981a-4f29-b523-abcd"
}
```

**Response:**
```json
{
  "status": "success",
  "action_applied": "override",
  "system_state": "allow",
  "effective_until": "2025-12-09T10:25:00Z",
  "reason_received": "external-api-latency-breach",
  "reason_classification": "external-dependency-failure",
  "evidence": {
    "recent_latency_avg_ms": 1840,
    "error_rate": 0.12
  },
  "correlation_id": "abcd-1234-dcba",
  "audit_id": "audit-ops-2025-12-09-001"
}
```

### Ops API Example — Rejected

```json
{
  "service_name": "payment",
  "action": "inject_failure",
  "environment": "ops",
  "reason": "test-failure-injection",
  "ttl_minutes": 5
}
```

**Response:**
```json
{
  "status": "rejected",
  "error_code": "ACTION_FORBIDDEN_IN_ENVIRONMENT",
  "error_message": "inject_failure action is forbidden in ops environment.",
  "retry_available": false,
  "action_applied": "none",
  "next_action_suggestion": "Use chaos environment for failure injection testing."
}
```

---

## 9. Validation Rules

### Required Field Validation

| Rule | Condition | Error Code |
|------|-----------|------------|
| `service_name` required | Missing or empty | `MISSING_REQUIRED_FIELD` |
| `action` required | Missing or invalid enum | `INVALID_ACTION` |
| `reason` required | Missing or empty | `MISSING_REASON` |
| `environment` required | Missing or invalid enum | `INVALID_ENVIRONMENT` |

### Environment-Specific Validation

| Rule | Condition | Error Code |
|------|-----------|------------|
| TTL required for ops override | `action=override` && `environment=ops` && !ttl | `TTL_REQUIRED_FOR_OPS_OVERRIDE` |
| TTL max 60 min in ops | `environment=ops` && `ttl_minutes > 60` | `TTL_EXCEEDS_POLICY_LIMIT` |
| inject_failure forbidden in ops | `action=inject_failure` && `environment=ops` | `ACTION_FORBIDDEN_IN_ENVIRONMENT` |

### Metadata Validation

| Rule | Condition | Error Code |
|------|-----------|------------|
| No nested objects | `metadata` contains nested objects | `METADATA_NESTING_FORBIDDEN` |
| No binary payloads | `metadata` contains binary data | `METADATA_BINARY_FORBIDDEN` |
| Max size 10KB | `metadata` size > 10KB | `METADATA_TOO_LARGE` |

### Disallowed Combinations

```yaml
Forbidden:
  - override without TTL in ops
  - inject_failure in ops
  - chaos metadata in ops environment
  - empty reason for any action
```

---

## 10. Error Codes

### Validation Errors

| Error Code | HTTP Status | Description |
|------------|-------------|-------------|
| `MISSING_REQUIRED_FIELD` | 400 | Required field is missing |
| `INVALID_ACTION` | 400 | Action is not a valid enum value |
| `INVALID_ENVIRONMENT` | 400 | Environment is not valid |
| `MISSING_REASON` | 400 | Reason is required for all actions |
| `TTL_REQUIRED_FOR_OPS_OVERRIDE` | 400 | Override in ops requires TTL |
| `TTL_EXCEEDS_POLICY_LIMIT` | 400 | TTL exceeds maximum allowed |
| `ACTION_FORBIDDEN_IN_ENVIRONMENT` | 403 | Action not allowed in this environment |

### Authorization Errors

| Error Code | HTTP Status | Description |
|------------|-------------|-------------|
| `UNAUTHORIZED` | 401 | Authentication required |
| `FORBIDDEN` | 403 | Insufficient permissions |
| `ROLE_REQUIRED` | 403 | Specific role required for action |
| `APPROVAL_REQUIRED` | 403 | Action requires approval |

### System Errors

| Error Code | HTTP Status | Description |
|------------|-------------|-------------|
| `SERVICE_NOT_FOUND` | 404 | Target service not found |
| `CONFLICT` | 409 | Conflicting operation in progress |
| `INTERNAL_ERROR` | 500 | Internal system error |

---

## 11. Schema Expansion

### Backward Compatibility

Future extensions are **additive** — backward compatible:

- New optional fields may be added
- New action types may be added
- New environments may be added
- Existing fields will not be removed or renamed

### Planned Extensions

| Extension | Type | Status |
|-----------|------|--------|
| JSON Schema | Formal schema | Planned |
| OpenAPI 3.x | API specification | Planned |
| Postman Collections | Testing | Planned |
| AsyncAPI Event Control | Event-driven | Future |

### Schema Version Header

```http
X-Control-API-Version: 1.0
```

---

## 12. Supplementary APIs

### DLQ Replay API

In addition to the 5 core Control API actions, a supplementary API is provided for DLQ (Dead Letter Queue) batch reprocessing.

#### Endpoint

```
POST /api/self-healing/dlq/replay/
```

#### Purpose

- Batch reprocessing of failed messages in the DLQ
- Filtering by domain/service supported
- Configurable batch size and max retry count

#### Request Model

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `domain` | string | No | - | Domain filter (e.g., `payment`, `order`) |
| `service_name` | string | No | - | Service filter |
| `batch_size` | integer | No | 100 | Number of messages to process at once |
| `max_retries` | integer | No | 3 | Maximum retry attempts |

#### Request Example

```json
{
  "domain": "payment",
  "batch_size": 50,
  "max_retries": 2
}
```

#### Response Model

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Whether the request was processed successfully |
| `processed` | integer | Number of messages processed |
| `failed` | integer | Number of messages that failed |
| `message` | string | Result message |

#### Response Example

**Success (200)**
```json
{
  "success": true,
  "processed": 45,
  "failed": 5,
  "message": "DLQ replay completed: 45 processed, 5 failed"
}
```

**Error (500)**
```json
{
  "success": false,
  "error": "DLQ replay failed: Connection timeout"
}
```

#### Access Control

- Available in all environments (test, chaos, ops)
- Audit log is recorded in `ops` environment
- Authentication required (session or API token)

---

## 13. Related Documents

| Document | Purpose | Location |
|----------|---------|----------|
| Security Governance | Who can do what, risk classification | [CONTROL_API_SECURITY_GOVERNANCE.md](./CONTROL_API_SECURITY_GOVERNANCE.md) |
| Execution Behavior | How actions execute, TTL lifecycle | [CONTROL_API_EXECUTION.md](./CONTROL_API_EXECUTION.md) |
| Architecture | Overall self-healing architecture | [../0_OVERVIEW/SELF_HEALING_ARCHITECTURE.md](../0_OVERVIEW/SELF_HEALING_ARCHITECTURE.md) |
| Operations | DLQ, Replay, Metrics | [../0_OVERVIEW/SELF_HEALING_OPERATIONS.md](../0_OVERVIEW/SELF_HEALING_OPERATIONS.md) |

---

## Strategic Value Statement

This API is **not a toggle**.

It is a **risk control interface** that:

- ✅ Transfers operational burden to automated systems
- ✅ Enables predictable recovery patterns
- ✅ Allows controlled chaos testing
- ✅ Supports AI-based assistance
- ✅ Provides audit-grade accountability

> **"Fail intentionally, recover predictably."**

---

*This document defines the API interface. For security and authorization, see [Security Governance](./CONTROL_API_SECURITY_GOVERNANCE.md). For execution behavior, see [Execution](./CONTROL_API_EXECUTION.md).*
