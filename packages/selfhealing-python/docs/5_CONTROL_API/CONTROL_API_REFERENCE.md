# Self-Healing Control API Reference

> **Version**: 1.0
> **Created**: 2025-12-09
> **Status**: Active — Production-Ready Specification
> **Part of**: Self-Healing Control API Suite

---

## Table of Contents

1. [Overview](#1-overview)
2. [Authentication & Authorization](#2-authentication--authorization)
3. [API Endpoints](#3-api-endpoints)
   - [Control Action](#31-control-action)
   - [Status Endpoints](#32-status-endpoints)
   - [Audit Logs](#33-audit-logs)
   - [Quick Actions](#34-quick-actions)
   - [Health & Metrics](#35-health--metrics)
   - [DLQ Replay](#36-dlq-replay)
4. [Common Response Codes](#4-common-response-codes)
5. [Related Documents](#5-related-documents)

---

## 1. Overview

This document provides the complete REST API reference for the Self-Healing Control API.

**Base URL:** `/api/self-healing/`

**OpenAPI Documentation:**
- Swagger UI: `/api/docs/swagger/`
- ReDoc: `/api/docs/redoc/`
- OpenAPI Schema: `/api/schema/`

---

## 2. Authentication & Authorization

| Endpoint | Authentication | Authorization |
|----------|----------------|---------------|
| `/health/` | None | Public |
| `/status/`, `/status/{service_name}/` | Bearer Token | Authenticated |
| `/metrics/` | Bearer Token | Authenticated |
| All others | Bearer Token | Admin Only |

**Headers:**
```http
Authorization: Bearer <access_token>
Content-Type: application/json
```

---

## 3. API Endpoints

### 3.1 Control Action

#### POST `/api/self-healing/control/`

Execute a self-healing control action on a service.

**Permission:** Admin Only

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `service_name` | string | ✅ | Target service (e.g., `payment`, `inventory`, `point`) |
| `action` | string | ✅ | Action to execute: `allow`, `block`, `override`, `reset`, `inject_failure` |
| `environment` | string | ✅ | Environment: `test`, `chaos`, `ops` |
| `reason` | string | ✅ | Reason for the action (audit trail) |
| `ttl_minutes` | integer | ⚠️ | TTL in minutes. **Required for `override` in `ops`** |
| `request_id` | string | ❌ | Client-provided request ID for correlation |
| `metadata` | object | ❌ | Additional metadata (e.g., `failure_rate`, `failure_type`) |

**Request Example:**

```json
{
  "service_name": "payment",
  "action": "override",
  "environment": "ops",
  "reason": "PG API latency exceeded SLA threshold",
  "ttl_minutes": 45,
  "request_id": "req-41e7ca58-981a-4f29"
}
```

**Response (200 OK):**

```json
{
  "status": "success",
  "action_applied": "override",
  "system_state": "allow",
  "effective_until": "2025-12-09T10:45:00Z",
  "reason_classification": "external-dependency-failure",
  "evidence": {
    "recent_latency_avg_ms": 1840,
    "error_rate": 0.12
  },
  "correlation_id": "corr-abcd-1234-dcba"
}
```

**Response (403 Forbidden):**

```json
{
  "status": "rejected",
  "error_code": "FORBIDDEN_ACTION",
  "error_message": "inject_failure is forbidden in ops environment",
  "action_requested": "inject_failure",
  "environment": "ops"
}
```

---

### 3.2 Status Endpoints

#### GET `/api/self-healing/status/`

Get the current status of all services.

**Permission:** Authenticated

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `environment` | string | `ops` | Environment context: `test`, `chaos`, `ops` |

**Response (200 OK):**

```json
{
  "environment": "ops",
  "services": [
    {
      "service_name": "payment",
      "state": "allow",
      "failure_count": 2,
      "success_count": 1547,
      "manual_control": false,
      "effective_until": null,
      "last_state_change": "2025-12-09T08:30:00Z"
    },
    {
      "service_name": "inventory",
      "state": "block",
      "failure_count": 45,
      "success_count": 120,
      "manual_control": true,
      "manual_reason": "Scheduled maintenance",
      "effective_until": "2025-12-09T12:00:00Z",
      "last_state_change": "2025-12-09T09:00:00Z"
    }
  ],
  "timestamp": "2025-12-09T09:40:00Z"
}
```

---

#### GET `/api/self-healing/status/{service_name}/`

Get the current status of a specific service.

**Permission:** Authenticated

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `service_name` | string | Target service name |

**Response (200 OK):**

```json
{
  "service_name": "payment",
  "state": "allow",
  "failure_count": 2,
  "success_count": 1547,
  "manual_control": false,
  "effective_until": null,
  "last_state_change": "2025-12-09T08:30:00Z",
  "circuit_breaker": {
    "state": "CLOSED",
    "failure_threshold": 5,
    "recovery_timeout": 30
  }
}
```

---

### 3.3 Audit Logs

#### GET `/api/self-healing/audit/`

Get audit logs for self-healing control actions.

**Permission:** Admin Only

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `service_name` | string | - | Filter by service name |
| `action` | string | - | Filter by action type |
| `environment` | string | - | Filter by environment |
| `page` | integer | 1 | Page number |
| `page_size` | integer | 50 | Items per page (max: 100) |

**Response (200 OK):**

```json
{
  "logs": [
    {
      "id": "audit-001",
      "timestamp": "2025-12-09T09:30:00Z",
      "service_name": "payment",
      "action": "block",
      "environment": "ops",
      "actor": "admin@example.com",
      "actor_role": "admin",
      "reason": "External API unresponsive",
      "ttl_minutes": 60,
      "result": "success",
      "evidence": {
        "error_rate": 0.45,
        "latency_p99": 5200
      }
    }
  ],
  "total_count": 156,
  "page": 1,
  "page_size": 50
}
```

---

### 3.4 Quick Actions

Shortcut endpoints for common operations. These provide a simpler interface for frequent actions.

#### POST `/api/self-healing/allow/{service_name}/`

Quickly enable service operations.

**Permission:** Admin Only

**Request Body (optional):**

```json
{
  "reason": "Service recovered",
  "environment": "ops"
}
```

**Response:** Same as Control Action response.

---

#### POST `/api/self-healing/block/{service_name}/`

Quickly block service operations.

**Permission:** Admin Only

**Request Body (optional):**

```json
{
  "reason": "Emergency maintenance",
  "environment": "ops",
  "ttl_minutes": 60
}
```

**Response:** Same as Control Action response.

---

#### POST `/api/self-healing/reset/{service_name}/`

Reset service to default configuration.

**Permission:** Admin Only

**Request Body (optional):**

```json
{
  "reason": "Restore default behavior",
  "environment": "ops"
}
```

**Response:** Same as Control Action response.

---

### 3.5 Health & Metrics

#### GET `/api/self-healing/health/`

Health check endpoint for the self-healing system.

**Permission:** Public (no authentication required)

**Response (200 OK):**

```json
{
  "status": "healthy",
  "circuit_breaker_enabled": true,
  "services_count": 5,
  "timestamp": "2025-12-09T09:40:00Z"
}
```

**Response (when degraded):**

```json
{
  "status": "degraded",
  "circuit_breaker_enabled": false,
  "services_count": 0,
  "timestamp": "2025-12-09T09:40:00Z"
}
```

---

#### GET `/api/self-healing/metrics/`

Get comprehensive metrics for trend analysis, dashboards, and AI agents.

**Permission:** Authenticated

**Response (200 OK):**

```json
{
  "total_services": 5,
  "healthy_services": 4,
  "degraded_services": 1,
  "last_5m_failure_rate": 0.02,
  "last_5m_request_count": 2340,
  "avg_time_to_recovery": 45.5,
  "auto_allowed_count_24h": 12,
  "auto_blocked_count_24h": 3,
  "total_dlq_pending": 7,
  "dlq_by_service": {
    "payment": 5,
    "inventory": 2
  },
  "services": [
    {
      "service_name": "payment",
      "failure_rate_5m": 0.01,
      "retry_success_rate": 0.95,
      "circuit_state": "CLOSED",
      "dlq_pending": 5
    }
  ],
  "timestamp": "2025-12-09T09:40:00Z",
  "collection_duration_ms": 45
}
```

---

### 3.6 DLQ Replay

#### POST `/api/self-healing/dlq/replay/`

Trigger replay of failed operations from the Dead Letter Queue.

**Permission:** Admin Only

**Request Body:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `domain` | string | - | Filter by domain: `payment`, `point`, `inventory` |
| `service_name` | string | - | Filter by service name |
| `batch_size` | integer | 50 | Max items to replay (max: 200) |
| `status` | string | `pending` | DLQ item status to replay |

**Request Example:**

```json
{
  "domain": "payment",
  "batch_size": 100,
  "status": "pending"
}
```

**Response (200 OK):**

```json
{
  "status": "success",
  "total": 100,
  "success_count": 87,
  "failed_count": 8,
  "skipped_count": 5
}
```

---

## 4. Common Response Codes

| Code | Meaning | When |
|------|---------|------|
| 200 | Success | Action executed successfully |
| 400 | Bad Request | Validation error (missing fields, invalid values) |
| 401 | Unauthorized | Missing or invalid authentication token |
| 403 | Forbidden | Action not allowed (e.g., `inject_failure` in `ops`) |
| 404 | Not Found | Service not found |
| 500 | Internal Error | Unexpected server error |

---

## 5. Related Documents

| Document | Description |
|----------|-------------|
| [README.md](./README.md) | Overview and Quick Reference |
| [CONTROL_API_INTERFACE.md](./CONTROL_API_INTERFACE.md) | Request/Response Schema Details |
| [CONTROL_API_SECURITY_GOVERNANCE.md](./CONTROL_API_SECURITY_GOVERNANCE.md) | Security, Authorization, Audit |
| [CONTROL_API_EXECUTION.md](./CONTROL_API_EXECUTION.md) | Execution Behavior, TTL Management |
| [CONTROL_API_TEST_REQUIREMENTS.md](./CONTROL_API_TEST_REQUIREMENTS.md) | Test Specifications |

---

## Appendix: Endpoint Summary

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
